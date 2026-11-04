from datetime import UTC, datetime, timedelta

import pytest

from axel.data.events import EventEnvelope
from axel.data.marketdata import OrderBookSnapshot, TradeSide, TradeTick
from axel.data.quality import (
    DataQualityError,
    DataQualityPolicy,
    QualityCode,
    Severity,
    assert_pit,
    check_bar_series,
    check_order_book,
    check_pit,
    check_record,
    check_timestamp_drift,
    check_trade_series,
)
from axel.data.schemas import BarRecord, Provenance

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def _bar(index: int = 0, **overrides) -> BarRecord:
    event = START + timedelta(minutes=index)
    values = {
        "symbol": "AAPL",
        "venue": "alpaca-iex",
        "instrument_id": "equity:alpaca:AAPL",
        "event_time": event,
        "available_at": event + timedelta(minutes=1),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1_000.0,
        "adjustment_status": "raw",
        "provenance": Provenance(
            source="test",
            source_id=f"bar-{index}",
            source_hash=f"hash-{index}",
            ingestion_timestamp=event,
        ),
    }
    values.update(overrides)
    return BarRecord(**values)


def _book(sequence: int, **overrides) -> OrderBookSnapshot:
    values = {
        "instrument_id": "crypto:binance:BTCUSDT",
        "venue": "binance",
        "event_time": START,
        "available_at": START,
        "bids": ((100.0, 1.0),),
        "asks": ((101.0, 1.0),),
        "sequence": sequence,
        "provenance": Provenance(
            source="binance",
            source_id="book",
            source_hash="book-hash",
            ingestion_timestamp=START,
        ),
    }
    values.update(overrides)
    return OrderBookSnapshot(**values)


def _trade(trade_id: str, event: datetime, **overrides) -> TradeTick:
    values = {
        "instrument_id": "crypto:binance:BTCUSDT",
        "venue": "binance",
        "event_time": event,
        "available_at": event,
        "price": 50_000.0,
        "quantity": 0.1,
        "side": TradeSide.BUY,
        "trade_id": trade_id,
        "provenance": Provenance(
            source="binance",
            source_id=trade_id,
            source_hash=trade_id,
            ingestion_timestamp=event,
        ),
    }
    values.update(overrides)
    return TradeTick(**values)


def test_valid_bar_series_passes_and_duplicate_or_gap_fails() -> None:
    bars = [_bar(0), _bar(1), _bar(2)]
    report = check_bar_series(bars, interval_seconds=60)
    assert report.ok
    assert report.checked == 3

    duplicate = check_bar_series([_bar(0), _bar(0)], interval_seconds=60)
    assert not duplicate.ok
    assert QualityCode.DUPLICATE_RECORD in {issue.code for issue in duplicate.errors}

    gap = check_bar_series([_bar(0), _bar(5)], interval_seconds=60)
    assert not gap.ok
    assert QualityCode.DATA_GAP in {issue.code for issue in gap.errors}

    out_of_order = check_bar_series([_bar(2), _bar(1)], interval_seconds=60)
    assert not out_of_order.ok
    assert QualityCode.OUT_OF_ORDER in {issue.code for issue in out_of_order.errors}


def test_empty_series_fails_closed_by_default() -> None:
    report = check_bar_series([])
    assert not report.ok
    assert QualityCode.MISSING_DATA in {issue.code for issue in report.errors}
    assert check_bar_series([], require_nonempty=False).ok


def test_stale_latest_bar_is_rejected_only_when_clock_is_supplied() -> None:
    bars = [_bar(0), _bar(1)]
    policy = DataQualityPolicy(max_age_seconds=30)
    assert check_bar_series(bars, interval_seconds=60, policy=policy).ok
    stale = check_bar_series(
        bars,
        interval_seconds=60,
        policy=policy,
        now=START + timedelta(minutes=10),
    )
    assert not stale.ok
    assert QualityCode.STALE_DATA in {issue.code for issue in stale.errors}


def test_point_in_time_guard_rejects_future_availability() -> None:
    bar = _bar(0)
    decision = bar.available_at - timedelta(seconds=1)
    report = check_pit([bar], decision_time=decision)
    assert not report.ok
    assert QualityCode.LOOKAHEAD in {issue.code for issue in report.errors}
    with pytest.raises(DataQualityError):
        assert_pit([bar], decision_time=decision)
    assert check_pit([bar], decision_time=bar.available_at).ok


def test_trade_series_detects_duplicates_and_unknown_sides() -> None:
    first = _trade("1", START)
    duplicate = _trade("1", START + timedelta(seconds=1))
    report = check_trade_series([first, duplicate])
    assert not report.ok
    assert QualityCode.DUPLICATE_RECORD in {issue.code for issue in report.errors}

    unknown = _trade("2", START, side=TradeSide.UNKNOWN)
    warning = check_trade_series([unknown])
    assert warning.ok
    assert warning.warnings[0].code is QualityCode.UNKNOWN_TRADE_SIDE
    strict = check_trade_series(
        [unknown], policy=DataQualityPolicy(unknown_trade_side_is_error=True)
    )
    assert not strict.ok


def test_order_book_sequence_gap_and_stale_timestamp() -> None:
    first = _book(10)
    assert check_order_book(first).ok
    gap = check_order_book(_book(12), previous_sequence=10)
    assert not gap.ok
    assert QualityCode.SEQUENCE_GAP in {issue.code for issue in gap.errors}

    stale = check_order_book(
        first,
        now=START + timedelta(seconds=30),
        policy=DataQualityPolicy(max_age_seconds=5),
    )
    assert not stale.ok
    assert QualityCode.STALE_DATA in {issue.code for issue in stale.errors}


def test_timestamp_drift_and_event_envelope_checks() -> None:
    drift = check_timestamp_drift(
        provider_time=START,
        local_time=START + timedelta(seconds=10),
        max_drift_seconds=2,
    )
    assert not drift.ok
    assert QualityCode.TIMESTAMP_DRIFT in {issue.code for issue in drift.errors}

    event = EventEnvelope(
        event_id="e-1",
        event_type="trade",
        source="binance",
        event_timestamp=START,
        published_timestamp=START,
        received_timestamp=START + timedelta(seconds=1),
        payload={"x": 1},
    )
    assert check_record(event).ok
    stale_event = check_record(
        event,
        now=START + timedelta(minutes=5),
        policy=DataQualityPolicy(max_age_seconds=30),
    )
    assert not stale_event.ok
    assert stale_event.errors[0].severity is Severity.ERROR


def test_quality_report_serialises_findings() -> None:
    report = check_bar_series([_bar(0), _bar(0)], interval_seconds=60)
    payload = report.to_dict()
    assert payload["ok"] is False
    assert payload["checked"] == 2
    assert payload["issues"]
