"""
Canonical cross-asset market-data records.

These are the point-in-time records returned by capability-aware providers.
Every record carries an explicit ``event_time`` and ``available_at``; a consumer
must never infer availability from the event timestamp alone.

The records are deliberately asset-agnostic. Asset-specific semantics live in
the instrument metadata and in the feature/analytics layer, not in a provider's
native payload.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from axel.data.schemas import Provenance, check_available, require_tz

MARKET_DATA_SCHEMA_VERSION = "marketdata-1"


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TradeTick:
    """A single trade print with aggressor information when the venue provides it."""

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    price: float
    quantity: float
    side: TradeSide
    trade_id: str
    provenance: Provenance
    sequence: int | None = None
    record_type: str = field(default="trade", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue or not self.trade_id:
            raise ValueError("trade requires instrument_id, venue and trade_id")
        if self.price <= 0:
            raise ValueError("trade price must be positive")
        if self.quantity <= 0:
            raise ValueError("trade quantity must be positive")
        if not isinstance(self.side, TradeSide):
            raise TypeError("trade side must be a TradeSide")
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("trade sequence cannot be negative")

    def key(self) -> str:
        return f"trade:{self.venue}:{self.instrument_id}:{self.trade_id}"


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """An L2 order-book snapshot with a venue sequence number when available."""

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    provenance: Provenance
    sequence: int | None = None
    record_type: str = field(default="order_book", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue:
            raise ValueError("order book requires instrument_id and venue")
        if not self.bids or not self.asks:
            raise ValueError("order book must contain both bids and asks")
        self._validate_side(self.bids, descending=True, name="bids")
        self._validate_side(self.asks, descending=False, name="asks")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError(
                f"crossed order book: best bid {self.bids[0][0]} >= best ask {self.asks[0][0]}"
            )
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("order-book sequence cannot be negative")

    @staticmethod
    def _validate_side(
        levels: Sequence[tuple[float, float]], *, descending: bool, name: str
    ) -> None:
        previous: float | None = None
        for price, quantity in levels:
            if price <= 0 or quantity <= 0:
                raise ValueError(f"{name} prices and quantities must be positive")
            if previous is not None:
                if descending and price > previous:
                    raise ValueError(f"{name} must be sorted best-to-worst")
                if not descending and price < previous:
                    raise ValueError(f"{name} must be sorted best-to-worst")
            previous = price

    @property
    def best_bid(self) -> float:
        return self.bids[0][0]

    @property
    def best_ask(self) -> float:
        return self.asks[0][0]

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        return (self.spread / mid) * 10_000.0 if mid > 0 else 0.0

    def depth_usd(self, levels: int = 5) -> tuple[float, float]:
        """Aggregate bid/ask notional over the requested number of levels."""
        if levels <= 0:
            raise ValueError("levels must be positive")
        bid = sum(price * quantity for price, quantity in self.bids[:levels])
        ask = sum(price * quantity for price, quantity in self.asks[:levels])
        return bid, ask

    def imbalance(self, levels: int = 5) -> float:
        """Order-book imbalance ``(Q_bid - Q_ask) / (Q_bid + Q_ask)`` in [-1, 1]."""
        bid, ask = self.depth_usd(levels)
        total = bid + ask
        return (bid - ask) / total if total > 0 else 0.0

    def key(self) -> str:
        suffix = self.sequence if self.sequence is not None else self.event_time.isoformat()
        return f"order_book:{self.venue}:{self.instrument_id}:{suffix}"


@dataclass(frozen=True, slots=True)
class QuoteTick:
    """Best bid/ask quote for an instrument at a point in time."""

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    bid: float
    ask: float
    provenance: Provenance
    bid_size: float | None = None
    ask_size: float | None = None
    record_type: str = field(default="quote", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue:
            raise ValueError("quote requires instrument_id and venue")
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError("quote prices must be positive")
        if self.bid >= self.ask:
            raise ValueError(f"crossed quote: bid {self.bid} >= ask {self.ask}")
        for name, value in (("bid_size", self.bid_size), ("ask_size", self.ask_size)):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float:
        return (self.spread / self.mid) * 10_000.0

    def key(self) -> str:
        return f"quote:{self.venue}:{self.instrument_id}:{self.event_time.isoformat()}"


@dataclass(frozen=True, slots=True)
class FundingRateObservation:
    """A perpetual-futures funding observation.

    A positive rate means longs pay shorts. Use ``funding_payment`` rather than
    re-implementing the sign convention at each call site.
    """

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    rate: float
    provenance: Provenance
    interval_hours: float = 8.0
    next_funding_time: datetime | None = None
    mark_price: float | None = None
    index_price: float | None = None
    record_type: str = field(default="funding_rate", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue:
            raise ValueError("funding rate requires instrument_id and venue")
        if self.interval_hours <= 0:
            raise ValueError("funding interval_hours must be positive")
        if self.next_funding_time is not None:
            require_tz(self.next_funding_time, "next_funding_time")
        for name, value in (("mark_price", self.mark_price), ("index_price", self.index_price)):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when supplied")

    def funding_payment(self, signed_quantity: float, price: float) -> float:
        """Cash paid by the position holder; positive means cash leaves the account."""
        if price <= 0:
            raise ValueError("price must be positive")
        return signed_quantity * price * self.rate

    def key(self) -> str:
        return f"funding_rate:{self.venue}:{self.instrument_id}:{self.event_time.isoformat()}"


@dataclass(frozen=True, slots=True)
class OpenInterestObservation:
    """Open interest for a derivative contract."""

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    contracts: float
    provenance: Provenance
    notional: float | None = None
    record_type: str = field(default="open_interest", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue:
            raise ValueError("open interest requires instrument_id and venue")
        if self.contracts < 0:
            raise ValueError("open interest contracts cannot be negative")
        if self.notional is not None and self.notional < 0:
            raise ValueError("open interest notional cannot be negative")

    def key(self) -> str:
        return f"open_interest:{self.venue}:{self.instrument_id}:{self.event_time.isoformat()}"


MarketDataRecord = (
    TradeTick | OrderBookSnapshot | QuoteTick | FundingRateObservation | OpenInterestObservation
)


def market_record_key(record: MarketDataRecord) -> str:
    return f"{record.record_type}:{record.key()}"


def _provenance_to_dict(provenance: Provenance) -> dict[str, Any]:
    return {
        "source": provenance.source,
        "source_id": provenance.source_id,
        "source_hash": provenance.source_hash,
        "ingestion_timestamp": provenance.ingestion_timestamp.isoformat(),
        "schema_version": provenance.schema_version,
        "normalization_version": provenance.normalization_version,
        "quality_flags": list(provenance.quality_flags),
    }


def _provenance_from_dict(data: Mapping[str, Any]) -> Provenance:
    return Provenance(
        source=str(data["source"]),
        source_id=str(data["source_id"]),
        source_hash=str(data["source_hash"]),
        ingestion_timestamp=datetime.fromisoformat(str(data["ingestion_timestamp"])),
        schema_version=str(data.get("schema_version", "canonical-1")),
        normalization_version=str(data.get("normalization_version", "norm-1")),
        quality_flags=tuple(str(flag) for flag in data.get("quality_flags", ())),
    )


def market_record_to_dict(record: MarketDataRecord) -> dict[str, Any]:
    base: dict[str, Any] = {
        "record_type": record.record_type,
        "instrument_id": record.instrument_id,
        "venue": record.venue,
        "event_time": record.event_time.isoformat(),
        "available_at": record.available_at.isoformat(),
        "provenance": _provenance_to_dict(record.provenance),
    }
    if isinstance(record, TradeTick):
        return base | {
            "price": record.price,
            "quantity": record.quantity,
            "side": record.side.value,
            "trade_id": record.trade_id,
            "sequence": record.sequence,
        }
    if isinstance(record, OrderBookSnapshot):
        return base | {
            "bids": [list(level) for level in record.bids],
            "asks": [list(level) for level in record.asks],
            "sequence": record.sequence,
        }
    if isinstance(record, QuoteTick):
        return base | {
            "bid": record.bid,
            "ask": record.ask,
            "bid_size": record.bid_size,
            "ask_size": record.ask_size,
        }
    if isinstance(record, FundingRateObservation):
        return base | {
            "rate": record.rate,
            "interval_hours": record.interval_hours,
            "next_funding_time": (
                record.next_funding_time.isoformat() if record.next_funding_time else None
            ),
            "mark_price": record.mark_price,
            "index_price": record.index_price,
        }
    if isinstance(record, OpenInterestObservation):
        return base | {"contracts": record.contracts, "notional": record.notional}
    raise TypeError(f"unknown market-data record: {type(record)!r}")


def market_record_from_dict(data: Mapping[str, Any]) -> MarketDataRecord:
    provenance = _provenance_from_dict(data["provenance"])
    kind = data["record_type"]
    common = {
        "instrument_id": str(data["instrument_id"]),
        "venue": str(data["venue"]),
        "event_time": datetime.fromisoformat(str(data["event_time"])),
        "available_at": datetime.fromisoformat(str(data["available_at"])),
        "provenance": provenance,
    }
    if kind == "trade":
        return TradeTick(
            **common,
            price=float(data["price"]),
            quantity=float(data["quantity"]),
            side=TradeSide(data["side"]),
            trade_id=str(data["trade_id"]),
            sequence=data.get("sequence"),
        )
    if kind == "order_book":
        return OrderBookSnapshot(
            **common,
            bids=tuple((float(price), float(quantity)) for price, quantity in data["bids"]),
            asks=tuple((float(price), float(quantity)) for price, quantity in data["asks"]),
            sequence=data.get("sequence"),
        )
    if kind == "quote":
        return QuoteTick(
            **common,
            bid=float(data["bid"]),
            ask=float(data["ask"]),
            bid_size=data.get("bid_size"),
            ask_size=data.get("ask_size"),
        )
    if kind == "funding_rate":
        next_funding = data.get("next_funding_time")
        return FundingRateObservation(
            **common,
            rate=float(data["rate"]),
            interval_hours=float(data.get("interval_hours", 8.0)),
            next_funding_time=datetime.fromisoformat(next_funding) if next_funding else None,
            mark_price=data.get("mark_price"),
            index_price=data.get("index_price"),
        )
    if kind == "open_interest":
        return OpenInterestObservation(
            **common,
            contracts=float(data["contracts"]),
            notional=data.get("notional"),
        )
    raise ValueError(f"unknown market-data record_type: {kind!r}")


__all__ = [
    "MARKET_DATA_SCHEMA_VERSION",
    "FundingRateObservation",
    "MarketDataRecord",
    "OpenInterestObservation",
    "OrderBookSnapshot",
    "QuoteTick",
    "TradeSide",
    "TradeTick",
    "market_record_from_dict",
    "market_record_key",
    "market_record_to_dict",
]
