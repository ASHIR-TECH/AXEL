"""
Daily loss monitoring and section stop-loss enforcement.
"""

from dataclasses import dataclass

from axel.core.types import Section
from axel.risk.limits import LIMITS, RiskLimits


@dataclass(frozen=True)
class DailyPnLState:
    section: Section
    start_of_day_equity: float
    realized_pnl: float
    unrealized_pnl: float


@dataclass(frozen=True)
class LossEvaluationResult:
    is_breached: bool
    daily_pnl_pct: float
    stop_threshold_pct: float
    action: str  # "ALLOW", "BLOCK_NEW_ENTRIES", "CANCEL_AND_FLATTEN"
    reason: str | None = None


class LossManager:
    """
    Evaluates section daily performance against hardcoded stop limits.
    Enforces a strict 3% daily drawdown threshold on combined realized and unrealized P&L.
    """

    def __init__(self, limits: RiskLimits = LIMITS):
        self.limits = limits

    def evaluate_daily_stop(self, state: DailyPnLState) -> LossEvaluationResult:
        """Pure function: evaluates daily stop status."""
        if state.start_of_day_equity <= 0.0:
            return LossEvaluationResult(
                is_breached=True,
                daily_pnl_pct=-1.0,
                stop_threshold_pct=-self.limits.SECTION_DAILY_STOP,
                action="BLOCK_NEW_ENTRIES",
                reason="Invalid or zero start-of-day equity",
            )

        total_daily_pnl = state.realized_pnl + state.unrealized_pnl
        daily_pnl_pct = total_daily_pnl / state.start_of_day_equity
        threshold = -abs(self.limits.SECTION_DAILY_STOP)

        if daily_pnl_pct <= threshold:
            return LossEvaluationResult(
                is_breached=True,
                daily_pnl_pct=round(daily_pnl_pct, 4),
                stop_threshold_pct=threshold,
                action="BLOCK_NEW_ENTRIES",
                reason=(
                    f"SECTION DAILY STOP BREACHED: PnL {daily_pnl_pct:.2%} <= threshold {threshold:.2%}. "
                    f"Section '{state.section.value}' blocked from opening new positions."
                ),
            )

        return LossEvaluationResult(
            is_breached=False,
            daily_pnl_pct=round(daily_pnl_pct, 4),
            stop_threshold_pct=threshold,
            action="ALLOW",
            reason=None,
        )
