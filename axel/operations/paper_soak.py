"""Phase 5 paper-soak evidence and promotion-gate reporting.

This module can only report readiness. It never changes `ENVIRONMENT`, submits an
order, or grants live-trading permission.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from axel.core.ids import generate_id
from axel.db.models import AuditLogModel


@dataclass(frozen=True)
class PaperTradeObservation:
    predicted_probability: float
    outcome: bool
    expected_slippage_bps: float
    realized_slippage_bps: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.predicted_probability <= 1.0:
            raise ValueError("predicted_probability must be in [0, 1]")
        if self.expected_slippage_bps < 0 or self.realized_slippage_bps < 0:
            raise ValueError("slippage values cannot be negative")


@dataclass(frozen=True)
class PromotionGatePolicy:
    minimum_soak_days: int = 56
    minimum_paper_trades: int = 200
    max_mean_slippage_delta_bps: float = 3.0
    max_brier_score: float = 0.25
    require_recent_killswitch_drill: bool = True
    require_recovery_drill: bool = True


@dataclass(frozen=True)
class PromotionGateReport:
    eligible: bool
    paper_trades: int
    soak_days: int
    brier_score: float | None
    mean_slippage_delta_bps: float | None
    reasons: tuple[str, ...]


def brier_score(observations: list[PaperTradeObservation]) -> float | None:
    if not observations:
        return None
    return sum((item.predicted_probability - float(item.outcome)) ** 2 for item in observations) / len(observations)


def mean_slippage_delta_bps(observations: list[PaperTradeObservation]) -> float | None:
    if not observations:
        return None
    return sum(abs(item.realized_slippage_bps - item.expected_slippage_bps) for item in observations) / len(observations)


def evaluate_promotion_gate(
    *, started_at: datetime, observations: list[PaperTradeObservation],
    unresolved_reconciliation_breaks: int, killswitch_drill_passed_at: datetime | None,
    recovery_drill_passed: bool, now: datetime | None = None,
    policy: PromotionGatePolicy | None = None,
) -> PromotionGateReport:
    """Evaluate fixed Phase 5 criteria; failed/missing evidence always fails closed."""
    now = now or datetime.now(UTC)
    policy = policy or PromotionGatePolicy()
    if started_at.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")
    soak_days = max(0, (now - started_at).days)
    calibration = brier_score(observations)
    slippage_delta = mean_slippage_delta_bps(observations)
    reasons: list[str] = []
    if soak_days < policy.minimum_soak_days:
        reasons.append("SOAK_DURATION_INCOMPLETE")
    if len(observations) < policy.minimum_paper_trades:
        reasons.append("INSUFFICIENT_PAPER_TRADES")
    if calibration is None or calibration > policy.max_brier_score:
        reasons.append("CALIBRATION_GATE_FAILED")
    if slippage_delta is None or slippage_delta > policy.max_mean_slippage_delta_bps:
        reasons.append("SLIPPAGE_TOLERANCE_FAILED")
    if unresolved_reconciliation_breaks:
        reasons.append("UNRESOLVED_RECONCILIATION_BREAKS")
    drill_recent = killswitch_drill_passed_at and now - killswitch_drill_passed_at <= timedelta(days=30)
    if policy.require_recent_killswitch_drill and not drill_recent:
        reasons.append("KILLSWITCH_DRILL_NOT_RECENT")
    if policy.require_recovery_drill and not recovery_drill_passed:
        reasons.append("RECOVERY_DRILL_NOT_PASSED")
    return PromotionGateReport(not reasons, len(observations), soak_days, calibration, slippage_delta, tuple(reasons))


def record_incident(session: Session, *, severity: str, summary: str, details: dict[str, object]) -> AuditLogModel:
    """Persist a paper-soak incident to the existing append-only audit log."""
    if severity not in {"info", "warning", "critical"}:
        raise ValueError("invalid incident severity")
    incident = AuditLogModel(id=generate_id("incident"), timestamp=datetime.now(UTC),
        event_type="paper_soak_incident", actor="paper_soak", payload={"severity": severity, "summary": summary, **details})
    session.add(incident)
    session.commit()
    return incident


def unresolved_incidents(session: Session) -> list[AuditLogModel]:
    """Return unclosed paper incidents; closure must be a separate append-only audit event."""
    stmt = select(AuditLogModel).where(AuditLogModel.event_type == "paper_soak_incident").order_by(AuditLogModel.timestamp.asc())
    return list(session.execute(stmt).scalars())
