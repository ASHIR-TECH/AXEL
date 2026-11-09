"""Deterministic synthetic market feed for the paper runtime.

The paper runtime must produce durable, reproducible activity without any
external credentials.  This module generates a random-walk OHLCV bar per
symbol, seeded from ``(seed, symbol, time bucket)`` so that a worker restart in
the same bucket cannot create a different price for the same timestamp.

It contains no execution logic: bars are data, not orders.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from axel.db.models import OhlcvBar


@dataclass(frozen=True)
class InstrumentSpec:
    """Static characteristics used to generate a believable paper market."""

    symbol: str
    section: str
    base_price: float
    bar_volatility: float
    drift: float
    volume: float


# Mirrors ``axel/dashboard/ui/js/config.js`` so every terminal panel has data.
SECTION_SYMBOLS: dict[str, tuple[str, ...]] = {
    "STOCKS": ("AAPL", "NVDA", "MSFT", "TSLA", "XOM", "JPM", "META", "AVGO", "LLY", "CAT"),
    "OPTIONS": ("SPY 26SEP550C", "QQQ 02OCT480P", "SPY 26SEP600C", "IWM 2200C", "NVDA 1200P"),
    "CRYPTO": ("BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD"),
    "FOREX": ("EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD"),
    "PREDICTIONS": ("KAL:FED-CUT", "KAL:NBA-BOS", "KAL:BTC>150K", "KAL:CPI-OCT", "KAL:SFR-DEC"),
}

_SECTION_DEFAULTS: dict[str, tuple[float, float, float]] = {
    # section: (base price, per-bar volatility, drift per bar)
    "STOCKS": (120.0, 0.0016, 0.00002),
    "OPTIONS": (6.5, 0.0100, 0.00000),
    "CRYPTO": (45_000.0, 0.0030, 0.00005),
    "FOREX": (1.08, 0.0009, 0.00000),
    "PREDICTIONS": (0.55, 0.0050, 0.00000),
}

_SYMBOL_PRICE_OVERRIDES: dict[str, float] = {
    "AAPL": 229.0,
    "NVDA": 138.0,
    "MSFT": 421.0,
    "TSLA": 248.0,
    "XOM": 118.0,
    "JPM": 236.0,
    "META": 585.0,
    "AVGO": 172.0,
    "LLY": 802.0,
    "CAT": 388.0,
    "SPY 26SEP550C": 12.4,
    "QQQ 02OCT480P": 8.1,
    "SPY 26SEP600C": 4.6,
    "IWM 2200C": 3.2,
    "NVDA 1200P": 9.8,
    "BTC-USD": 68_400.0,
    "ETH-USD": 3_250.0,
    "SOL-USD": 168.0,
    "AVAX-USD": 37.0,
    "LINK-USD": 17.0,
    "EURUSD": 1.08,
    "USDJPY": 151.2,
    "GBPUSD": 1.27,
    "AUDUSD": 0.66,
    "USDCAD": 1.36,
}

_VOLUME_OVERRIDES: dict[str, float] = {
    "BTC-USD": 120.0,
    "ETH-USD": 2_400.0,
    "SOL-USD": 18_000.0,
}


def instrument_spec(symbol: str) -> InstrumentSpec:
    """Return the deterministic generation parameters for one symbol."""
    for section, symbols in SECTION_SYMBOLS.items():
        if symbol in symbols:
            base, volatility, drift = _SECTION_DEFAULTS[section]
            return InstrumentSpec(
                symbol=symbol,
                section=section,
                base_price=_SYMBOL_PRICE_OVERRIDES.get(symbol, base),
                bar_volatility=volatility,
                drift=drift,
                volume=_VOLUME_OVERRIDES.get(symbol, 25_000.0),
            )
    raise KeyError(f"unknown paper symbol: {symbol}")


def all_symbols() -> tuple[str, ...]:
    return tuple(symbol for symbols in SECTION_SYMBOLS.values() for symbol in symbols)


@dataclass(frozen=True)
class SyntheticBar:
    """One generated OHLCV bar, ready to persist."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def latest_bar(session: Session, symbol: str) -> OhlcvBar | None:
    stmt = (
        select(OhlcvBar)
        .where(OhlcvBar.symbol == symbol)
        .order_by(OhlcvBar.timestamp.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def latest_price(session: Session, symbol: str) -> float | None:
    bar = latest_bar(session, symbol)
    return float(bar.close) if bar is not None else None


def latest_prices(session: Session, symbols: tuple[str, ...] | list[str]) -> dict[str, float]:
    """Return the most recent close for each symbol in a single query.

    The ingest worker needs the previous close for every symbol before it can
    generate the next bar. Querying one symbol at a time turns a 30-symbol
    cycle into 30 round trips; this uses a window function so the composite
    ``(symbol, timestamp)`` index is walked once per partition.
    """
    wanted = list(dict.fromkeys(symbols))
    if not wanted:
        return {}
    ranked = (
        select(
            OhlcvBar.symbol.label("symbol"),
            OhlcvBar.close.label("close"),
            func.row_number()
            .over(partition_by=OhlcvBar.symbol, order_by=OhlcvBar.timestamp.desc())
            .label("rank"),
        )
        .where(OhlcvBar.symbol.in_(wanted))
        .subquery()
    )
    stmt = select(ranked.c.symbol, ranked.c.close).where(ranked.c.rank == 1)
    return {str(symbol): float(close) for symbol, close in session.execute(stmt)}


def recent_closes(session: Session, symbol: str, limit: int = 40) -> list[float]:
    stmt = (
        select(OhlcvBar.close)
        .where(OhlcvBar.symbol == symbol)
        .order_by(OhlcvBar.timestamp.desc())
        .limit(limit)
    )
    rows = [float(value) for value in session.execute(stmt).scalars()]
    rows.reverse()
    return rows


_UNSET: object = object()


def next_bar(
    session: Session | None,
    symbol: str,
    timestamp: datetime,
    *,
    interval_seconds: float,
    seed: int,
    previous_price: float | None | object = _UNSET,
) -> SyntheticBar:
    """Generate the next bar from the last persisted close.

    The RNG is seeded by ``(seed, symbol, timestamp bucket)`` so repeated calls
    in the same bucket are idempotent and a restart cannot invent a different
    price for an already-recorded timestamp.

    ``previous_price`` lets the ingest worker pass the close it already fetched
    in a batch, avoiding one ``SELECT`` per symbol per cycle. When omitted the
    latest bar is read from the database.
    """
    spec = instrument_spec(symbol)
    if previous_price is _UNSET:
        previous = latest_price(session, symbol) if session is not None else None
    else:
        previous = previous_price
    open_price = previous if isinstance(previous, (int, float)) else spec.base_price

    bucket = int(timestamp.timestamp() // max(0.001, interval_seconds))
    rng = random.Random(f"{seed}:{symbol}:{bucket}")
    shock = rng.gauss(0.0, 1.0)
    # Clamp fat tails so a paper run cannot generate absurd marks.
    shock = max(-4.0, min(4.0, shock))
    change = math.exp(spec.drift + spec.bar_volatility * shock) - 1.0
    close = max(0.01, open_price * (1.0 + change))
    spread = abs(change) + spec.bar_volatility * 0.5
    high = max(open_price, close) * (1.0 + spread * 0.6)
    low = min(open_price, close) * (1.0 - spread * 0.6)
    volume = spec.volume * (0.6 + rng.random() * 0.8)
    return SyntheticBar(
        symbol=symbol,
        timestamp=timestamp,
        open=round(open_price, 6),
        high=round(high, 6),
        low=round(max(0.01, low), 6),
        close=round(close, 6),
        volume=round(volume, 2),
    )


def bar_id(symbol: str, timestamp: datetime, interval_seconds: float) -> str:
    bucket = int(timestamp.timestamp() // max(0.001, interval_seconds))
    safe = symbol.replace(" ", "_").replace(":", "_").replace(">", "_")
    return f"bar_{safe}_{bucket}"


def _bar_values(
    bar: SyntheticBar, interval_seconds: float, *, ingested_at: datetime
) -> dict[str, object]:
    return {
        "id": bar_id(bar.symbol, bar.timestamp, interval_seconds),
        "symbol": bar.symbol,
        "timestamp": bar.timestamp,
        "ingestion_time": ingested_at,
        "timeframe": "1m",
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "vwap": round((bar.high + bar.low + bar.close) / 3.0, 6),
    }


def _idempotent_insert(dialect_name: str):
    """Return a dialect INSERT ... ON CONFLICT DO NOTHING, or None if unsupported.

    Both PostgreSQL and SQLite support the clause, so the hot path never has to
    issue a ``SELECT`` before every insert. The fallback keeps other dialects
    (and exotic test engines) working.
    """
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        return None
    return insert(OhlcvBar).on_conflict_do_nothing(index_elements=["id"])


def persist_bars(
    session: Session, bars: list[SyntheticBar], *, interval_seconds: float
) -> int:
    """Insert many bars in one round trip, ignoring already-stored buckets."""
    if not bars:
        return 0
    ingested_at = datetime.now(UTC)
    rows = [_bar_values(bar, interval_seconds, ingested_at=ingested_at) for bar in bars]
    statement = _idempotent_insert(session.get_bind().dialect.name)
    if statement is None:
        for row in rows:
            if session.get(OhlcvBar, row["id"]) is None:
                session.add(OhlcvBar(**row))
        return len(rows)
    session.execute(statement, rows)
    return len(rows)


def persist_bar(session: Session, bar: SyntheticBar, *, interval_seconds: float) -> bool:
    """Insert one bar idempotently; returns True when the row was newly written."""
    ingested_at = datetime.now(UTC)
    row = _bar_values(bar, interval_seconds, ingested_at=ingested_at)
    statement = _idempotent_insert(session.get_bind().dialect.name)
    if statement is None:
        if session.get(OhlcvBar, row["id"]) is not None:
            return False
        session.add(OhlcvBar(**row))
        return True
    result = session.execute(statement.values(**row))
    return (result.rowcount or 0) > 0


__all__ = [
    "SECTION_SYMBOLS",
    "InstrumentSpec",
    "SyntheticBar",
    "all_symbols",
    "bar_id",
    "instrument_spec",
    "latest_bar",
    "latest_price",
    "latest_prices",
    "next_bar",
    "persist_bar",
    "persist_bars",
    "recent_closes",
]
