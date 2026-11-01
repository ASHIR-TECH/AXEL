"""Order-book and trade-flow features adapted from the passive-liquidity tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from axel.data.features import (
    depth_imbalance,
    depth_imbalance_within_bps,
    microprice_deviation_bps,
    quote_imbalance,
    spread_bps,
    trade_flow_imbalance,
)
from axel.data.marketdata import OrderBookSnapshot, QuoteTick, TradeSide, TradeTick
from axel.data.schemas import Provenance

START = datetime(2026, 1, 1, tzinfo=UTC)


def _prov(seed: int) -> Provenance:
    return Provenance(
        source="test",
        source_id=str(seed),
        source_hash=f"hash-{seed}",
        ingestion_timestamp=START,
    )


def _book(
    index: int,
    *,
    instrument: str = "BTC-USD",
    bids: tuple[tuple[float, float], ...] = ((99.5, 10.0), (99.0, 20.0)),
    asks: tuple[tuple[float, float], ...] = ((100.5, 5.0), (101.0, 15.0)),
) -> OrderBookSnapshot:
    event = START + timedelta(seconds=index)
    return OrderBookSnapshot(
        instrument_id=instrument,
        venue="binance",
        event_time=event,
        available_at=event,
        bids=bids,
        asks=asks,
        provenance=_prov(index),
    )


def _quote(index: int, *, bid_size: float | None = 10.0, ask_size: float | None = 5.0) -> QuoteTick:
    event = START + timedelta(seconds=index)
    return QuoteTick(
        instrument_id="BTC-USD",
        venue="binance",
        event_time=event,
        available_at=event,
        bid=99.5,
        ask=100.5,
        bid_size=bid_size,
        ask_size=ask_size,
        provenance=_prov(index),
    )


def _trade(index: int, *, side: TradeSide, quantity: float) -> TradeTick:
    event = START + timedelta(seconds=index)
    return TradeTick(
        instrument_id="BTC-USD",
        venue="binance",
        event_time=event,
        available_at=event,
        price=100.0,
        quantity=quantity,
        side=side,
        trade_id=f"t{index}",
        provenance=_prov(index),
    )


def test_spread_bps_matches_the_quoted_spread() -> None:
    features = spread_bps([_book(0)])
    assert features[0].value == pytest.approx(100.0)


def test_depth_imbalance_is_positive_when_bids_are_deeper() -> None:
    features = depth_imbalance([_book(0)], levels=2)
    assert features[0].value == pytest.approx((2975.0 - 2017.5) / (2975.0 + 2017.5))


def test_depth_imbalance_rejects_nonpositive_levels() -> None:
    with pytest.raises(ValueError):
        depth_imbalance([_book(0)], levels=0)


def test_microprice_leants_toward_the_thin_side() -> None:
    features = microprice_deviation_bps([_book(0)])
    microprice = (99.5 * 5.0 + 100.5 * 10.0) / 15.0
    assert features[0].value == pytest.approx((microprice - 100.0) / 100.0 * 10_000.0)


def test_depth_within_bps_excludes_far_levels() -> None:
    book = _book(0)
    tight = depth_imbalance_within_bps([book], bps=10.0)
    wide = depth_imbalance_within_bps([book], bps=1_000.0)
    assert tight[0].value == 0.0
    assert wide[0].value == pytest.approx((2975.0 - 2017.5) / (2975.0 + 2017.5))


def test_quote_imbalance_uses_touch_sizes() -> None:
    features = quote_imbalance([_quote(0)])
    assert features[0].value == pytest.approx((10.0 - 5.0) / 15.0)


def test_quote_imbalance_skips_quotes_without_sizes() -> None:
    assert quote_imbalance([_quote(0, bid_size=None)]) == []


def test_trade_flow_imbalance_uses_aggressor_side() -> None:
    trades = [
        _trade(0, side=TradeSide.BUY, quantity=10.0),
        _trade(1, side=TradeSide.SELL, quantity=5.0),
        _trade(2, side=TradeSide.BUY, quantity=5.0),
    ]
    features = trade_flow_imbalance(trades, window=3)
    assert features[0].value == pytest.approx(0.5)


def test_trade_flow_imbalance_ignores_unknown_side() -> None:
    trades = [
        _trade(0, side=TradeSide.UNKNOWN, quantity=10.0),
        _trade(1, side=TradeSide.BUY, quantity=5.0),
    ]
    assert trade_flow_imbalance(trades, window=2)[0].value == pytest.approx(1.0)


def test_microstructure_features_reject_mixed_instruments() -> None:
    with pytest.raises(ValueError):
        spread_bps([_book(0), _book(1, instrument="ETH-USD")])


def test_microstructure_features_are_point_in_time() -> None:
    produced = [
        *spread_bps([_book(0)]),
        *depth_imbalance([_book(0)]),
        *microprice_deviation_bps([_book(0)]),
        *depth_imbalance_within_bps([_book(0)]),
        *quote_imbalance([_quote(0)]),
        *trade_flow_imbalance([_trade(0, side=TradeSide.BUY, quantity=1.0)], window=1),
    ]
    assert all(feature.available_at >= feature.event_time for feature in produced)
    assert all(len(feature.fingerprint()) == 64 for feature in produced)
