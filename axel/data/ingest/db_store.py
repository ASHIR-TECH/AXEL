"""
TimescaleDB-backed canonical store.

Implements the same duck-typed contract as :class:`CanonicalStore` (``put`` /
``has`` / ``get`` / ``all`` / ``count`` / iteration) so the pipeline can be
pointed at Postgres without touching any call site -- ``IngestionPipeline`` only
ever calls those methods.

Idempotency is enforced by the database, not by application memory: the logical
key is a PRIMARY KEY, so ``put`` uses ``ON CONFLICT DO NOTHING`` and reports
whether the row was inserted. That is what makes the store safe for concurrent
ingestion, which an in-process dict cannot be.

Point-in-time fields (``event_time`` / ``available_at``) are stored as real
timestamptz columns and indexed, so a "what was knowable at T" query is a single
SQL predicate instead of an application scan.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from axel.data.schemas import (
    BarRecord,
    CanonicalRecord,
    FilingRecord,
    MacroObservation,
    NewsRecord,
    record_from_dict,
    record_key,
    record_to_dict,
)

TABLE = "canonical_records"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    record_key      TEXT PRIMARY KEY,
    record_type     TEXT        NOT NULL,
    entity          TEXT        NOT NULL,
    event_time      TIMESTAMPTZ NOT NULL,
    available_at    TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version  TEXT        NOT NULL,
    source          TEXT        NOT NULL,
    quality_flags   TEXT[]      NOT NULL DEFAULT '{{}}',
    payload         JSONB       NOT NULL,
    CONSTRAINT canonical_available_not_before_event
        CHECK (available_at >= event_time)
);
CREATE INDEX IF NOT EXISTS canonical_pit_idx
    ON {TABLE} (entity, available_at);
CREATE INDEX IF NOT EXISTS canonical_type_pit_idx
    ON {TABLE} (record_type, available_at);
"""


def entity_of(record: CanonicalRecord) -> str:
    """The instrument/topic a record is about, for indexed lookups."""
    if isinstance(record, BarRecord):
        return record.symbol
    if isinstance(record, MacroObservation):
        return record.series_id
    if isinstance(record, FilingRecord):
        return record.issuer_id
    if isinstance(record, NewsRecord):
        return record.article_id
    return record.record_key()


def _as_jsonable(record: CanonicalRecord) -> dict[str, Any]:
    return record_to_dict(record)


class TimescaleStore:
    """
    Canonical records in TimescaleDB/Postgres with DB-enforced uniqueness.

    ``session_factory`` is a zero-arg callable returning a SQLAlchemy Session, so
    this module never owns connection lifecycle -- the caller's engine, pooling
    and transaction boundaries stay in charge. That also keeps the store usable
    from both the backfill script and a request handler without branching.
    """

    def __init__(
        self,
        session_factory: Any,
        *,
        table: str = TABLE,
        ensure_schema: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._table = table
        self._ensured = False
        if ensure_schema:
            self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create the table and indexes if absent (idempotent)."""
        from sqlalchemy import text

        with self._session_factory() as session:
            for statement in CREATE_TABLE_SQL.split(";"):
                if statement.strip():
                    session.execute(text(statement))
            session.commit()
        self._ensured = True

    def put(self, record: CanonicalRecord) -> bool:
        """Insert unless the logical key exists; True when newly inserted."""
        from sqlalchemy import text

        key = record_key(record)
        payload = json.dumps(_as_jsonable(record), default=str)
        statement = text(
            f"""
            INSERT INTO {self._table} (
                record_key, record_type, entity, event_time, available_at,
                schema_version, source, quality_flags, payload
            ) VALUES (
                :key, :record_type, :entity, :event_time, :available_at,
                :schema_version, :source, :quality_flags, CAST(:payload AS JSONB)
            )
            ON CONFLICT (record_key) DO NOTHING
            RETURNING record_key
            """
        )
        params = {
            "key": key,
            "record_type": record.record_type,
            "entity": entity_of(record),
            "event_time": record.event_time,
            "available_at": record.available_at,
            "schema_version": record.provenance.schema_version,
            "source": record.provenance.source,
            "quality_flags": list(record.provenance.quality_flags),
            "payload": payload,
        }
        with self._session_factory() as session:
            inserted = session.execute(statement, params).scalar_one_or_none()
            session.commit()
        return inserted is not None

    def put_many(self, records: Sequence[CanonicalRecord]) -> tuple[int, int]:
        """Bulk insert; returns ``(inserted, duplicates)``."""
        inserted = 0
        for record in records:
            if self.put(record):
                inserted += 1
        return inserted, len(records) - inserted

    def has(self, record: CanonicalRecord) -> bool:
        from sqlalchemy import text

        with self._session_factory() as session:
            found = session.execute(
                text(f"SELECT 1 FROM {self._table} WHERE record_key = :key"),
                {"key": record_key(record)},
            ).scalar_one_or_none()
            session.rollback()
        return found is not None

    def get(self, record: CanonicalRecord) -> CanonicalRecord | None:
        from sqlalchemy import text

        with self._session_factory() as session:
            row = session.execute(
                text(
                    f"SELECT payload FROM {self._table} WHERE record_key = :key"
                ),
                {"key": record_key(record)},
            ).scalar_one_or_none()
            session.rollback()
        return record_from_dict(row) if row is not None else None

    def all(self, record_type: str | None = None) -> list[CanonicalRecord]:
        from sqlalchemy import text

        query = f"SELECT payload FROM {self._table}"
        params: dict[str, Any] = {}
        if record_type is not None:
            query += " WHERE record_type = :record_type"
            params["record_type"] = record_type
        query += " ORDER BY event_time, record_key"
        with self._session_factory() as session:
            rows = session.execute(text(query), params).scalars().all()
            session.rollback()
        return [record_from_dict(row) for row in rows]

    def count(self, record_type: str | None = None) -> int:
        from sqlalchemy import text

        query = f"SELECT count(*) FROM {self._table}"
        params: dict[str, Any] = {}
        if record_type is not None:
            query += " WHERE record_type = :record_type"
            params["record_type"] = record_type
        with self._session_factory() as session:
            total = session.execute(text(query), params).scalar_one()
            session.rollback()
        return int(total)

    def available_at(self, as_of: datetime, record_type: str | None = None) -> list[CanonicalRecord]:
        """Point-in-time query: exactly what was knowable at ``as_of``."""
        from sqlalchemy import text

        query = (
            f"SELECT payload FROM {self._table} WHERE available_at <= :as_of"
        )
        params: dict[str, Any] = {"as_of": as_of}
        if record_type is not None:
            query += " AND record_type = :record_type"
            params["record_type"] = record_type
        query += " ORDER BY available_at, record_key"
        with self._session_factory() as session:
            rows = session.execute(text(query), params).scalars().all()
            session.rollback()
        return [record_from_dict(row) for row in rows]

    def lookahead_check(self) -> int:
        """
        Count records the DB believes are unavailable at ``as_of``.

        Always 0 given the CHECK constraint; exposed so a backfill can assert the
        point-in-time invariant holds in storage, not just in code.
        """
        from sqlalchemy import text

        with self._session_factory() as session:
            offenders = session.execute(
                text(
                    f"SELECT count(*) FROM {self._table} WHERE available_at < event_time"
                )
            ).scalar_one()
            session.rollback()
        return int(offenders)

    def __len__(self) -> int:
        return self.count()

    def __iter__(self) -> Iterator[CanonicalRecord]:
        return iter(self.all())


def session_factory_from_url(url: str) -> Any:
    """Convenience builder: a zero-arg session factory over a SQLAlchemy URL."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(url, pool_pre_ping=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


__all__ = [
    "CREATE_TABLE_SQL",
    "TABLE",
    "TimescaleStore",
    "entity_of",
    "session_factory_from_url",
]