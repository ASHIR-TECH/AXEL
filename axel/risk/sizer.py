"""
Deterministic position sizer implementing fractional Kelly Criterion with multi-tier risk caps.
"""

from dataclasses import dataclass
import math
from typing import Literal

from axel.risk.limits import LIMITS, RiskLimits


@dataclass(frozen=True)
class SizingResult:
    approved_qty: float
    notional_usd: float
    fractional_kelly_f: float
    raw_kelly_f_star: float
    binding_limit: str
    max_loss_at_stop_usd: float


class KellySizer:
    """
    Computes position sizes using fractional Kelly criterion:
    f* = (b * p - q) / b
    where:
      p = win probability
      q = 1 - p (loss probability)
      b = average win / loss ratio

    Enforces:
    1. Fractional scaling (default 0.25 * f*).
    2. Notional portfolio cap (default max 5% of section NAV).
    3. Risk-at-stop cap (default max 1% of section NAV lost if stop is triggered).
    4. Conservative haircut on historical win rate.
    """

    def __init__(self, limits: RiskLimits = LIMITS):
        self.limits = limits

    def compute_size(
        self,
        nav: float,
        entry_price: float,
        stop_loss: float,
        win_rate: float,
        avg_win_loss_ratio: float,
        haircut: float = 0.05,
        asset_type: Literal["equity", "crypto"] = "equity",
    ) -> SizingResult:
        """
        Pure function: calculates compliant position size without side effects.
        """
        if nav <= 0.0 or entry_price <= 0.0 or stop_loss <= 0.0:
            return SizingResult(0.0, 0.0, 0.0, 0.0, "INVALID_INPUTS", 0.0)

        # Stop distance verification
        stop_distance = abs(entry_price - stop_loss)
        stop_distance_pct = stop_distance / entry_price
        if stop_distance_pct < 1e-4:
            return SizingResult(0.0, 0.0, 0.0, 0.0, "STOP_TOO_CLOSE", 0.0)

        # Apply conservative haircut to historical win rate
        p = max(0.0, min(1.0, win_rate - haircut))
        q = 1.0 - p
        b = max(1e-4, avg_win_loss_ratio)

        edge = (b * p) - q
        if edge <= 0.0:
            # Negative or zero expectancy -> DO NOT ALLOCATE
            return SizingResult(
                approved_qty=0.0,
                notional_usd=0.0,
                fractional_kelly_f=0.0,
                raw_kelly_f_star=0.0,
                binding_limit="ZERO_OR_NEGATIVE_EDGE",
                max_loss_at_stop_usd=0.0,
            )

        f_star = edge / b
        f_fractional = min(f_star, 1.0) * self.limits.KELLY_CAP

        # 1. Kelly Notional
        kelly_notional = nav * f_fractional

        # 2. Portfolio Notional Cap (5% of NAV)
        notional_cap = nav * self.limits.MAX_POSITION_PCT

        # 3. Risk-at-stop cap (1% of NAV max loss at stop)
        max_loss_allowed = nav * self.limits.MAX_PER_TRADE_RISK_PCT
        risk_at_stop_notional = max_loss_allowed / stop_distance_pct

        # Determine binding limit
        candidates = [
            ("KELLY_FRACTION", kelly_notional),
            ("NOTIONAL_CAP_5PCT", notional_cap),
            ("RISK_AT_STOP_CAP", risk_at_stop_notional),
        ]
        binding_name, approved_notional = min(candidates, key=lambda item: item[1])

        # Floor at zero
        approved_notional = max(0.0, approved_notional)

        # Compute units
        if asset_type == "equity":
            # Whole shares for equities
            qty = float(math.floor(approved_notional / entry_price))
            final_notional = qty * entry_price
        else:
            # Fractional precision for crypto
            qty = approved_notional / entry_price
            final_notional = approved_notional

        actual_risk_at_stop = qty * stop_distance

        return SizingResult(
            approved_qty=qty,
            notional_usd=round(final_notional, 2),
            fractional_kelly_f=round(f_fractional, 4),
            raw_kelly_f_star=round(f_star, 4),
            binding_limit=binding_name,
            max_loss_at_stop_usd=round(actual_risk_at_stop, 2),
        )
