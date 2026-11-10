"""Read-only Alpaca IEX market data for the paper runtime.

Only the stocks section has a real feed. The other sections stay on the
deterministic synthetic feed, so :func:`trading_sections` narrows the runtime to
stocks whenever the Alpaca feed is selected.

This module only reads market data. It never submits orders and never uses the
execution credential path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.data.providers.alpaca import AlpacaMarketData
from axel.data.providers.keypool import pool_from_pairs
from axel.runtime import audit
from axel.runtime.paper_feed import SECTION_SYMBOLS, SyntheticBar, persist_bars

STOCK_SYMBOLS: tuple[str, ...] = SECTION_SYMBOLS["STOCKS"]
BAR_INTERVAL_SECONDS = 60
DEFAULT_LOOKBACK_MINUTES = 10


def provider() -> AlpacaMarketData:
    """Build the read-only market-data client from the configured key pool."""
    pairs = settings.alpaca_credentials()
    if not pairs:
        raise RuntimeError(
            "paper_data_source=alpaca requires ALPACA_API_KEY and ALPACA_SECRET_KEY"
        )
    return AlpacaMarketData(pool=pool_from_pairs(pairs))


def _to_synthetic(records: Any) -> list[SyntheticBar]:
    """Convert canonical Alpaca bar records into the runtime's bar shape."""
    bars: list[SyntheticBar] = []
    for record in records:
        timestamp = record.event_time
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        bars.append(
            SyntheticBar(
                symbol=str(record.symbol).upper(),
                timestamp=timestamp,
                open=float(record.open),
                high=float(record.high),
                low=float(record.low),
                close=float(record.close),
                volume=float(record.volume),
            )
        )
    return bars


def fetch_stock_bars(
    now: datetime,
    *,
    lookback: timedelta = timedelta(minutes=DEFAULT_LOOKBACK_MINUTES),
    market_data: AlpacaMarketData | None = None,
) -> list[SyntheticBar]:
    """Fetch recent one-minute bars for every stock symbol."""
    client = market_data or provider()
    start = now - lookback
    bars: list[SyntheticBar] = []
    for symbol in STOCK_SYMBOLS:
        result = client.fetch_bars(symbol, start=start, end=now, timeframe="1Min")
        bars.extend(_to_synthetic(result.records))
    return bars


def ingest_alpaca_once(
    session: Session,
    *,
    now: datetime | None = None,
    market_data: AlpacaMarketData | None = None,
) -> dict[str, int]:
    """Persist the newest real Alpaca bars. Idempotent per (symbol, minute)."""
    stamp = now or datetime.now(UTC)
    bars = fetch_stock_bars(stamp, market_data=market_data)
    written = persist_bars(session, bars, interval_seconds=BAR_INTERVAL_SECONDS)
    audit.record(
        session,
        event_type="market.ingest",
        actor="axel.data.ingest",
        message=(
            f"alpaca ingest · {written} bars · {len(STOCK_SYMBOLS)} symbols · "
            f"venue=alpaca-iex"
        ),
        logger="axel.data.ingest",
        channel="SYS",
        level="INFO",
        timestamp=stamp,
        symbols=written,
        macro_records=0,
        source="alpaca",
    )
    return {"bars": written, "news": 0}


__all__ = [
    "BAR_INTERVAL_SECONDS",
    "DEFAULT_LOOKBACK_MINUTES",
    "STOCK_SYMBOLS",
    "fetch_stock_bars",
    "ingest_alpaca_once",
    "provider",
]
