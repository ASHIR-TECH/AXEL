"""
Deterministic order-book and trade-flow features.

Prior art: the Polymarket passive-liquidity tool in ``repos/`` scores resting
orders by book proximity, depth imbalance and short-window trade pressure
before repricing. Those ideas are reimplemented here as *pure, read-only
features* over AXEL's canonical ``OrderBookSnapshot``, ``QuoteTick`` and
``TradeTick`` records -- no order placement, no wallet, no venue call.

Every feature is point-in-time: its ``available_at`` is the latest input
availability, never the wall clock, so the same code is safe in a backtest.
"""

from __future__ import annotations

from collections.abc import Sequence

from axel.data.features.base import FeatureRecord, make_feature, max_available, param
from axel.data.marketdata import OrderBookSnapshot, QuoteTick, TradeSide, TradeTick


def _ordered(records: Sequence, attribute: str = "instrument_id") -> list:
    identities = {getattr(record, attribute) for record in records}
    if len(identities) > 1:
        raise ValueError("microstructure features require one instrument at a time")
    return sorted(records, key=lambda record: record.event_time)


def _record(name: str, record, value: float, params) -> FeatureRecord:
    return make_feature(
        name,
        entity=record.instrument_id,
        event_time=record.event_time,
        available_at=record.available_at,
        value=value,
        params=params,
    )


def spread_bps(books: Sequence[OrderBookSnapshot]) -> list[FeatureRecord]:
    """Quoted spread in basis points of the mid price."""
    params = param()
    return [_record("spread_bps", book, book.spread_bps, params) for book in _ordered(books)]


def depth_imbalance(
    books: Sequence[OrderBookSnapshot], *, levels: int = 5
) -> list[FeatureRecord]:
    """Signed depth imbalance in [-1, 1] over the requested number of levels.

    Positive means more bid notional than ask notional (buy-side pressure).
    """
    if levels <= 0:
        raise ValueError("levels must be positive")
    params = param(levels=levels)
    return [
        _record(f"depth_imbalance_{levels}", book, book.imbalance(levels), params)
        for book in _ordered(books)
    ]


def microprice_deviation_bps(books: Sequence[OrderBookSnapshot]) -> list[FeatureRecord]:
    """Size-weighted microprice deviation from the mid, in basis points.

    The microprice leans toward the side with less resting size: a thin ask
    book pulls it above the mid, which is the standard short-horizon pressure
    signal. A zero-size book reports 0.0 rather than dividing by zero.
    """
    params = param()
    features: list[FeatureRecord] = []
    for book in _ordered(books):
        bid_size = book.bids[0][1]
        ask_size = book.asks[0][1]
        total = bid_size + ask_size
        if total <= 0:
            value = 0.0
        else:
            microprice = (book.best_bid * ask_size + book.best_ask * bid_size) / total
            value = (microprice - book.mid_price) / book.mid_price * 10_000.0
        features.append(_record("microprice_deviation_bps", book, value, params))
    return features


def depth_imbalance_within_bps(
    books: Sequence[OrderBookSnapshot], *, bps: float = 10.0
) -> list[FeatureRecord]:
    """Depth imbalance using only levels within ``bps`` of the mid.

    Resting size far from the touch is not immediately executable, so counting
    it would overstate support. A non-positive ``bps`` is rejected rather than
    silently producing an empty book.
    """
    if bps <= 0:
        raise ValueError("bps must be positive")
    params = param(bps=bps)
    features: list[FeatureRecord] = []
    for book in _ordered(books):
        mid = book.mid_price
        lower = mid * (1.0 - bps / 10_000.0)
        upper = mid * (1.0 + bps / 10_000.0)
        bid_notional = sum(
            price * quantity for price, quantity in book.bids if price >= lower
        )
        ask_notional = sum(
            price * quantity for price, quantity in book.asks if price <= upper
        )
        total = bid_notional + ask_notional
        value = (bid_notional - ask_notional) / total if total > 0 else 0.0
        features.append(
            _record(f"depth_imbalance_within_{bps:g}bps", book, value, params)
        )
    return features


def quote_imbalance(quotes: Sequence[QuoteTick]) -> list[FeatureRecord]:
    """Size imbalance at the touch from best-bid/ask quote sizes.

    Quotes without sizes are skipped: inventing a size would make an
    uninformative quote look like a directional signal.
    """
    params = param()
    features: list[FeatureRecord] = []
    for quote in _ordered(quotes):
        if quote.bid_size is None or quote.ask_size is None:
            continue
        total = quote.bid_size + quote.ask_size
        value = (quote.bid_size - quote.ask_size) / total if total > 0 else 0.0
        features.append(_record("quote_imbalance", quote, value, params))
    return features


def trade_flow_imbalance(
    trades: Sequence[TradeTick], *, window: int = 50
) -> list[FeatureRecord]:
    """Rolling signed-volume imbalance in [-1, 1].

    Aggressor-buy volume counts positive, aggressor-sell negative and unknown
    side zero. A window with no directional volume reports 0.0.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    ordered = _ordered(trades)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(ordered)):
        used = ordered[index - window + 1 : index + 1]
        buys = sum(trade.quantity for trade in used if trade.side == TradeSide.BUY)
        sells = sum(trade.quantity for trade in used if trade.side == TradeSide.SELL)
        total = buys + sells
        value = (buys - sells) / total if total > 0 else 0.0
        latest = used[-1]
        features.append(
            make_feature(
                f"trade_flow_imbalance_{window}",
                entity=latest.instrument_id,
                event_time=latest.event_time,
                available_at=max_available(used),
                value=value,
                params=params,
            )
        )
    return features


__all__ = [
    "depth_imbalance",
    "depth_imbalance_within_bps",
    "microprice_deviation_bps",
    "quote_imbalance",
    "spread_bps",
    "trade_flow_imbalance",
]
