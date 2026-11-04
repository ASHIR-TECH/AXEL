"""
P11-PROPOSAL: allocation and rebalance proposals with structured explanations.

A proposal is evidence plus a candidate allocation. It is not an order and not
authorization. The existing deterministic risk, execution, and Watchdog layers
remain authoritative.

Every proposal records its input cutoff, expiry, solver status, constraint
evaluation, stress reference, model versions, data quality, and known
limitations. Failed optimization produces an explicit DEFERRED proposal, not an
empty allocation that could be mistaken for a valid zero-risk target.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from axel.core.types import Section
from axel.portfolio.constraints import hard_constraints_pass
from axel.portfolio.serialization import fingerprint
from axel.portfolio.stress import StressReport
from axel.portfolio.types import (
    AllocationProposal,
    AllocationResult,
    AllocationTarget,
    ConstraintResult,
    CostSummary,
    DataQuality,
    Explanation,
    ExposureSnapshot,
    PortfolioSnapshot,
    ProposalAction,
    ProposalState,
    RebalanceChange,
    RebalanceProposal,
    RiskBudgetPolicy,
    RiskBudgetProposal,
    RiskSummary,
    as_section,
    require_aware,
    require_non_negative,
    utc_now,
)

PROPOSAL_VERSION = "p11-proposal-1"
DEFAULT_PROPOSAL_TTL_SECONDS = 900


class ProposalBuildError(ValueError):
    """Raised when a proposal cannot be built from inconsistent inputs."""


def _empty_cost_summary(reason: str) -> CostSummary:
    return CostSummary(
        total_cost=0.0,
        stress_cost=0.0,
        by_component={},
        by_instrument={},
        model_version="none",
        coverage=DataQuality.MISSING,
        warnings=(reason,),
        limitations=("no trade costs were estimated because no allocation was produced",),
    )


def _empty_explanation(reason: str) -> Explanation:
    return Explanation(
        observed_facts=(),
        statistical_estimates=(),
        optimization_results=(reason,),
        scenario_assumptions=(),
        policy_limits=(),
        missing_information=(reason,),
        model_limitations=("A failed proposal is not authorization to trade.",),
    )


def _target_action(delta: float, threshold: float) -> ProposalAction:
    if delta > threshold:
        return ProposalAction.INCREASE
    if delta < -threshold:
        return ProposalAction.REDUCE
    return ProposalAction.HOLD


def _overall_action(targets: Sequence[AllocationTarget]) -> ProposalAction:
    if any(target.action == ProposalAction.INCREASE for target in targets):
        return ProposalAction.INCREASE
    if any(target.action == ProposalAction.REDUCE for target in targets):
        return ProposalAction.REDUCE
    return ProposalAction.HOLD


def _dedupe_constraints(
    results: Sequence[ConstraintResult],
) -> tuple[ConstraintResult, ...]:
    seen: dict[tuple[str, str, float | None], ConstraintResult] = {}
    for result in results:
        key = (result.name, result.unit, result.limit)
        existing = seen.get(key)
        if existing is None or (existing.passed and not result.passed):
            seen[key] = result
    return tuple(seen.values())


def _exposure_totals(exposure: ExposureSnapshot) -> Mapping[str, float]:
    totals: dict[str, float] = {}
    for item in exposure.instrument_exposures:
        if item.asset_class.value == "option" and item.delta_equivalent is not None:
            value = item.delta_equivalent
        elif item.asset_class.value == "prediction" and item.max_loss is not None:
            value = item.max_loss
        elif item.asset_class.value == "forex" and item.delta_equivalent is not None:
            value = item.delta_equivalent
        else:
            value = item.market_value
        totals[item.instrument_id] = totals.get(item.instrument_id, 0.0) + value
    return totals


def _build_explanation(
    *,
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    budget: RiskBudgetProposal,
    allocation: AllocationResult,
    policy: RiskBudgetPolicy,
    risk_summary: RiskSummary,
    stress_report: StressReport | None,
    cost_summary: CostSummary,
) -> Explanation:
    top_contributions = sorted(
        (
            f"{instrument_id}: {weight:.4f}"
            for instrument_id, weight in zip(
                allocation.instrument_ids, allocation.weights or (), strict=True
            )
        ),
        key=lambda value: value,
    )[:5]
    observed = [
        f"Portfolio snapshot {snapshot.snapshot_id} at {snapshot.snapshot_time.isoformat()}.",
        (
            f"Equity {snapshot.capital_summary.equity_value} {snapshot.base_currency}; "
            f"data quality {snapshot.data_quality_status.value}; "
            f"reconciliation {snapshot.reconciliation_status.value}."
        ),
        f"{len(snapshot.positions)} position(s) and {len(snapshot.open_orders)} open order(s).",
        (
            f"Exposure snapshot {exposure.exposure_snapshot_id} coverage "
            f"{exposure.coverage_ratio:.3f}."
        ),
    ]
    statistical = [
        (
            f"Expected volatility {risk_summary.volatility:.6f}; "
            f"95% VaR {risk_summary.var_95:.6f}; "
            f"95% expected shortfall {risk_summary.expected_shortfall_95:.6f}."
        ),
        (
            f"Gross exposure {risk_summary.gross_exposure:.2f}; "
            f"net exposure {risk_summary.net_exposure:.2f}."
        ),
        "Top target weights: " + ", ".join(top_contributions) + ".",
    ]
    optimization = [
        (
            f"Method {allocation.method.value}; solver status {allocation.status.value}; "
            f"iterations {allocation.iterations}."
        ),
        (
            f"One-way turnover {allocation.turnover}; estimated cost "
            f"{cost_summary.total_cost:.2f} {snapshot.base_currency}."
        ),
        (
            f"Expected return {allocation.expected_return}; "
            f"expected volatility {allocation.expected_volatility}."
        ),
    ]
    scenarios: list[str] = []
    if stress_report is not None:
        scenarios.append(
            f"Stress suite {stress_report.report_id} passed={stress_report.passed}; "
            f"worst loss {stress_report.worst_case_loss:.2f} in "
            f"{stress_report.worst_scenario_id}."
        )
        scenarios.extend(
            f"{result.scenario_id} is hypothetical; no calibrated probability."
            for result in stress_report.results
            if result.hypothetical
        )
    else:
        scenarios.append("No stress report was supplied; stress status is unknown.")
    limits = [
        (
            f"Policy {policy.policy_version}; global gross cap "
            f"{policy.global_max_gross_exposure}; net cap {policy.global_max_net_exposure}; "
            f"max turnover {policy.global_max_turnover}."
        ),
        (
            f"Budget proposal {budget.budget_id}; method {budget.method}; "
            f"total budget {budget.total_risk_budget:.2f}."
        ),
    ]
    missing: list[str] = []
    if exposure.failures:
        missing.extend(
            f"normalization failure {failure.instrument_id}: {failure.reason}"
            for failure in exposure.failures
        )
    missing.extend(snapshot.known_limitations)
    if stress_report is not None:
        missing.extend(stress_report.limitations)
    model_limits = [
        "Proposal generation is not authorization and does not submit orders.",
        "Covariance, VaR, and expected shortfall are estimates, not guarantees.",
        "The optimizer does not override the deterministic risk or execution layer.",
    ]
    model_limits.extend(cost_summary.limitations)
    return Explanation(
        observed_facts=tuple(observed),
        statistical_estimates=tuple(statistical),
        optimization_results=tuple(optimization),
        scenario_assumptions=tuple(dict.fromkeys(scenarios)),
        policy_limits=tuple(limits),
        missing_information=tuple(dict.fromkeys(missing)),
        model_limitations=tuple(dict.fromkeys(model_limits)),
    )


def build_allocation_proposal(
    *,
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    budget: RiskBudgetProposal,
    allocation: AllocationResult,
    policy: RiskBudgetPolicy,
    cost_summary: CostSummary,
    risk_summary: RiskSummary,
    section_by_instrument: Mapping[str, Section],
    stress_report: StressReport | None = None,
    extra_constraints: Sequence[ConstraintResult] = (),
    data_quality_status: DataQuality | None = None,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_PROPOSAL_TTL_SECONDS,
    known_limitations: Sequence[str] = (),
    requires_human_approval: bool = True,
) -> AllocationProposal:
    """Build an allocation proposal from a successful candidate allocation."""
    current = require_aware(now or utc_now(), "now")
    if ttl_seconds <= 0:
        raise ProposalBuildError("ttl_seconds must be positive")
    if exposure.portfolio_snapshot_id != snapshot.snapshot_id:
        raise ProposalBuildError("exposure does not match the portfolio snapshot")
    if budget.portfolio_snapshot_id != snapshot.snapshot_id:
        raise ProposalBuildError("budget does not match the portfolio snapshot")
    if any(
        instrument_id not in section_by_instrument
        for instrument_id in allocation.instrument_ids
    ):
        raise ProposalBuildError("section mapping is missing an allocation instrument")
    if cost_summary.coverage == DataQuality.MISSING:
        data_quality = DataQuality.MISSING
    else:
        data_quality = data_quality_status or snapshot.data_quality_status
    expires_at = current + timedelta(seconds=ttl_seconds)
    constraints = _dedupe_constraints(
        tuple(allocation.constraint_results) + tuple(extra_constraints)
    )
    stress_passed = stress_report.passed if stress_report is not None else False
    hard_pass = hard_constraints_pass(constraints) and stress_passed
    successful = allocation.successful and allocation.weights is not None

    if not successful:
        reason = allocation.solver_message or allocation.status.value
        proposal_id = fingerprint(
            "alloc_prop",
            PROPOSAL_VERSION,
            snapshot.snapshot_id,
            exposure.exposure_snapshot_id,
            budget.budget_id,
            allocation.input_fingerprint,
            "deferred",
            current,
        )
        return AllocationProposal(
            proposal_id=proposal_id,
            portfolio_snapshot_id=snapshot.snapshot_id,
            exposure_snapshot_id=exposure.exposure_snapshot_id,
            risk_budget_proposal_id=budget.budget_id,
            policy_version=policy.policy_version,
            optimizer_version=allocation.input_fingerprint,
            method=allocation.method,
            solver_status=allocation.status,
            proposed_budgets={
                item.section.value: item.risk_budget for item in budget.section_budgets
            },
            proposed_targets=(),
            estimated_costs=cost_summary,
            estimated_turnover=0.0,
            risk_summary=risk_summary,
            stress_report_id=stress_report.report_id if stress_report is not None else None,
            stress_passed=stress_passed,
            constraint_results=constraints,
            data_quality_status=data_quality,
            approval_state=ProposalState.DEFERRED,
            action=ProposalAction.DEFER,
            expires_at=expires_at,
            created_at=current,
            input_cutoff_time=snapshot.source_cutoff_time,
            explanation=_empty_explanation(reason),
            known_limitations=tuple(
                dict.fromkeys(
                    tuple(known_limitations)
                    + tuple(exposure.uncertainty_flags)
                    + (reason,)
                )
            ),
            requires_human_approval=requires_human_approval,
            idempotency_key=fingerprint(
                "alloc_prop_key", proposal_id, allocation.input_fingerprint
            ),
        )

    equity = snapshot.capital_summary.equity_value
    if equity is None or equity <= 0:
        raise ProposalBuildError("portfolio equity is unknown or non-positive")
    weights = tuple(allocation.weights or ())
    if len(weights) != len(allocation.instrument_ids):
        raise ProposalBuildError("allocation weight vector has the wrong length")
    current_totals = _exposure_totals(exposure)
    targets: list[AllocationTarget] = []
    for index, (instrument_id, weight) in enumerate(
        zip(allocation.instrument_ids, weights, strict=True)
    ):
        section = as_section(section_by_instrument[instrument_id])
        current_exposure = current_totals.get(instrument_id, 0.0)
        proposed_exposure = weight * equity
        delta = proposed_exposure - current_exposure
        action = _target_action(delta, 1e-9)
        reason_codes = [
            f"optimizer:{allocation.method.value}",
            f"solver:{allocation.status.value}",
            f"section_budget:{section.value}",
            "hard_constraints_passed" if hard_pass else "hard_constraints_failed",
        ]
        targets.append(
            AllocationTarget(
                instrument_id=instrument_id,
                section=section,
                current_weight=allocation.current_weights[index],
                proposed_weight=weight,
                current_exposure=current_exposure,
                proposed_exposure=proposed_exposure,
                delta_exposure=delta,
                action=action,
                reason_codes=tuple(reason_codes),
            )
        )
    overall_action = _overall_action(targets)
    state = (
        ProposalState.PENDING_VALIDATION
        if hard_pass and overall_action != ProposalAction.DEFER
        else ProposalState.DEFERRED
    )
    if state == ProposalState.DEFERRED:
        overall_action = ProposalAction.DEFER
    explanation = _build_explanation(
        snapshot=snapshot,
        exposure=exposure,
        budget=budget,
        allocation=allocation,
        policy=policy,
        risk_summary=risk_summary,
        stress_report=stress_report,
        cost_summary=cost_summary,
    )
    proposal_id = fingerprint(
        "alloc_prop",
        PROPOSAL_VERSION,
        snapshot.snapshot_id,
        exposure.exposure_snapshot_id,
        budget.budget_id,
        allocation.input_fingerprint,
        tuple(targets),
        current,
    )
    limitations = tuple(
        dict.fromkeys(
            tuple(known_limitations)
            + tuple(exposure.uncertainty_flags)
            + tuple(cost_summary.limitations)
            + (
                ()
                if stress_report is not None
                else ("no stress report supplied",)
            )
        )
    )
    return AllocationProposal(
        proposal_id=proposal_id,
        portfolio_snapshot_id=snapshot.snapshot_id,
        exposure_snapshot_id=exposure.exposure_snapshot_id,
        risk_budget_proposal_id=budget.budget_id,
        policy_version=policy.policy_version,
        optimizer_version=allocation.input_fingerprint,
        method=allocation.method,
        solver_status=allocation.status,
        proposed_budgets={
            item.section.value: item.risk_budget for item in budget.section_budgets
        },
        proposed_targets=tuple(targets),
        estimated_costs=cost_summary,
        estimated_turnover=allocation.turnover or 0.0,
        risk_summary=risk_summary,
        stress_report_id=stress_report.report_id if stress_report is not None else None,
        stress_passed=stress_passed,
        constraint_results=constraints,
        data_quality_status=data_quality,
        approval_state=state,
        action=overall_action,
        expires_at=expires_at,
        created_at=current,
        input_cutoff_time=snapshot.source_cutoff_time,
        explanation=explanation,
        known_limitations=limitations,
        requires_human_approval=requires_human_approval,
        idempotency_key=fingerprint(
            "alloc_prop_key", proposal_id, allocation.input_fingerprint
        ),
    )


def build_rebalance_proposal(
    proposal: AllocationProposal,
    *,
    snapshot: PortfolioSnapshot,
    now: datetime | None = None,
) -> RebalanceProposal:
    """Turn an allocation proposal into explicit proposed changes.

    This function has no execution path. It cannot create orders.
    """
    current = require_aware(now or utc_now(), "now")
    if proposal.portfolio_snapshot_id != snapshot.snapshot_id:
        raise ProposalBuildError("proposal does not match the portfolio snapshot")
    changes: list[RebalanceChange] = []
    for target in proposal.proposed_targets:
        if target.action == ProposalAction.HOLD:
            continue
        changes.append(
            RebalanceChange(
                instrument_id=target.instrument_id,
                section=target.section,
                current_exposure=target.current_exposure,
                target_exposure=target.proposed_exposure,
                delta_exposure=target.delta_exposure,
                delta_weight=target.proposed_weight - target.current_weight,
                action=target.action,
                estimated_cost=proposal.estimated_costs.by_instrument.get(
                    target.instrument_id, 0.0
                ),
            )
        )
    gross_increase = sum(max(0.0, change.delta_exposure) for change in changes)
    gross_reduction = sum(max(0.0, -change.delta_exposure) for change in changes)
    required_capital = {
        "gross_increase": gross_increase,
        "gross_reduction": gross_reduction,
        "estimated_cost": proposal.estimated_costs.total_cost,
        "total": gross_increase + proposal.estimated_costs.total_cost,
    }
    preconditions: list[str] = []
    if snapshot.is_usable_for_risk_increase:
        preconditions.append("snapshot_usable_for_risk_increase")
    else:
        preconditions.append("snapshot_not_usable_for_risk_increase")
    if snapshot.reconciliation_status.value == "reconciled":
        preconditions.append("reconciliation_reconciled")
    else:
        preconditions.append("reconciliation_not_reconciled")
    preconditions.append("stress_suite_passed" if proposal.stress_passed else "stress_suite_failed")
    preconditions.append(
        "hard_constraints_passed"
        if hard_constraints_pass(proposal.constraint_results)
        else "hard_constraints_failed"
    )
    preconditions.append(
        "proposal_fresh" if not proposal.expired_at(current) else "proposal_expired"
    )
    preconditions.append(
        "human_approval_required"
        if proposal.requires_human_approval
        else "human_approval_not_required"
    )
    preconditions.append("external_authorization_required")
    blocking_values = {
        "stress_suite_failed",
        "hard_constraints_failed",
        "proposal_expired",
    }
    if proposal.is_risk_increasing:
        blocking_values.update(
            {
                "snapshot_not_usable_for_risk_increase",
                "reconciliation_not_reconciled",
            }
        )
    blocking = any(value in blocking_values for value in preconditions)
    state = ProposalState.DEFERRED if blocking else ProposalState.PENDING_VALIDATION
    action = ProposalAction.DEFER if blocking else _overall_action(
        [
            AllocationTarget(
                instrument_id=change.instrument_id,
                section=change.section,
                current_weight=0.0,
                proposed_weight=0.0,
                current_exposure=change.current_exposure,
                proposed_exposure=change.target_exposure,
                delta_exposure=change.delta_exposure,
                action=change.action,
            )
            for change in changes
        ]
    )
    rebalance_id = fingerprint(
        "rebalance",
        PROPOSAL_VERSION,
        proposal.proposal_id,
        tuple(changes),
        required_capital,
        preconditions,
        current,
    )
    return RebalanceProposal(
        rebalance_id=rebalance_id,
        allocation_proposal_id=proposal.proposal_id,
        portfolio_snapshot_id=snapshot.snapshot_id,
        changes=tuple(changes),
        estimated_costs=proposal.estimated_costs,
        required_capital=required_capital,
        preconditions=tuple(preconditions),
        validation_state=state,
        approval_state=state,
        action=action,
        expires_at=proposal.expires_at,
        created_at=current,
        idempotency_key=fingerprint("rebalance_key", rebalance_id, proposal.idempotency_key),
        known_limitations=(
            (
                "Rebalance proposals do not submit orders; the existing deterministic "
                "execution path remains authoritative."
            ),
        ),
    )


def is_proposal_fresh(proposal: AllocationProposal | RebalanceProposal, now: datetime) -> bool:
    current = require_aware(now, "now")
    return proposal.created_at <= current <= proposal.expires_at


def expire_proposal(
    proposal: AllocationProposal,
    now: datetime,
) -> AllocationProposal:
    """Return an expired copy, preserving the original for audit."""
    current = require_aware(now, "now")
    if not proposal.expired_at(current):
        return proposal
    return replace(
        proposal,
        approval_state=ProposalState.EXPIRED,
        action=ProposalAction.DEFER,
    )


def proposal_age_seconds(proposal: AllocationProposal | RebalanceProposal, now: datetime) -> float:
    return (require_aware(now, "now") - proposal.created_at).total_seconds()


def proposal_is_stale(
    proposal: AllocationProposal | RebalanceProposal,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    require_non_negative(float(max_age_seconds), "max_age_seconds")
    return proposal_age_seconds(proposal, now) > max_age_seconds


__all__ = [
    "DEFAULT_PROPOSAL_TTL_SECONDS",
    "PROPOSAL_VERSION",
    "ProposalBuildError",
    "build_allocation_proposal",
    "build_rebalance_proposal",
    "expire_proposal",
    "is_proposal_fresh",
    "proposal_age_seconds",
    "proposal_is_stale",
]
