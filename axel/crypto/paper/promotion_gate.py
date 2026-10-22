"""
Crypto Promotion Gate for Phase 7 (Crypto).

Strict, deterministic evaluation of all 10 promotion criteria specified in the PRD.
Promotion is strategy- and venue-specific.
Fails closed if any criterion is unmet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class CryptoPromotionEvidence:
    """Evidence bundle submitted for strategy/venue live promotion."""

    strategy_id: str
    venue: str
    oos_backtest_passed: bool
    oos_sharpe: float
    trade_count: int
    parameter_robustness_passed: bool  # +/-20% param perturbation keeps positive expectancy
    no_leakage_audit_passed: bool      # available_at >= event_time proven
    paper_trade_count: int
    paper_net_edge_positive: bool
    arbitrage_leg_completion_rate: float
    unresolved_reconciliation_breaks: int
    venue_uptime_pct: float
    venue_avg_latency_ms: float
    no_leverage_verified: bool
    rolling_24h_utc_stop_passed: bool
    killswitch_watchdog_passed: bool
    human_signoff_recorded: bool
    human_reviewer: str | None = None
    signoff_notes: str | None = None


@dataclass(frozen=True)
class CryptoPromotionDecision:
    """Outcome of promotion gate evaluation."""

    promoted: bool
    strategy_id: str
    venue: str
    checks_passed: tuple[str, ...]
    checks_failed: tuple[str, ...]
    reasons: tuple[str, ...]
    evaluated_at: datetime


class CryptoPromotionGate:
    """
    Deterministic gatekeeper for promoting a Crypto strategy/venue pair.
    """

    MIN_BACKTEST_TRADES = 100
    MIN_OOS_SHARPE = 0.70
    MIN_PAPER_TRADES = 50
    MIN_LEG_COMPLETION_RATE = 0.95     # >=95% of 2-leg arbitrage orders filled on both sides
    MIN_VENUE_UPTIME = 0.99            # >=99% uptime
    MAX_VENUE_LATENCY_MS = 250.0

    @classmethod
    def evaluate(
        cls,
        evidence: CryptoPromotionEvidence,
        now: datetime | None = None,
    ) -> CryptoPromotionDecision:
        now = now or datetime.now(UTC)
        passed: list[str] = []
        failed: list[str] = []
        reasons: list[str] = []

        # 1. Realistic-cost OOS backtest pass
        if evidence.oos_backtest_passed and evidence.oos_sharpe >= cls.MIN_OOS_SHARPE:
            passed.append("OOS_BACKTEST_PASSED")
        else:
            failed.append("OOS_BACKTEST_FAILED")
            reasons.append(f"OOS Sharpe {evidence.oos_sharpe:.2f} < required {cls.MIN_OOS_SHARPE:.2f}")

        # 2. Trade count and robustness requirements
        if evidence.trade_count >= cls.MIN_BACKTEST_TRADES and evidence.parameter_robustness_passed:
            passed.append("ROBUSTNESS_AND_TRADES_PASSED")
        else:
            failed.append("ROBUSTNESS_FAILED")
            reasons.append(
                f"Backtest trades {evidence.trade_count} (<{cls.MIN_BACKTEST_TRADES}) or param robustness failed"
            )

        # 3. No leakage audit
        if evidence.no_leakage_audit_passed:
            passed.append("NO_LEAKAGE_AUDIT_PASSED")
        else:
            failed.append("NO_LEAKAGE_FAILED")
            reasons.append("Data leakage audit has not passed (available_at >= event_time not proven)")

        # 4. Paper evidence complete
        if evidence.paper_trade_count >= cls.MIN_PAPER_TRADES and evidence.paper_net_edge_positive:
            passed.append("PAPER_EVIDENCE_PASSED")
        else:
            failed.append("PAPER_EVIDENCE_FAILED")
            reasons.append(
                f"Paper trades {evidence.paper_trade_count} (<{cls.MIN_PAPER_TRADES}) or paper net edge <= 0"
            )

        # 5. Arbitrage leg completion rate
        if evidence.arbitrage_leg_completion_rate >= cls.MIN_LEG_COMPLETION_RATE:
            passed.append("LEG_COMPLETION_PASSED")
        else:
            failed.append("LEG_COMPLETION_FAILED")
            reasons.append(
                f"Leg completion rate {evidence.arbitrage_leg_completion_rate:.1%} < {cls.MIN_LEG_COMPLETION_RATE:.1%}"
            )

        # 6. Reconciliation stable
        if evidence.unresolved_reconciliation_breaks == 0:
            passed.append("RECONCILIATION_STABLE")
        else:
            failed.append("RECONCILIATION_FAILED")
            reasons.append(f"{evidence.unresolved_reconciliation_breaks} unresolved reconciliation break(s)")

        # 7. Venue reliability
        if (
            evidence.venue_uptime_pct >= cls.MIN_VENUE_UPTIME
            and evidence.venue_avg_latency_ms <= cls.MAX_VENUE_LATENCY_MS
        ):
            passed.append("VENUE_RELIABILITY_PASSED")
        else:
            failed.append("VENUE_RELIABILITY_FAILED")
            reasons.append(
                f"Venue uptime {evidence.venue_uptime_pct:.1%} or latency {evidence.venue_avg_latency_ms}ms outside bounds"
            )

        # 8. No-leverage rule verification
        if evidence.no_leverage_verified:
            passed.append("NO_LEVERAGE_VERIFIED")
        else:
            failed.append("NO_LEVERAGE_FAILED")
            reasons.append("No-leverage rule verification failed")

        # 9. Rolling 24h UTC stop verification
        if evidence.rolling_24h_utc_stop_passed:
            passed.append("ROLLING_24H_STOP_PASSED")
        else:
            failed.append("ROLLING_24H_STOP_FAILED")
            reasons.append("Rolling 24-hour UTC stop verification failed")

        # 10. Kill-switch / Watchdog
        if evidence.killswitch_watchdog_passed:
            passed.append("KILLSWITCH_WATCHDOG_PASSED")
        else:
            failed.append("KILLSWITCH_WATCHDOG_FAILED")
            reasons.append("Kill-switch / Watchdog drill verification failed")

        # 11. Human review sign-off
        if evidence.human_signoff_recorded and evidence.human_reviewer:
            passed.append("HUMAN_SIGNOFF_RECORDED")
        else:
            failed.append("HUMAN_SIGNOFF_MISSING")
            reasons.append("Explicit human review sign-off has not been recorded")

        promoted = len(failed) == 0

        return CryptoPromotionDecision(
            promoted=promoted,
            strategy_id=evidence.strategy_id,
            venue=evidence.venue,
            checks_passed=tuple(passed),
            checks_failed=tuple(failed),
            reasons=tuple(reasons),
            evaluated_at=now,
        )
