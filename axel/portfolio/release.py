"""
P11-GOVERNANCE: shadow-mode evidence and release-gate evaluation.

The release gate can approve evidence for human review. It cannot enable live
execution, modify risk limits, or bypass the existing deterministic core and
Watchdog. Thresholds must be approved before the shadow period begins.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite

from axel.portfolio.serialization import fingerprint
from axel.portfolio.simulation import SimulationStatus
from axel.portfolio.types import (
    ProposalAction,
    SolverStatus,
    ValidationDecision,
    require_aware,
    require_non_negative,
)

RELEASE_GATE_VERSION = "p11-release-gate-1"


class ReleaseStatus(str, Enum):
    APPROVED_FOR_HUMAN_REVIEW = "approved_for_human_review"
    BLOCKED = "blocked"


class ReleaseGateError(ValueError):
    """Raised for malformed criteria or shadow observations."""


@dataclass(frozen=True)
class ShadowCriteria:
    """Pre-registered thresholds for shadow-mode evidence."""

    criteria_version: str
    approved_at: datetime
    effective_at: datetime
    minimum_shadow_days: int = 14
    minimum_proposals: int = 20
    minimum_simulations: int = 10
    max_solver_failure_rate: float = 0.02
    max_rejection_rate: float = 0.25
    max_stale_rate: float = 0.01
    max_open_reconciliation_issues: int = 0
    minimum_stress_pass_rate: float = 1.0
    max_proposal_age_seconds: float = 900.0
    max_cost_forecast_error_bps: float = 50.0
    minimum_completeness_rate: float = 1.0
    minimum_freshness_rate: float = 1.0
    minimum_reconciliation_rate: float = 1.0
    require_no_live_orders: bool = True
    require_watchdog_clear: bool = True
    require_human_approval: bool = True

    def __post_init__(self) -> None:
        if not self.criteria_version:
            raise ReleaseGateError("criteria_version must be non-empty")
        object.__setattr__(self, "approved_at", require_aware(self.approved_at, "approved_at"))
        object.__setattr__(
            self, "effective_at", require_aware(self.effective_at, "effective_at")
        )
        if self.approved_at > self.effective_at:
            raise ReleaseGateError("criteria cannot be effective before approval")
        for name in (
            "minimum_shadow_days",
            "minimum_proposals",
            "minimum_simulations",
            "max_open_reconciliation_issues",
        ):
            if getattr(self, name) < 0:
                raise ReleaseGateError(f"{name} must be non-negative")
        for name in (
            "max_solver_failure_rate",
            "max_rejection_rate",
            "max_stale_rate",
            "minimum_stress_pass_rate",
            "minimum_completeness_rate",
            "minimum_freshness_rate",
            "minimum_reconciliation_rate",
        ):
            value = getattr(self, name)
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ReleaseGateError(f"{name} must be in [0, 1]")
        for name in ("max_proposal_age_seconds", "max_cost_forecast_error_bps"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ReleaseGateError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class ShadowObservation:
    """One proposal/validation/simulation observation from shadow mode."""

    observed_at: datetime
    snapshot_complete: bool
    snapshot_fresh: bool
    reconciled: bool
    solver_status: SolverStatus
    proposal_action: ProposalAction
    validation_decision: ValidationDecision | None = None
    stress_passed: bool | None = None
    proposal_age_seconds: float = 0.0
    estimated_turnover: float = 0.0
    predicted_cost: float | None = None
    realized_cost: float | None = None
    simulation_status: SimulationStatus | None = None
    live_orders_submitted: bool = False
    watchdog_halted: bool = False
    open_reconciliation_issues: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", require_aware(self.observed_at, "observed_at"))
        if not isinstance(self.solver_status, SolverStatus):
            object.__setattr__(self, "solver_status", SolverStatus(self.solver_status))
        if not isinstance(self.proposal_action, ProposalAction):
            object.__setattr__(
                self, "proposal_action", ProposalAction(self.proposal_action)
            )
        if self.validation_decision is not None and not isinstance(
            self.validation_decision, ValidationDecision
        ):
            object.__setattr__(
                self,
                "validation_decision",
                ValidationDecision(self.validation_decision),
            )
        if self.simulation_status is not None and not isinstance(
            self.simulation_status, SimulationStatus
        ):
            object.__setattr__(
                self, "simulation_status", SimulationStatus(self.simulation_status)
            )
        require_non_negative(self.proposal_age_seconds, "proposal_age_seconds")
        require_non_negative(self.estimated_turnover, "estimated_turnover")
        if self.predicted_cost is not None:
            require_non_negative(self.predicted_cost, "predicted_cost")
        if self.realized_cost is not None:
            require_non_negative(self.realized_cost, "realized_cost")
        if self.open_reconciliation_issues < 0:
            raise ReleaseGateError("open_reconciliation_issues must be non-negative")


@dataclass(frozen=True)
class ShadowMetrics:
    period_start: datetime
    period_end: datetime
    observations: int
    proposals: int
    simulations: int
    solver_failures: int
    stale_count: int
    rejected_count: int
    stress_runs: int
    stress_passes: int
    open_reconciliation_issues: int
    max_proposal_age_seconds: float
    turnover: float
    predicted_cost: float
    realized_cost: float | None
    cost_forecast_error_bps: float | None
    live_order_count: int
    watchdog_halt_count: int
    completeness_rate: float
    freshness_rate: float
    reconciliation_rate: float
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "period_start", require_aware(self.period_start, "period_start"))
        object.__setattr__(self, "period_end", require_aware(self.period_end, "period_end"))
        if self.period_end < self.period_start:
            raise ReleaseGateError("period_end cannot precede period_start")
        if self.observations < 0 or self.proposals < 0 or self.simulations < 0:
            raise ReleaseGateError("observation counts must be non-negative")

    @property
    def shadow_days(self) -> float:
        return (self.period_end - self.period_start).total_seconds() / 86_400.0

    @property
    def solver_failure_rate(self) -> float:
        return self.solver_failures / self.proposals if self.proposals else 0.0

    @property
    def rejection_rate(self) -> float:
        return self.rejected_count / self.proposals if self.proposals else 0.0

    @property
    def stale_rate(self) -> float:
        return self.stale_count / self.observations if self.observations else 0.0

    @property
    def stress_pass_rate(self) -> float:
        return self.stress_passes / self.stress_runs if self.stress_runs else 0.0


def collect_shadow_metrics(
    observations: Sequence[ShadowObservation],
    *,
    period_start: datetime,
    period_end: datetime,
) -> ShadowMetrics:
    """Aggregate shadow observations without hiding missing outcomes."""
    start = require_aware(period_start, "period_start")
    end = require_aware(period_end, "period_end")
    if end < start:
        raise ReleaseGateError("period_end cannot precede period_start")
    if not observations:
        raise ReleaseGateError("shadow metrics require at least one observation")
    for observation in observations:
        if not start <= observation.observed_at <= end:
            raise ReleaseGateError("observation falls outside the shadow period")
    proposals = len(observations)
    simulations = sum(
        1 for observation in observations if observation.simulation_status is not None
    )
    solver_failures = sum(
        1
        for observation in observations
        if observation.solver_status
        not in {SolverStatus.OPTIMAL, SolverStatus.APPROXIMATE}
    )
    stale = sum(
        1
        for observation in observations
        if not observation.snapshot_complete
        or not observation.snapshot_fresh
        or not observation.reconciled
    )
    rejected = sum(
        1
        for observation in observations
        if observation.validation_decision == ValidationDecision.REJECT
    )
    stress_observations = [
        observation for observation in observations if observation.stress_passed is not None
    ]
    stress_passes = sum(1 for observation in stress_observations if observation.stress_passed)
    predicted = sum(
        observation.predicted_cost or 0.0 for observation in observations
    )
    realized_values = [
        observation.realized_cost
        for observation in observations
        if observation.realized_cost is not None
    ]
    realized = sum(realized_values) if realized_values else None
    cost_error: float | None = None
    if realized is not None:
        cost_error = abs(realized - predicted) / max(abs(predicted), 1e-9) * 10_000.0
    warnings: list[str] = []
    if len(realized_values) < simulations:
        warnings.append(
            "realized costs are missing for some simulated rebalances; cost forecast "
            "error is incomplete"
        )
    if not stress_observations:
        warnings.append("no stress results were recorded during shadow mode")
    return ShadowMetrics(
        period_start=start,
        period_end=end,
        observations=len(observations),
        proposals=proposals,
        simulations=simulations,
        solver_failures=solver_failures,
        stale_count=stale,
        rejected_count=rejected,
        stress_runs=len(stress_observations),
        stress_passes=stress_passes,
        open_reconciliation_issues=max(
            observation.open_reconciliation_issues for observation in observations
        ),
        max_proposal_age_seconds=max(
            observation.proposal_age_seconds for observation in observations
        ),
        turnover=sum(observation.estimated_turnover for observation in observations),
        predicted_cost=predicted,
        realized_cost=realized,
        cost_forecast_error_bps=cost_error,
        live_order_count=sum(
            1 for observation in observations if observation.live_orders_submitted
        ),
        watchdog_halt_count=sum(
            1 for observation in observations if observation.watchdog_halted
        ),
        completeness_rate=sum(
            1 for observation in observations if observation.snapshot_complete
        )
        / len(observations),
        freshness_rate=sum(
            1 for observation in observations if observation.snapshot_fresh
        )
        / len(observations),
        reconciliation_rate=sum(
            1 for observation in observations if observation.reconciled
        )
        / len(observations),
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class ReleaseGateResult:
    """Evidence-gate outcome. Approval here is not live-execution permission."""

    status: ReleaseStatus
    criteria_version: str
    criteria_fingerprint: str
    evaluated_at: datetime
    metrics: ShadowMetrics
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: Mapping[str, object] = field(default_factory=dict)
    live_execution_enabled: bool = False
    requires_human_approval: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluated_at", require_aware(self.evaluated_at, "evaluated_at"))
        if self.live_execution_enabled:
            raise ReleaseGateError(
                "Phase 11 release gate cannot enable live execution by itself"
            )

    @property
    def approved_for_human_review(self) -> bool:
        return self.status == ReleaseStatus.APPROVED_FOR_HUMAN_REVIEW


def evaluate_release_gate(
    metrics: ShadowMetrics,
    criteria: ShadowCriteria,
    *,
    evaluated_at: datetime,
) -> ReleaseGateResult:
    """Evaluate pre-registered shadow evidence and fail closed."""
    current = require_aware(evaluated_at, "evaluated_at")
    blockers: list[str] = []
    warnings: list[str] = []
    if criteria.approved_at > metrics.period_start:
        blockers.append("criteria_not_pre_registered")
    if current < criteria.effective_at:
        blockers.append("criteria_not_yet_effective")
    if current < metrics.period_end:
        blockers.append("shadow_period_not_complete")
    if metrics.shadow_days < criteria.minimum_shadow_days:
        blockers.append("insufficient_shadow_days")
    if metrics.proposals < criteria.minimum_proposals:
        blockers.append("insufficient_proposals")
    if metrics.simulations < criteria.minimum_simulations:
        blockers.append("insufficient_simulations")
    if metrics.solver_failure_rate > criteria.max_solver_failure_rate + 1e-12:
        blockers.append("solver_failure_rate_exceeded")
    if metrics.rejection_rate > criteria.max_rejection_rate + 1e-12:
        blockers.append("rejection_rate_exceeded")
    if metrics.stale_rate > criteria.max_stale_rate + 1e-12:
        blockers.append("stale_rate_exceeded")
    if metrics.open_reconciliation_issues > criteria.max_open_reconciliation_issues:
        blockers.append("open_reconciliation_issues")
    if metrics.stress_runs == 0:
        blockers.append("missing_stress_evidence")
    elif metrics.stress_pass_rate < criteria.minimum_stress_pass_rate - 1e-12:
        blockers.append("stress_pass_rate_below_minimum")
    if metrics.max_proposal_age_seconds > criteria.max_proposal_age_seconds + 1e-9:
        blockers.append("proposal_age_exceeded")
    if metrics.cost_forecast_error_bps is None:
        blockers.append("missing_realized_cost_evidence")
    elif metrics.cost_forecast_error_bps > criteria.max_cost_forecast_error_bps + 1e-9:
        blockers.append("cost_forecast_error_exceeded")
    if metrics.completeness_rate < criteria.minimum_completeness_rate - 1e-12:
        blockers.append("completeness_rate_below_minimum")
    if metrics.freshness_rate < criteria.minimum_freshness_rate - 1e-12:
        blockers.append("freshness_rate_below_minimum")
    if metrics.reconciliation_rate < criteria.minimum_reconciliation_rate - 1e-12:
        blockers.append("reconciliation_rate_below_minimum")
    if criteria.require_no_live_orders and metrics.live_order_count:
        blockers.append("live_orders_observed_in_shadow_mode")
    if criteria.require_watchdog_clear and metrics.watchdog_halt_count:
        blockers.append("watchdog_halt_observed")
    if criteria.require_human_approval:
        warnings.append("release approval still requires a human decision")
    warnings.extend(metrics.warnings)
    status = ReleaseStatus.BLOCKED if blockers else ReleaseStatus.APPROVED_FOR_HUMAN_REVIEW
    return ReleaseGateResult(
        status=status,
        criteria_version=criteria.criteria_version,
        criteria_fingerprint=fingerprint("release_criteria", criteria),
        evaluated_at=current,
        metrics=metrics,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        evidence={
            "release_gate_version": RELEASE_GATE_VERSION,
            "criteria": criteria,
            "metrics": metrics,
            "blockers": tuple(dict.fromkeys(blockers)),
            "live_execution_enabled": False,
            "requires_human_approval": True,
        },
    )


__all__ = [
    "RELEASE_GATE_VERSION",
    "ReleaseGateError",
    "ReleaseGateResult",
    "ReleaseStatus",
    "ShadowCriteria",
    "ShadowMetrics",
    "ShadowObservation",
    "collect_shadow_metrics",
    "evaluate_release_gate",
]
