"""Transaction cost models. Simulated PnL is only valid with explicit costs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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


@dataclass(frozen=True)
class FinancingModel:
    """Time-based financing and short-borrow cost.

    Both rates are annualised basis points charged on absolute notional.
    A positive return value means cash leaves the account. Long positions pay
    ``long_financing_bps_per_year`` (margin financing); short positions pay
    ``short_borrow_bps_per_year`` (stock borrow). The model deliberately keeps
    the two rates separate: a broker's borrow fee is not the policy rate.

    Accrual uses calendar time, not trading sessions, so a position held over a
    weekend or holiday still accrues financing.
    """

    long_financing_bps_per_year: float = 0.0
    short_borrow_bps_per_year: float = 0.0
    day_count: int = 365

    def __post_init__(self) -> None:
        if self.long_financing_bps_per_year < 0 or self.short_borrow_bps_per_year < 0:
            raise ValueError("financing rates cannot be negative")
        if self.day_count <= 0:
            raise ValueError("day_count must be positive")

    def annual_rate_bps(self, signed_quantity: float) -> float:
        if signed_quantity < 0:
            return self.short_borrow_bps_per_year
        return self.long_financing_bps_per_year

    def accrual(
        self,
        signed_quantity: float,
        price: float,
        start: datetime,
        end: datetime,
    ) -> float:
        """Cash owed for holding ``signed_quantity`` between ``start`` and ``end``."""
        if signed_quantity == 0 or end <= start or price <= 0:
            return 0.0
        seconds = (end - start).total_seconds()
        years = seconds / (86_400.0 * self.day_count)
        rate_bps = self.annual_rate_bps(signed_quantity)
        return abs(signed_quantity) * price * rate_bps / 10_000.0 * years


NO_FINANCING = FinancingModel()
