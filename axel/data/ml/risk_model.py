"""
Risk models that define the denominator of an R-multiple.

The old backtester derived risk as ``equity * risk_per_trade``, a fixed
percentage of notional. That makes R depend on account size rather than on the
trade, and it is the pattern the quant research pack explicitly rejects (no
universal 1%/2% risk; risk must follow volatility, stop distance and exposure).

Every model here returns the *loss at the risk unit*, scaled by the position, so
``pnl / risk`` is comparable across trades and account sizes:

* ``StopDistanceRiskModel`` -- matches the live ``KellySizer`` convention:
  risk = quantity x stop distance.
* ``VolatilityRiskModel`` -- no stop available: risk = quantity x price x
  realised volatility, i.e. a one-sigma adverse move.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from axel.data.schemas import BarRecord
from axel.util.stats import log_returns, sample_std

MIN_HISTORY = 2


class RiskModel(Protocol):
    """Loss at the risk unit for a position, in account currency."""

    def risk(
        self,
        *,
        symbol: str,
        quantity: float,
        price: float,
        history: Sequence[BarRecord],
    ) -> float: ...


def realized_volatility(history: Sequence[BarRecord], lookback: int = 20) -> float:
    """Std-dev of log returns over the last ``lookback`` bars (0.0 if unknown)."""
    closes = [bar.close for bar in history[-lookback:]]
    series = log_returns(closes)
    if len(series) < MIN_HISTORY:
        return 0.0
    return sample_std(series)


@dataclass(frozen=True)
class VolatilityRiskModel:
    """Risk = notional x realised volatility x sqrt(horizon)."""

    lookback: int = 20
    horizon_bars: int = 1
    floor_vol: float = 1e-4

    def __post_init__(self) -> None:
        if self.lookback < MIN_HISTORY:
            raise ValueError("lookback must allow at least two returns")
        if self.horizon_bars <= 0:
            raise ValueError("horizon_bars must be positive")
        if self.floor_vol < 0:
            raise ValueError("floor_vol cannot be negative")

    def risk(
        self,
        *,
        symbol: str,
        quantity: float,
        price: float,
        history: Sequence[BarRecord],
    ) -> float:
        if quantity == 0 or price <= 0:
            return 0.0
        volatility = max(
            realized_volatility(history, self.lookback), self.floor_vol
        )
        return abs(quantity) * price * volatility * self.horizon_bars**0.5


@dataclass(frozen=True)
class StopDistanceRiskModel:
    """Risk = quantity x stop distance (the KellySizer convention)."""

    stop_fraction: float
    max_lookback: int = 0

    def __post_init__(self) -> None:
        if self.stop_fraction <= 0:
            raise ValueError("stop_fraction must be positive")

    def risk(
        self,
        *,
        symbol: str,
        quantity: float,
        price: float,
        history: Sequence[BarRecord],
    ) -> float:
        if quantity == 0 or price <= 0:
            return 0.0
        return abs(quantity) * price * self.stop_fraction


@dataclass(frozen=True)
class FixedFractionalRiskModel:
    """Legacy escape hatch: risk = notional x fraction. Prefer the above."""

    fraction: float

    def __post_init__(self) -> None:
        if self.fraction < 0:
            raise ValueError("fraction cannot be negative")

    def risk(
        self,
        *,
        symbol: str,
        quantity: float,
        price: float,
        history: Sequence[BarRecord],
    ) -> float:
        return abs(quantity) * price * self.fraction


__all__ = [
    "FixedFractionalRiskModel",
    "RiskModel",
    "StopDistanceRiskModel",
    "VolatilityRiskModel",
    "realized_volatility",
]