"""Immutable data records used by ingestion and backtesting."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "1d"

    def __post_init__(self) -> None:
        if not self.symbol or self.timestamp.tzinfo is None:
            raise ValueError("bars require a symbol and timezone-aware timestamp")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.low > self.high:
            raise ValueError("bar prices must be positive and low <= high")
        if self.volume < 0:
            raise ValueError("bar volume cannot be negative")
