"""Fetch historical bars from a read-only provider and write the training CSV.

The provider is injected, so this module never imports credentials and can be
tested with a stub. ``AlpacaMarketData`` satisfies the expected interface.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from axel.data.export import write_bars_csv
from axel.data.models import Bar


class HistoricalProvider(Protocol):
    def bars(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = ...
    ) -> list[Bar]: ...


def fetch_historical_bars(
    provider: HistoricalProvider,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    *,
    timeframe: str = "1Hour",
) -> list[Bar]:
    """Fetch one symbol at a time and return a deterministically ordered list."""
    if end <= start:
        raise ValueError("end must be after start")
    bars: list[Bar] = []
    for symbol in symbols:
        bars.extend(provider.bars(symbol, start, end, timeframe=timeframe))
    return sorted(bars, key=lambda bar: (bar.symbol, bar.timestamp))


def fetch_to_csv(
    provider: HistoricalProvider,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    destination: Path,
    *,
    timeframe: str = "1Hour",
) -> tuple[int, str]:
    """Fetch and write a CSV; return ``(row_count, content_sha256)``."""
    bars = fetch_historical_bars(provider, symbols, start, end, timeframe=timeframe)
    content_hash = write_bars_csv(bars, destination)
    return len(bars), content_hash


__all__ = ["HistoricalProvider", "fetch_historical_bars", "fetch_to_csv"]
