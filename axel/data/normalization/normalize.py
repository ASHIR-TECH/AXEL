"""
Normalization: validate canonical records, attach quality flags, and re-parse raw
payloads for deterministic replay.

Normalization never obscures data. Suspicious-but-usable records are flagged;
records that violate hard invariants are rejected (fail closed) by the caller.
"""

from __future__ import annotations

from dataclasses import replace

from axel.data.providers import parse_bars, parse_filings, parse_news, parse_observations
from axel.data.schemas import (
    BarRecord,
    CanonicalRecord,
    FilingRecord,
    MacroObservation,
    NewsRecord,
    RawPayload,
)

WIDE_RANGE_RATIO = 0.5
MACRO_LAG_DAYS = 400


class InvalidRecord(ValueError):
    """Raised when a canonical record violates a hard invariant."""


def flag_record(record: CanonicalRecord) -> CanonicalRecord:
    """Return the record with data-quality flags attached to its provenance."""
    flags: list[str] = []
    if isinstance(record, BarRecord):
        if record.volume == 0:
            flags.append("ZERO_VOLUME")
        if record.high == record.low:
            flags.append("FLAT_BAR")
        if record.available_at == record.event_time:
            flags.append("SAME_BAR_AVAILABILITY")
        if (record.high - record.low) / record.close > WIDE_RANGE_RATIO:
            flags.append("WIDE_RANGE")
    elif isinstance(record, MacroObservation):
        if (record.available_at - record.event_time).days > MACRO_LAG_DAYS:
            flags.append("MACRO_LAG")
    elif isinstance(record, FilingRecord):
        if record.event_time == record.available_at:
            flags.append("SAME_DAY_FILING")
    elif isinstance(record, NewsRecord):
        if not record.entities:
            flags.append("NO_ENTITIES")
    if not flags:
        return record
    return replace(record, provenance=record.provenance.with_flags(*flags))


def normalize_records(records: tuple[CanonicalRecord, ...] | list[CanonicalRecord]) -> list[CanonicalRecord]:
    """Validate and flag a batch of already-parsed canonical records."""
    return [flag_record(record) for record in records]


def normalize_raw(raw: RawPayload) -> list[CanonicalRecord]:
    """Re-parse a stored raw payload into canonical records (replay path)."""
    context = raw.context_dict()
    try:
        if raw.source == "alpaca":
            return parse_bars(
                raw.body,
                symbol=context["symbol"],
                venue=context.get("venue", "alpaca-iex"),
                timeframe=context.get("timeframe", "1Day"),
                ingestion_timestamp=raw.fetched_at,
            )
        if raw.source == "fred":
            return parse_observations(
                raw.body, series_id=context["series_id"], ingestion_timestamp=raw.fetched_at
            )
        if raw.source == "sec":
            return parse_filings(raw.body, cik=context["cik"], ingestion_timestamp=raw.fetched_at)
        if raw.source in {"gdelt", "news"}:
            return parse_news(raw.body, ingestion_timestamp=raw.fetched_at)
    except KeyError as exc:
        raise InvalidRecord(f"raw payload missing required context key: {exc}") from exc
    raise InvalidRecord(f"no normalizer registered for source {raw.source!r}")
