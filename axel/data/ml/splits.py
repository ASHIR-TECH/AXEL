"""Split adjustment with explicit, idempotent provenance (no double adjustment)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from axel.data.schemas import BarRecord


@dataclass(frozen=True)
class Split:
    """A ratio of N means a 1 -> N forward split (prices divide, volume multiplies)."""

    symbol: str
    effective_date: date
    ratio: float

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError("split ratio must be positive")
        if not self.symbol:
            raise ValueError("split requires a symbol")


def apply_splits(bars: Sequence[BarRecord], splits: Sequence[Split]) -> list[BarRecord]:
    """Adjust bars for splits effective after each bar's date.

    Idempotent: each applied split is recorded in provenance flags, so re-running
    the function on its own output never applies the same split twice.
    """
    adjusted: list[BarRecord] = []
    for bar in bars:
        applied_flags = {
            flag for flag in bar.provenance.quality_flags if flag.startswith("split:")
        }
        applicable = [
            split
            for split in splits
            if split.symbol == bar.symbol
            and split.effective_date > bar.event_time.date()
            and f"split:{split.effective_date.isoformat()}" not in applied_flags
        ]
        if not applicable:
            adjusted.append(bar)
            continue
        factor = 1.0
        for split in applicable:
            factor *= split.ratio
        new_flags = tuple(f"split:{split.effective_date.isoformat()}" for split in applicable)
        adjusted.append(
            replace(
                bar,
                open=bar.open / factor,
                high=bar.high / factor,
                low=bar.low / factor,
                close=bar.close / factor,
                volume=bar.volume * factor,
                adjustment_status="split_adjusted",
                provenance=bar.provenance.with_flags(*new_flags),
            )
        )
    return sorted(adjusted, key=lambda bar: bar.event_time)
