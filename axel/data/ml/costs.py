"""Transaction cost models. Simulated PnL is only valid with explicit costs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Linear commission with an optional floor, in basis points of notional."""

    commission_bps: float = 1.0
    min_commission: float = 0.0

    def __post_init__(self) -> None:
        if self.commission_bps < 0 or self.min_commission < 0:
            raise ValueError("cost parameters cannot be negative")

    def commission(self, notional: float) -> float:
        return max(self.min_commission, abs(notional) * self.commission_bps / 10_000.0)


ZERO_COSTS = CostModel(commission_bps=0.0, min_commission=0.0)
