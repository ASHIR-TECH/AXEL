"""
Restartable, idempotent ingestion pipeline.

Flow: persist immutable raw payload -> normalize/flag -> deduplicate -> versioned
store -> metrics. Repeating an interval never creates duplicate logical records.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from axel.data.ingest.raw_store import RawStore
from axel.data.ingest.store import CanonicalStore
from axel.data.normalization import InvalidRecord, flag_record, normalize_raw
from axel.data.providers.base import ProviderResult
from axel.data.schemas import CanonicalRecord, RawPayload


@dataclass(frozen=True)
class IngestionReport:
    """Outcome metrics for one ingestion run (also feeds observability)."""

    source: str
    raw_hash: str
    parsed: int
    normalized: int
    stored: int
    duplicates: int
    rejected: int
    errors: tuple[str, ...] = ()

    @property
    def inserted(self) -> int:
        return self.stored

    @property
    def is_repeat(self) -> bool:
        return self.stored == 0 and self.parsed >= 0

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "raw_hash": self.raw_hash,
            "parsed": self.parsed,
            "normalized": self.normalized,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "errors": list(self.errors),
        }


class IngestionPipeline:
    """Coordinates raw persistence, normalization and deduplicated storage."""

    def __init__(
        self,
        raw_store: RawStore,
        canonical_store: CanonicalStore,
        *,
        on_report: Callable[[IngestionReport], None] | None = None,
    ) -> None:
        self._raw_store = raw_store
        self._canonical_store = canonical_store
        self._on_report = on_report

    def ingest(self, result: ProviderResult) -> IngestionReport:
        return self._process(result.raw, result.records)

    def replay(self, raw: RawPayload) -> IngestionReport:
        """Re-derive canonical records from a stored raw payload and store them."""
        return self._process(raw, tuple(normalize_raw(raw)))

    def _process(
        self, raw: RawPayload, records: Iterable[CanonicalRecord]
    ) -> IngestionReport:
        # 1. Persist the immutable raw payload exactly once (content-addressed).
        self._raw_store.persist(raw)

        parsed = 0
        normalized = 0
        stored = 0
        duplicates = 0
        rejected = 0
        errors: list[str] = []

        for record in records:
            parsed += 1
            try:
                flagged = flag_record(record)
            except (InvalidRecord, ValueError) as exc:  # fail closed per record
                rejected += 1
                errors.append(f"{type(record).__name__}: {exc}")
                continue
            normalized += 1
            if self._canonical_store.put(flagged):
                stored += 1
            else:
                duplicates += 1

        report = IngestionReport(
            source=raw.source,
            raw_hash=raw.content_hash,
            parsed=parsed,
            normalized=normalized,
            stored=stored,
            duplicates=duplicates,
            rejected=rejected,
            errors=tuple(errors),
        )
        if self._on_report is not None:
            self._on_report(report)
        return report
