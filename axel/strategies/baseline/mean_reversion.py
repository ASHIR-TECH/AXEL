"""Baseline mean-reversion strategy: contrarian z-score sizing, stateless."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from axel.data.features.base import population_std
from axel.data.schemas import BarRecord
from axel.strategies.metadata import StrategyMetadata


@dataclass(frozen=True)
class MeanReversion:
    """Fade the z-score of price versus its rolling mean, scaled by extremity."""

    window: int = 20
    entry_z: float = 1.5
    deadband_z: float = 0.25

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError("window must be >= 2")
        if self.entry_z <= 0 or self.deadband_z < 0:
            raise ValueError("entry_z must be positive and deadband_z non-negative")
        if self.deadband_z >= self.entry_z:
            raise ValueError("deadband_z must be smaller than entry_z")

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id="baseline.mean_reversion",
            name="Z-Score Mean Reversion",
            version="1.0",
            description="Contrarian z-score reversion with a deadband.",
            params=(
                ("window", str(self.window)),
                ("entry_z", str(self.entry_z)),
                ("deadband_z", str(self.deadband_z)),
            ),
        )

    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]:
        targets: dict[str, float] = {}
        for symbol, bars in history.items():
            closes = [bar.close for bar in bars]
            if len(closes) < self.window:
                targets[symbol] = 0.0
                continue
            window = closes[-self.window :]
            average = sum(window) / self.window
            deviation = population_std(window)
            if deviation == 0:
                targets[symbol] = 0.0
                continue
            z_score = (closes[-1] - average) / deviation
            if abs(z_score) <= self.deadband_z:
                targets[symbol] = 0.0
                continue
            size = min(1.0, abs(z_score) / self.entry_z)
            targets[symbol] = -size if z_score > 0 else size
        return targets