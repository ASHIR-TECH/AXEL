"""
Deterministic SectionRiskAgent engine.
Pure-function evaluation gate: zero LLM dependencies, 100% testable.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from axel.core.contracts import Proposal, RiskDecision
from axel.core.types import OrderSide, RiskVerdict, Section
from axel.risk.limits import LIMITS, RiskLimits
from axel.risk.loss_manager import DailyPnLState, LossManager
from axel.risk.sizer import KellySizer, SizingResult


@dataclass(frozen=True)
class AccountState:
    """Snapshot of overall account equity and safety states."""
    total_equity: float
    cash: float
    global_drawdown_pct: float
    kill_switch_active: bool = False


@dataclass(frozen=True)
class SectionRiskState:
    """Snapshot of section-specific capital, PnL, and active trades."""
    section: Section
    section_nav: float
    start_of_day_equity: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    active_positions_count: int = 0


@dataclass(frozen=True)
class OpenPositionInfo:
    """Information about an active position for correlation and concentration checks."""
    symbol: str
    section: Section
    side: OrderSide
    notional_usd: float
    correlation_with_proposal: float = 0.0


class SectionRiskAgent:
    """
    The deterministic gatekeeper for AXEL.
    Every trade proposal MUST pass through evaluate() before reaching execution.
    Contains no side effects or I/O.
    """

    def __init__(self, limits: RiskLimits = LIMITS):
        self.limits = limits
        self.sizer = KellySizer(limits)
        self.loss_manager = LossManager(limits)

    def evaluate(
        self,
        proposal: Proposal,
        account_state: AccountState,
        section_state: SectionRiskState,
        open_positions: Optional[List[OpenPositionInfo]] = None,
    ) -> RiskDecision:
        """
        Pure function: evaluates all risk rules against the incoming proposal.
        """
        checks_passed: List[str] = []
        checks_failed: List[str] = []
        reasons: List[str] = []
        open_positions = open_positions or []

        # 1. Global Kill-Switch Check (Absolute highest priority)
        if account_state.kill_switch_active or account_state.global_drawdown_pct >= self.limits.GLOBAL_MAX_DRAWDOWN:
            reason = (
                f"GLOBAL_KILL_SWITCH_ACTIVE: Global drawdown "
                f"{account_state.global_drawdown_pct:.2%} >= limit {self.limits.GLOBAL_MAX_DRAWDOWN:.2%}"
            )
            return RiskDecision(
                proposal_id=proposal.id,
                approved=False,
                verdict=RiskVerdict.HALTED,
                approved_qty=0.0,
                approved_notional_usd=0.0,
                binding_limit="GLOBAL_KILL_SWITCH",
                reasons=[reason],
                checks_passed=checks_passed,
                checks_failed=["GLOBAL_KILL_SWITCH"],
            )
        checks_passed.append("GLOBAL_KILL_SWITCH_CLEAR")

        # 2. Section Daily Stop-Loss Check
        daily_state = DailyPnLState(
            section=section_state.section,
            start_of_day_equity=section_state.start_of_day_equity,
            realized_pnl=section_state.realized_pnl,
            unrealized_pnl=section_state.unrealized_pnl,
        )
        loss_eval = self.loss_manager.evaluate_daily_stop(daily_state)
        if loss_eval.is_breached:
            checks_failed.append("SECTION_DAILY_STOP_BREACHED")
            reasons.append(loss_eval.reason or "Daily stop breached")
        else:
            checks_passed.append("SECTION_DAILY_STOP_CLEAR")

        # 3. Minimum Panel Confidence Threshold
        if proposal.panel_confidence < self.limits.MIN_PANEL_CONFIDENCE:
            checks_failed.append("CONFIDENCE_BELOW_THRESHOLD")
            reasons.append(
                f"Panel confidence {proposal.panel_confidence:.2f} < minimum required {self.limits.MIN_PANEL_CONFIDENCE:.2f}"
            )
        else:
            checks_passed.append("PANEL_CONFIDENCE_SUFFICIENT")

        # 4. Maximum Concurrent Positions in Section
        if section_state.active_positions_count >= self.limits.MAX_OPEN_POSITIONS_PER_SECTION:
            checks_failed.append("MAX_CONCURRENT_POSITIONS_REACHED")
            reasons.append(
                f"Section {section_state.section.value} has {section_state.active_positions_count} positions (max {self.limits.MAX_OPEN_POSITIONS_PER_SECTION})"
            )
        else:
            checks_passed.append("CONCURRENT_POSITIONS_CAP_CLEAR")

        # 5. Risk / Reward Ratio Check
        if proposal.side == OrderSide.BUY:
            risk_per_share = proposal.entry - proposal.stop
            reward_per_share = proposal.target - proposal.entry
        else:
            risk_per_share = proposal.stop - proposal.entry
            reward_per_share = proposal.entry - proposal.target

        if risk_per_share <= 0:
            checks_failed.append("INVALID_STOP_PLACEMENT")
            reasons.append(f"Risk per share is non-positive: {risk_per_share}")
            rr_ratio = 0.0
        else:
            rr_ratio = reward_per_share / risk_per_share

        if rr_ratio < self.limits.MIN_RR_RATIO:
            checks_failed.append("RR_RATIO_TOO_LOW")
            reasons.append(f"Risk/Reward ratio {rr_ratio:.2f} < minimum required {self.limits.MIN_RR_RATIO:.2f}")
        else:
            checks_passed.append("RR_RATIO_VALID")

        # 6. Overfitting Sanity Gate (>4R flagged as suspect)
        if rr_ratio > self.limits.MAX_EXPECTED_R:
            checks_failed.append("R_TARGET_OVERFITTING_FLAG")
            reasons.append(
                f"Risk/Reward target {rr_ratio:.2f}R > maximum believable {self.limits.MAX_EXPECTED_R:.2f}R. Flagged as likely overfit or data leakage."
            )
        else:
            checks_passed.append("R_TARGET_SANITY_CLEAR")

        # 7. Correlation Concentration Check
        for active_pos in open_positions:
            if active_pos.correlation_with_proposal >= self.limits.CORRELATION_BLOCK_THRESHOLD:
                checks_failed.append("CORRELATION_LIMIT_BREACH")
                reasons.append(
                    f"Correlation with active position '{active_pos.symbol}' is {active_pos.correlation_with_proposal:.2f} >= threshold {self.limits.CORRELATION_BLOCK_THRESHOLD:.2f}"
                )
                break
        else:
            checks_passed.append("CORRELATION_CHECK_CLEAR")

        # 8. Deterministic Kelly Sizing
        asset_type = "crypto" if proposal.section == Section.CRYPTO else "equity"
        sizing: SizingResult = self.sizer.compute_size(
            nav=section_state.section_nav,
            entry_price=proposal.entry,
            stop_loss=proposal.stop,
            win_rate=proposal.win_rate,
            avg_win_loss_ratio=proposal.avg_win_loss_ratio,
            haircut=0.05,
            asset_type=asset_type,
        )

        if sizing.approved_qty <= 0:
            checks_failed.append("SIZING_ZERO_QTY")
            reasons.append(f"Position sizer assigned zero quantity (binding limit: {sizing.binding_limit})")
        else:
            checks_passed.append("POSITION_SIZING_APPROVED")

        # Final Verdict Decision
        if checks_failed:
            return RiskDecision(
                proposal_id=proposal.id,
                approved=False,
                verdict=RiskVerdict.REJECTED,
                approved_qty=0.0,
                approved_notional_usd=0.0,
                binding_limit=checks_failed[0],
                reasons=reasons,
                checks_passed=checks_passed,
                checks_failed=checks_failed,
            )

        return RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            verdict=RiskVerdict.APPROVED,
            approved_qty=sizing.approved_qty,
            approved_notional_usd=sizing.notional_usd,
            binding_limit=sizing.binding_limit,
            reasons=["All deterministic risk and sizing checks passed"],
            checks_passed=checks_passed,
            checks_failed=[],
        )
