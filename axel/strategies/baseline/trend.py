"""Baseline trend strategy: dual moving-average, long-only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from axel.data.schemas import BarRecord
from axel.strategies.metadata import StrategyMetadata


@dataclass(frozen=True)
class TrendFollowing:
    """Long while the fast moving average is above the slow one, otherwise flat."""

    short_window: int = 10
    long_window: int = 30

    def __post_init__(self) -> None:
        if self.short_window < 1 or self.long_window < 1:
            raise ValueError("moving-average windows must be positive")
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be smaller than long_window")

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id="baseline.trend",
            name="Dual Moving Average Trend",
            version="1.0",
            description="Long-only dual moving-average trend follower.",
            params=(
                ("short_window", str(self.short_window)),
                ("long_window", str(self.long_window)),
            ),
        )

    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]:
        targets: dict[str, float] = {}
        for symbol, bars in history.items():
            closes = [bar.close for bar in bars]
            if len(closes) < self.long_window:
                targets[symbol] = 0.0
                continue
            fast = sum(closes[-self.short_window :]) / self.short_window
            slow = sum(closes[-self.long_window :]) / self.long_window
            targets[symbol] = 1.0 if fast > slow else 0.0
        return targets