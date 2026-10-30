"""
Canonical prediction-market records.

Binary contracts are not equities or options. A YES price is a probability in
``[0, 1]``; 0 and 1 are legitimate prices, and a market can be locked or
settled. Reusing the equity ``BarRecord``/``QuoteTick`` validators would either
reject valid observations or weaken validation for every other asset class.

These records therefore keep prediction-market semantics explicit:

* ``PredictionQuote`` -- YES/NO bid/ask, last price, volume and open interest.
* ``PredictionTrade`` -- executed price and taker side, with the YES/NO
  complement preserved rather than guessed.
* ``PredictionBar`` -- OHLCV in probability units.
* ``PredictionMarketInfo`` -- rules, status, close/settlement times, result and
  settlement source, so a strategy cannot use an unverified outcome.

All records carry ``event_time`` and ``available_at`` and are validated
point-in-time at construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from axel.data.schemas import Provenance, check_available, require_tz

PREDICTION_SCHEMA_VERSION = "prediction-1"


class PredictionMarketStatus(StrEnum):
    INITIALIZED = "initialized"
    ACTIVE = "active"
    CLOSED = "closed"
    DETERMINED = "determined"
    DISPUTED = "disputed"
    AMENDED = "amended"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class PredictionResult(StrEnum):
    YES = "yes"
    NO = "no"
    VOID = "void"
    UNRESOLVED = "unresolved"


class PredictionSide(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def _probability(value: float | None, name: str) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value}")


def _optional_time(value: datetime | None, name: str) -> None:
    if value is not None:
        require_tz(value, name)


@dataclass(frozen=True, slots=True)
class PredictionQuote:
    """Point-in-time YES/NO quote for a binary prediction contract."""

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    provenance: Provenance
    last_price: float | None = None
    volume: float = 0.0
    open_interest: float = 0.0
    status: PredictionMarketStatus = PredictionMarketStatus.ACTIVE
    record_type: str = field(default="prediction_quote", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue:
            raise ValueError("prediction quote requires instrument_id and venue")
        for name, value in (
            ("yes_bid", self.yes_bid),
            ("yes_ask", self.yes_ask),
            ("no_bid", self.no_bid),
            ("no_ask", self.no_ask),
            ("last_price", self.last_price),
        ):
            _probability(value, name)
        if self.yes_bid > self.yes_ask:
            raise ValueError(f"crossed YES quote: bid {self.yes_bid} > ask {self.yes_ask}")
        if self.no_bid > self.no_ask:
            raise ValueError(f"crossed NO quote: bid {self.no_bid} > ask {self.no_ask}")
        if self.volume < 0 or self.open_interest < 0:
            raise ValueError("volume and open_interest cannot be negative")
        if not isinstance(self.status, PredictionMarketStatus):
            raise TypeError("status must be a PredictionMarketStatus")

    @property
    def mid_yes(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread_yes(self) -> float:
        return self.yes_ask - self.yes_bid

    def key(self) -> str:
        return (
            f"prediction_quote:{self.venue}:{self.instrument_id}:"
            f"{self.event_time.isoformat()}"
        )


@dataclass(frozen=True, slots=True)
class PredictionTrade:
    """An executed binary-contract trade.

    ``yes_price`` is always recorded in YES-probability units. ``no_price`` is
    the complement, exposed as a property so the two conventions cannot drift.
    """

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    yes_price: float
    quantity: float
    taker_side: PredictionSide
    trade_id: str
    provenance: Provenance
    sequence: int | None = None
    record_type: str = field(default="prediction_trade", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue or not self.trade_id:
            raise ValueError("prediction trade requires instrument_id, venue and trade_id")
        _probability(self.yes_price, "yes_price")
        if self.quantity <= 0:
            raise ValueError("prediction trade quantity must be positive")
        if not isinstance(self.taker_side, PredictionSide):
            raise TypeError("taker_side must be a PredictionSide")
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("prediction trade sequence cannot be negative")

    @property
    def no_price(self) -> float:
        return 1.0 - self.yes_price

    def key(self) -> str:
        return f"prediction_trade:{self.venue}:{self.instrument_id}:{self.trade_id}"


@dataclass(frozen=True, slots=True)
class PredictionBar:
    """OHLCV for a binary contract, in probability units."""

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    provenance: Provenance
    record_type: str = field(default="prediction_bar", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue:
            raise ValueError("prediction bar requires instrument_id and venue")
        for name, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            _probability(value, name)
        if self.low > self.high:
            raise ValueError("prediction bar low cannot exceed high")
        if self.volume < 0:
            raise ValueError("prediction bar volume cannot be negative")

    def key(self) -> str:
        return f"prediction_bar:{self.venue}:{self.instrument_id}:{self.event_time.isoformat()}"


@dataclass(frozen=True, slots=True)
class PredictionOrderBook:
    """YES/NO order book for a binary contract, in probability units.

    Unlike the generic ``OrderBookSnapshot``, 0 and 1 are valid prices here.
    A locked book (best bid == best ask) is recorded, not hidden; a crossed
    book is rejected as invalid.
    """

    instrument_id: str
    venue: str
    event_time: datetime
    available_at: datetime
    yes_bids: tuple[tuple[float, float], ...]
    yes_asks: tuple[tuple[float, float], ...]
    no_bids: tuple[tuple[float, float], ...]
    no_asks: tuple[tuple[float, float], ...]
    provenance: Provenance
    record_type: str = field(default="prediction_order_book", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.instrument_id or not self.venue:
            raise ValueError("prediction order book requires instrument_id and venue")
        for name, levels, descending in (
            ("yes_bids", self.yes_bids, True),
            ("yes_asks", self.yes_asks, False),
            ("no_bids", self.no_bids, True),
            ("no_asks", self.no_asks, False),
        ):
            _validate_prediction_levels(levels, name=name, descending=descending)
        if self.yes_bids and self.yes_asks and self.yes_bids[0][0] > self.yes_asks[0][0]:
            raise ValueError(
                f"crossed YES book: bid {self.yes_bids[0][0]} > ask {self.yes_asks[0][0]}"
            )
        if self.no_bids and self.no_asks and self.no_bids[0][0] > self.no_asks[0][0]:
            raise ValueError(
                f"crossed NO book: bid {self.no_bids[0][0]} > ask {self.no_asks[0][0]}"
            )

    @property
    def best_yes_bid(self) -> float | None:
        return self.yes_bids[0][0] if self.yes_bids else None

    @property
    def best_yes_ask(self) -> float | None:
        return self.yes_asks[0][0] if self.yes_asks else None

    @property
    def mid_yes(self) -> float | None:
        if not self.yes_bids or not self.yes_asks:
            return None
        return (self.yes_bids[0][0] + self.yes_asks[0][0]) / 2.0

    @property
    def spread_yes(self) -> float | None:
        if not self.yes_bids or not self.yes_asks:
            return None
        return self.yes_asks[0][0] - self.yes_bids[0][0]

    def depth(self, levels: int = 5) -> tuple[float, float]:
        if levels <= 0:
            raise ValueError("levels must be positive")
        bid = sum(quantity for _, quantity in self.yes_bids[:levels])
        ask = sum(quantity for _, quantity in self.yes_asks[:levels])
        return bid, ask

    def imbalance(self, levels: int = 5) -> float:
        bid, ask = self.depth(levels)
        total = bid + ask
        return (bid - ask) / total if total > 0 else 0.0

    def key(self) -> str:
        return (
            f"prediction_order_book:{self.venue}:{self.instrument_id}:"
            f"{self.event_time.isoformat()}"
        )


def _validate_prediction_levels(
    levels: tuple[tuple[float, float], ...], *, name: str, descending: bool
) -> None:
    previous: float | None = None
    for price, quantity in levels:
        _probability(price, f"{name} price")
        if quantity <= 0:
            raise ValueError(f"{name} quantities must be positive")
        if previous is not None:
            if descending and price > previous:
                raise ValueError(f"{name} must be sorted best-to-worst")
            if not descending and price < previous:
                raise ValueError(f"{name} must be sorted best-to-worst")
        previous = price


@dataclass(frozen=True, slots=True)
class PredictionMarketInfo:
    """Market rules, status and settlement metadata for a binary contract."""

    market_id: str
    venue: str
    ticker: str
    event_ticker: str
    title: str
    status: PredictionMarketStatus
    provenance: Provenance
    open_time: datetime | None = None
    close_time: datetime | None = None
    expiration_time: datetime | None = None
    settlement_time: datetime | None = None
    updated_time: datetime | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    last_price: float | None = None
    volume: float = 0.0
    open_interest: float = 0.0
    liquidity: float | None = None
    result: PredictionResult = PredictionResult.UNRESOLVED
    settlement_source: str = ""
    rules_primary: str = ""
    rules_secondary: str = ""
    can_close_early: bool = False
    fractional_trading_enabled: bool = False
    extra: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("market_id", self.market_id),
            ("venue", self.venue),
            ("ticker", self.ticker),
            ("event_ticker", self.event_ticker),
            ("title", self.title),
        ):
            if not value or not value.strip():
                raise ValueError(f"prediction market requires {name}")
        if not isinstance(self.status, PredictionMarketStatus):
            raise TypeError("status must be a PredictionMarketStatus")
        if not isinstance(self.result, PredictionResult):
            raise TypeError("result must be a PredictionResult")
        for name, value in (
            ("open_time", self.open_time),
            ("close_time", self.close_time),
            ("expiration_time", self.expiration_time),
            ("settlement_time", self.settlement_time),
            ("updated_time", self.updated_time),
        ):
            _optional_time(value, name)
        for name, value in (
            ("yes_bid", self.yes_bid),
            ("yes_ask", self.yes_ask),
            ("no_bid", self.no_bid),
            ("no_ask", self.no_ask),
            ("last_price", self.last_price),
        ):
            _probability(value, name)
        if self.yes_bid is not None and self.yes_ask is not None and self.yes_bid > self.yes_ask:
            raise ValueError("crossed YES quote in market metadata")
        if self.no_bid is not None and self.no_ask is not None and self.no_bid > self.no_ask:
            raise ValueError("crossed NO quote in market metadata")
        if self.volume < 0 or self.open_interest < 0:
            raise ValueError("volume and open_interest cannot be negative")
        if self.liquidity is not None and self.liquidity < 0:
            raise ValueError("liquidity cannot be negative")
        keys = [key for key, _ in self.extra]
        if len(keys) != len(set(keys)):
            raise ValueError("extra metadata keys must be unique")

    @property
    def is_settled(self) -> bool:
        return self.status is PredictionMarketStatus.SETTLED and (
            self.result is not PredictionResult.UNRESOLVED
        )

    def key(self) -> str:
        return f"prediction_market:{self.venue}:{self.ticker}"


PredictionRecord = PredictionQuote | PredictionTrade | PredictionBar | PredictionOrderBook


def prediction_record_key(record: PredictionRecord) -> str:
    return record.key()


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


def prediction_record_to_dict(record: PredictionRecord) -> dict[str, Any]:
    base: dict[str, Any] = {
        "record_type": record.record_type,
        "instrument_id": record.instrument_id,
        "venue": record.venue,
        "event_time": record.event_time.isoformat(),
        "available_at": record.available_at.isoformat(),
        "provenance": _provenance_to_dict(record.provenance),
    }
    if isinstance(record, PredictionQuote):
        return base | {
            "yes_bid": record.yes_bid,
            "yes_ask": record.yes_ask,
            "no_bid": record.no_bid,
            "no_ask": record.no_ask,
            "last_price": record.last_price,
            "volume": record.volume,
            "open_interest": record.open_interest,
            "status": record.status.value,
        }
    if isinstance(record, PredictionTrade):
        return base | {
            "yes_price": record.yes_price,
            "no_price": record.no_price,
            "quantity": record.quantity,
            "taker_side": record.taker_side.value,
            "trade_id": record.trade_id,
            "sequence": record.sequence,
        }
    if isinstance(record, PredictionBar):
        return base | {
            "open": record.open,
            "high": record.high,
            "low": record.low,
            "close": record.close,
            "volume": record.volume,
        }
    if isinstance(record, PredictionOrderBook):
        return base | {
            "yes_bids": [list(level) for level in record.yes_bids],
            "yes_asks": [list(level) for level in record.yes_asks],
            "no_bids": [list(level) for level in record.no_bids],
            "no_asks": [list(level) for level in record.no_asks],
        }
    raise TypeError(f"unknown prediction record: {type(record)!r}")


def prediction_record_from_dict(data: Mapping[str, Any]) -> PredictionRecord:
    provenance = _provenance_from_dict(data["provenance"])
    common = {
        "instrument_id": str(data["instrument_id"]),
        "venue": str(data["venue"]),
        "event_time": datetime.fromisoformat(str(data["event_time"])),
        "available_at": datetime.fromisoformat(str(data["available_at"])),
        "provenance": provenance,
    }
    kind = data["record_type"]
    if kind == "prediction_quote":
        return PredictionQuote(
            **common,
            yes_bid=float(data["yes_bid"]),
            yes_ask=float(data["yes_ask"]),
            no_bid=float(data["no_bid"]),
            no_ask=float(data["no_ask"]),
            last_price=data.get("last_price"),
            volume=float(data.get("volume", 0.0)),
            open_interest=float(data.get("open_interest", 0.0)),
            status=PredictionMarketStatus(data.get("status", "active")),
        )
    if kind == "prediction_trade":
        return PredictionTrade(
            **common,
            yes_price=float(data["yes_price"]),
            quantity=float(data["quantity"]),
            taker_side=PredictionSide(data["taker_side"]),
            trade_id=str(data["trade_id"]),
            sequence=data.get("sequence"),
        )
    if kind == "prediction_bar":
        return PredictionBar(
            **common,
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            close=float(data["close"]),
            volume=float(data.get("volume", 0.0)),
        )
    if kind == "prediction_order_book":
        return PredictionOrderBook(
            **common,
            yes_bids=tuple((float(price), float(qty)) for price, qty in data["yes_bids"]),
            yes_asks=tuple((float(price), float(qty)) for price, qty in data["yes_asks"]),
            no_bids=tuple((float(price), float(qty)) for price, qty in data["no_bids"]),
            no_asks=tuple((float(price), float(qty)) for price, qty in data["no_asks"]),
        )
    raise ValueError(f"unknown prediction record_type: {kind!r}")


__all__ = [
    "PREDICTION_SCHEMA_VERSION",
    "PredictionBar",
    "PredictionMarketInfo",
    "PredictionMarketStatus",
    "PredictionOrderBook",
    "PredictionQuote",
    "PredictionRecord",
    "PredictionResult",
    "PredictionSide",
    "PredictionTrade",
    "prediction_record_from_dict",
    "prediction_record_key",
    "prediction_record_to_dict",
]
