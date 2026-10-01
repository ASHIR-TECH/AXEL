"""
Canonical, versioned data records for Phase 2.

Provider-native payloads are converted into these immutable, provenance-carrying
records at the ingestion boundary. Every record carries enough information
(source, source hash, event time, availability time, schema/normalization
version) to reconstruct and audit itself, and to be joined point-in-time.

Phase 2 architectural rule: this module has NO execution, broker or LLM imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any

SCHEMA_VERSION = "canonical-1"
NORMALIZATION_VERSION = "norm-1"

# How a bar's prices relate to the raw feed. Recorded on the record rather than
# inferred, so a consumer can never mistake an adjusted series for a raw one.
ADJUSTMENT_STATUSES = frozenset(
    {
        "raw",
        "split_adjusted",
        "dividend_adjusted",
        "split_and_dividend_adjusted",
        "adjusted",
    }
)


def sha256_text(text: str) -> str:
    """Stable content hash used for raw payloads and per-record provenance."""
    return sha256(text.encode("utf-8")).hexdigest()


def require_tz(value: datetime, name: str) -> datetime:
    if value is None or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def check_available(event_time: datetime, available_at: datetime) -> None:
    """Enforce point-in-time ordering: a fact cannot be known before it happened."""
    require_tz(event_time, "event_time")
    require_tz(available_at, "available_at")
    if available_at < event_time:
        raise ValueError("available_at cannot precede event_time (look-ahead)")


def parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'."""
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class Provenance:
    """Reconstruction metadata attached to every canonical record."""

    source: str
    source_id: str
    source_hash: str
    ingestion_timestamp: datetime
    schema_version: str = SCHEMA_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("provenance requires a source name")
        if not self.source_hash:
            raise ValueError("provenance requires a source hash")
        require_tz(self.ingestion_timestamp, "ingestion_timestamp")

    def with_flags(self, *flags: str) -> Provenance:
        if not flags:
            return self
        merged = tuple(dict.fromkeys((*self.quality_flags, *flags)))
        return Provenance(
            source=self.source,
            source_id=self.source_id,
            source_hash=self.source_hash,
            ingestion_timestamp=self.ingestion_timestamp,
            schema_version=self.schema_version,
            normalization_version=self.normalization_version,
            quality_flags=merged,
        )


@dataclass(frozen=True)
class RawPayload:
    """An immutable, content-addressed provider response retained for replay/audit."""

    source: str
    request: str
    fetched_at: datetime
    body: str
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("raw payload requires a source name")
        require_tz(self.fetched_at, "fetched_at")

    @property
    def content_hash(self) -> str:
        return sha256_text(self.body)

    def context_dict(self) -> dict[str, str]:
        return dict(self.context)


@dataclass(frozen=True)
class BarRecord:
    """Canonical OHLCV bar with explicit adjustment status and availability."""

    symbol: str
    venue: str
    event_time: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjustment_status: str
    provenance: Provenance
    record_type: str = field(default="bar", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.symbol or not self.venue:
            raise ValueError("bar requires a symbol and venue")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.low > self.high:
            raise ValueError("bar prices must be positive and low <= high")
        if self.volume < 0:
            raise ValueError("bar volume cannot be negative")
        if self.adjustment_status not in ADJUSTMENT_STATUSES:
            raise ValueError(
                f"invalid adjustment_status: {self.adjustment_status!r}; "
                f"expected one of {sorted(ADJUSTMENT_STATUSES)}"
            )

    def key(self) -> str:
        return f"bar:{self.venue}:{self.symbol}:{self.event_time.isoformat()}"


@dataclass(frozen=True)
class MacroObservation:
    """A point-in-time macro observation (FRED vintage aware)."""

    series_id: str
    event_time: datetime
    available_at: datetime
    value: float
    vintage: str
    provenance: Provenance
    record_type: str = field(default="macro", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.series_id:
            raise ValueError("macro observation requires a series_id")

    def key(self) -> str:
        return f"macro:{self.series_id}:{self.vintage}:{self.event_time.isoformat()}"


@dataclass(frozen=True)
class FilingRecord:
    """A normalized regulatory filing, usable only from its public filing time."""

    issuer_id: str
    accession: str
    form: str
    event_time: datetime
    available_at: datetime
    fields: tuple[tuple[str, str], ...]
    provenance: Provenance
    record_type: str = field(default="filing", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not (self.issuer_id and self.accession and self.form):
            raise ValueError("filing requires issuer_id, accession and form")

    def key(self) -> str:
        return f"filing:{self.issuer_id}:{self.accession}"


@dataclass(frozen=True)
class NewsRecord:
    """A news article treated strictly as data (never as an instruction)."""

    article_id: str
    publisher: str
    event_time: datetime
    available_at: datetime
    title: str
    body_ref: str
    entities: tuple[str, ...]
    provenance: Provenance
    record_type: str = field(default="news", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not (self.article_id and self.publisher):
            raise ValueError("news requires article_id and publisher")

    def key(self) -> str:
        return f"news:{self.publisher}:{self.article_id}"


CanonicalRecord = BarRecord | MacroObservation | FilingRecord | NewsRecord


def record_key(record: CanonicalRecord) -> str:
    return f"{record.record_type}:{record.key()}"


def _prov_to_dict(prov: Provenance) -> dict[str, Any]:
    return {
        "source": prov.source,
        "source_id": prov.source_id,
        "source_hash": prov.source_hash,
        "ingestion_timestamp": prov.ingestion_timestamp.isoformat(),
        "schema_version": prov.schema_version,
        "normalization_version": prov.normalization_version,
        "quality_flags": list(prov.quality_flags),
    }


def _prov_from_dict(data: dict[str, Any]) -> Provenance:
    return Provenance(
        source=data["source"],
        source_id=data["source_id"],
        source_hash=data["source_hash"],
        ingestion_timestamp=parse_dt(data["ingestion_timestamp"]),
        schema_version=data.get("schema_version", SCHEMA_VERSION),
        normalization_version=data.get("normalization_version", NORMALIZATION_VERSION),
        quality_flags=tuple(data.get("quality_flags", ())),
    )


def record_to_dict(record: CanonicalRecord) -> dict[str, Any]:
    """Serialize a canonical record for durable (JSONL) storage."""
    base = {"record_type": record.record_type, "provenance": _prov_to_dict(record.provenance)}
    if isinstance(record, BarRecord):
        return base | {
            "symbol": record.symbol,
            "venue": record.venue,
            "event_time": record.event_time.isoformat(),
            "available_at": record.available_at.isoformat(),
            "open": record.open,
            "high": record.high,
            "low": record.low,
            "close": record.close,
            "volume": record.volume,
            "adjustment_status": record.adjustment_status,
        }
    if isinstance(record, MacroObservation):
        return base | {
            "series_id": record.series_id,
            "event_time": record.event_time.isoformat(),
            "available_at": record.available_at.isoformat(),
            "value": record.value,
            "vintage": record.vintage,
        }
    if isinstance(record, FilingRecord):
        return base | {
            "issuer_id": record.issuer_id,
            "accession": record.accession,
            "form": record.form,
            "event_time": record.event_time.isoformat(),
            "available_at": record.available_at.isoformat(),
            "fields": [list(item) for item in record.fields],
        }
    if isinstance(record, NewsRecord):
        return base | {
            "article_id": record.article_id,
            "publisher": record.publisher,
            "event_time": record.event_time.isoformat(),
            "available_at": record.available_at.isoformat(),
            "title": record.title,
            "body_ref": record.body_ref,
            "entities": list(record.entities),
        }
    raise TypeError(f"unknown canonical record: {type(record)!r}")


def record_from_dict(data: dict[str, Any]) -> CanonicalRecord:
    """Rehydrate a canonical record previously written by ``record_to_dict``."""
    prov = _prov_from_dict(data["provenance"])
    kind = data["record_type"]
    if kind == "bar":
        return BarRecord(
            symbol=data["symbol"],
            venue=data["venue"],
            event_time=parse_dt(data["event_time"]),
            available_at=parse_dt(data["available_at"]),
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            close=float(data["close"]),
            volume=float(data["volume"]),
            adjustment_status=data["adjustment_status"],
            provenance=prov,
        )
    if kind == "macro":
        return MacroObservation(
            series_id=data["series_id"],
            event_time=parse_dt(data["event_time"]),
            available_at=parse_dt(data["available_at"]),
            value=float(data["value"]),
            vintage=data["vintage"],
            provenance=prov,
        )
    if kind == "filing":
        return FilingRecord(
            issuer_id=data["issuer_id"],
            accession=data["accession"],
            form=data["form"],
            event_time=parse_dt(data["event_time"]),
            available_at=parse_dt(data["available_at"]),
            fields=tuple((str(k), str(v)) for k, v in data["fields"]),
            provenance=prov,
        )
    if kind == "news":
        return NewsRecord(
            article_id=data["article_id"],
            publisher=data["publisher"],
            event_time=parse_dt(data["event_time"]),
            available_at=parse_dt(data["available_at"]),
            title=data["title"],
            body_ref=data["body_ref"],
            entities=tuple(data.get("entities", ())),
            provenance=prov,
        )
    raise ValueError(f"unknown record_type: {kind!r}")
