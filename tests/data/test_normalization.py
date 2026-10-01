from datetime import UTC, datetime, timedelta

import pytest

from axel.data.normalization import InvalidRecord, flag_record, normalize_raw
from axel.data.schemas import BarRecord, Provenance, RawPayload


def _prov() -> Provenance:
    return Provenance(
        source="alpaca",
        source_id="AAPL:1Day",
        source_hash="hash",
        ingestion_timestamp=datetime(2026, 1, 3, tzinfo=UTC),
    )


def _bar(volume: float = 10.0, high: float = 2.0, low: float = 1.0) -> BarRecord:
    event = datetime(2026, 1, 2, tzinfo=UTC)
    return BarRecord(
        symbol="AAPL",
        venue="alpaca-iex",
        event_time=event,
        available_at=event + timedelta(days=1),
        open=1.0,
        high=high,
        low=low,
        close=1.5,
        volume=volume,
        adjustment_status="raw",
        provenance=_prov(),
    )


def test_flag_record_annotates_quality_without_mutating_values() -> None:
    flagged = flag_record(_bar(volume=0.0))
    assert "ZERO_VOLUME" in flagged.provenance.quality_flags
    assert flagged.close == 1.5


def test_normalize_raw_replays_alpaca_payload_deterministically() -> None:
    body = '{"bars": {"AAPL": [{"t": "2026-01-02T00:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 4}]}}'
    raw = RawPayload(
        source="alpaca",
        request="https://data.alpaca.markets/v2/stocks/bars",
        fetched_at=datetime(2026, 1, 3, tzinfo=UTC),
        body=body,
        context=(("symbol", "AAPL"), ("timeframe", "1Day"), ("venue", "alpaca-iex")),
    )
    records = normalize_raw(raw)
    assert len(records) == 1
    assert records[0].symbol == "AAPL"
    assert records[0].available_at > records[0].event_time


def test_normalize_raw_missing_context_fails_closed() -> None:
    raw = RawPayload(
        source="fred",
        request="https://api.stlouisfed.org",
        fetched_at=datetime(2026, 1, 3, tzinfo=UTC),
        body="{}",
        context=(),
    )
    with pytest.raises(InvalidRecord):
        normalize_raw(raw)
