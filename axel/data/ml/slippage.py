"""Slippage models applied at fill time (side-aware: +1 buy, -1 sell)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from axel.data.schemas import BarRecord


class SlippageModel(Protocol):
    def adjust(self, price: float, quantity: float, *, side: int, bar: BarRecord) -> float: ...


@dataclass(frozen=True)
class NoSlippage:
    def adjust(self, price: float, quantity: float, *, side: int, bar: BarRecord) -> float:
        return price


@dataclass(frozen=True)
class FixedBpsSlippage:
    bps: float = 1.0

    def __post_init__(self) -> None:
        if self.bps < 0:
            raise ValueError("bps cannot be negative")

    def adjust(self, price: float, quantity: float, *, side: int, bar: BarRecord) -> float:
        if side == 0:
            return price
        return price * (1.0 + side * self.bps / 10_000.0)


@dataclass(frozen=True)
class VolumeImpactSlippage:
    """Square-root market-impact model using the bar's volume as liquidity."""

    coefficient: float = 0.1

    def __post_init__(self) -> None:
        if self.coefficient < 0:
            raise ValueError("coefficient cannot be negative")

    def adjust(self, price: float, quantity: float, *, side: int, bar: BarRecord) -> float:
        if side == 0 or bar.volume <= 0:
            return price
        participation = abs(quantity) / bar.volume
        impact = self.coefficient * participation**0.5
        return price * (1.0 + side * impact)


NO_SLIPPAGE = NoSlippage()
