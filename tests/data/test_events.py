from datetime import UTC, datetime, timedelta

import pytest

from axel.data.events import (
    AppendOutcome,
    EventConflictError,
    EventEnvelope,
    InMemoryEventLog,
    envelope_for_market_record,
    envelope_for_record,
    make_event_id,
    payload_hash,
)
from axel.data.marketdata import (
    FundingRateObservation,
    OrderBookSnapshot,
    TradeSide,
    TradeTick,
    market_record_from_dict,
    market_record_to_dict,
)
from axel.data.schemas import BarRecord, Provenance

NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def _provenance(source: str = "test") -> Provenance:
    return Provenance(
        source=source,
        source_id="id-1",
        source_hash="hash-1",
        ingestion_timestamp=NOW,
    )


def _bar() -> BarRecord:
    return BarRecord(
        symbol="AAPL",
        venue="alpaca-iex",
        instrument_id="equity:alpaca:AAPL",
        event_time=NOW,
        available_at=NOW + timedelta(minutes=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10_000.0,
        adjustment_status="raw",
        provenance=_provenance("alpaca"),
    )


def _trade() -> TradeTick:
    return TradeTick(
        instrument_id="crypto:binance:BTCUSDT",
        venue="binance",
        event_time=NOW,
        available_at=NOW,
        price=50_000.0,
        quantity=0.1,
        side=TradeSide.BUY,
        trade_id="42",
        provenance=_provenance("binance"),
    )


def _envelope(payload: dict | None = None, *, event_id: str = "e-1") -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type="trade",
        source="binance",
        instrument_id="crypto:binance:BTCUSDT",
        event_timestamp=NOW,
        published_timestamp=NOW,
        received_timestamp=NOW + timedelta(seconds=1),
        payload=payload or {"price": 50_000.0, "quantity": 0.1},
    )


def test_envelope_hashes_payload_and_separates_clocks() -> None:
    event = _envelope()
    assert event.payload_hash == payload_hash({"price": 50_000.0, "quantity": 0.1})
    assert event.available_timestamp == NOW
    assert event.is_available_at(NOW)
    assert not event.is_available_at(NOW - timedelta(seconds=1))

    with pytest.raises(ValueError, match="published_timestamp"):
        EventEnvelope(
            event_id="e-2",
            event_type="trade",
            source="binance",
            event_timestamp=NOW,
            published_timestamp=NOW - timedelta(seconds=1),
            received_timestamp=NOW,
            payload={"x": 1},
        )


def test_envelope_rejects_tampered_hash_and_non_json_payload() -> None:
    with pytest.raises(ValueError, match="payload_hash"):
        EventEnvelope(
            event_id="e-3",
            event_type="trade",
            source="binance",
            event_timestamp=NOW,
            published_timestamp=NOW,
            received_timestamp=NOW,
            payload={"x": 1},
            payload_hash="not-the-hash",
        )
    with pytest.raises(ValueError, match="canonical-JSON"):
        _envelope({"bad": float("nan")})


def test_envelope_round_trip_is_stable() -> None:
    event = _envelope()
    restored = EventEnvelope.from_dict(event.to_dict())
    assert restored == event
    assert restored.payload_json == event.payload_json


def test_idempotent_log_appends_once_and_conflicts_on_changed_payload() -> None:
    log = InMemoryEventLog()
    event = _envelope()
    assert log.append(event) is AppendOutcome.APPENDED
    assert log.append(event) is AppendOutcome.DUPLICATE
    assert len(log) == 1

    changed = _envelope({"price": 50_001.0})
    with pytest.raises(EventConflictError, match="different payload"):
        log.append(changed)


def test_replay_orders_by_availability_and_filters() -> None:
    log = InMemoryEventLog()
    first = _envelope({"x": 1}, event_id="a")
    second = EventEnvelope(
        event_id="b",
        event_type="quote",
        source="binance",
        event_timestamp=NOW + timedelta(seconds=5),
        published_timestamp=NOW + timedelta(seconds=5),
        received_timestamp=NOW + timedelta(seconds=6),
        payload={"bid": 1.0, "ask": 2.0},
    )
    log.extend([second, first])
    assert [event.event_id for event in log.replay()] == ["a", "b"]
    assert [event.event_id for event in log.replay(event_type="quote")] == ["b"]
    assert log.latest_available("crypto:binance:BTCUSDT", NOW + timedelta(seconds=2)) == first


def test_market_record_round_trip_and_event_wrapping() -> None:
    trade = _trade()
    assert market_record_from_dict(market_record_to_dict(trade)) == trade
    event = envelope_for_market_record(trade)
    assert event.instrument_id == trade.instrument_id
    assert event.event_timestamp == trade.event_time
    assert event.payload["trade_id"] == "42"


def test_canonical_bar_wrapping_preserves_availability() -> None:
    bar = _bar()
    event = envelope_for_record(bar)
    assert event.instrument_id == bar.instrument_id
    assert event.available_timestamp == bar.available_at
    assert event.payload["close"] == bar.close


def test_funding_sign_convention_is_explicit() -> None:
    funding = FundingRateObservation(
        instrument_id="crypto:binance:BTCUSDT",
        venue="binance",
        event_time=NOW,
        available_at=NOW,
        rate=0.0001,
        provenance=_provenance("binance"),
    )
    assert funding.funding_payment(1.0, 50_000.0) == pytest.approx(5.0)
    assert funding.funding_payment(-1.0, 50_000.0) == pytest.approx(-5.0)


def test_order_book_metrics_and_crossed_rejection() -> None:
    book = OrderBookSnapshot(
        instrument_id="crypto:binance:BTCUSDT",
        venue="binance",
        event_time=NOW,
        available_at=NOW,
        bids=((100.0, 2.0), (99.0, 3.0)),
        asks=((101.0, 1.0), (102.0, 4.0)),
        provenance=_provenance("binance"),
        sequence=10,
    )
    assert book.mid_price == 100.5
    assert book.spread_bps == pytest.approx(100 / 100.5 * 100, rel=1e-9)
    assert book.depth_usd(1) == (200.0, 101.0)
    assert book.imbalance(1) == pytest.approx((200 - 101) / (200 + 101))
    with pytest.raises(ValueError, match="crossed"):
        OrderBookSnapshot(
            instrument_id="crypto:binance:BTCUSDT",
            venue="binance",
            event_time=NOW,
            available_at=NOW,
            bids=((101.0, 1.0),),
            asks=((101.0, 1.0),),
            provenance=_provenance("binance"),
        )


def test_make_event_id_is_content_addressed() -> None:
    content = payload_hash({"x": 1})
    first = make_event_id("src", "trade", "i", NOW, content)
    second = make_event_id("src", "trade", "i", NOW, content)
    other = make_event_id("src", "trade", "i", NOW, payload_hash({"x": 2}))
    assert first == second != other
