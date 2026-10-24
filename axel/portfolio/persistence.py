"""
Phase 11 persistence helpers.

These functions map frozen domain objects to the append-only ORM rows in
``axel.portfolio.db_models``. They are deliberately thin: no risk calculation,
no validation, and no order submission happens here. Callers own the database
transaction boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from axel.portfolio.db_models import (
    PortfolioAlertRow,
    PortfolioCommitmentRow,
    PortfolioConstraintResultRow,
    PortfolioDecisionAuditRow,
    PortfolioExposureRow,
    PortfolioOpenOrderRow,
    PortfolioPositionRow,
    PortfolioProposalRow,
    PortfolioProposalTargetRow,
    PortfolioRebalanceRow,
    PortfolioReconciliationIssueRow,
    PortfolioRiskBudgetRow,
    PortfolioSnapshotRow,
    PortfolioSourceRow,
    PortfolioStressRunRow,
)
from axel.portfolio.serialization import fingerprint, to_jsonable
from axel.portfolio.stress import StressReport
from axel.portfolio.types import (
    AllocationProposal,
    ExposureSnapshot,
    PortfolioAlert,
    PortfolioSnapshot,
    RebalanceProposal,
    ReconciliationIssue,
    RiskBudgetProposal,
)
from axel.portfolio.validator import PortfolioValidationResult


def _json(value: Any) -> Any:
    return to_jsonable(value)


def save_snapshot(session: Session, snapshot: PortfolioSnapshot) -> PortfolioSnapshotRow:
    existing = session.get(PortfolioSnapshotRow, snapshot.snapshot_id)
    if existing is not None:
        return existing
    row = PortfolioSnapshotRow(
        id=snapshot.snapshot_id,
        snapshot_version=snapshot.snapshot_version,
        snapshot_time=snapshot.snapshot_time,
        source_cutoff_time=snapshot.source_cutoff_time,
        base_currency=snapshot.base_currency,
        capital_summary=_json(snapshot.capital_summary),
        data_quality_status=snapshot.data_quality_status.value,
        completeness_status=snapshot.completeness_status.value,
        reconciliation_status=snapshot.reconciliation_status.value,
        is_usable_for_risk_increase=snapshot.is_usable_for_risk_increase,
        fingerprint=snapshot.fingerprint,
        schema_version=snapshot.schema_version,
        calculation_version=snapshot.calculation_version,
        known_limitations=list(snapshot.known_limitations),
        fx_rates=_json(snapshot.fx_rates),
    )
    session.add(row)
    for source in snapshot.source_refs:
        session.add(
            PortfolioSourceRow(
                id=fingerprint("src_row", snapshot.snapshot_id, source.source_id),
                snapshot_id=snapshot.snapshot_id,
                source_system=source.source_type,
                source_type=source.source_type,
                as_of=source.observed_at,
                ingestion_time=source.received_at,
                content_hash=fingerprint("src_hash", source),
                is_authoritative=False,
                freshness_status=source.quality.value,
                metadata_json={
                    "lineage": list(source.lineage),
                    "note": source.note,
                    "section": source.section.value if source.section else None,
                },
            )
        )
    for position in snapshot.positions:
        session.add(
            PortfolioPositionRow(
                id=fingerprint("pos_row", snapshot.snapshot_id, position.position_id),
                snapshot_id=snapshot.snapshot_id,
                position_id=position.position_id,
                instrument_id=position.instrument_id,
                venue=position.venue,
                section=position.section.value,
                asset_class="unknown",
                quantity=position.quantity,
                multiplier=position.multiplier,
                price=position.price,
                market_value=position.market_value,
                currency=position.currency,
                settlement_currency=position.currency,
                as_of=position.as_of,
                source_ref=position.source,
                option=_json(position.option) if position.option is not None else None,
                metadata_json={
                    "prediction": _json(position.prediction)
                    if position.prediction is not None
                    else None,
                    "valuation_method": position.valuation_method,
                    "reconciliation_status": position.reconciliation_status.value,
                    "quality": position.quality.value,
                    "metadata": dict(position.metadata),
                },
            )
        )
    for order in snapshot.open_orders:
        session.add(
            PortfolioOpenOrderRow(
                id=fingerprint("order_row", snapshot.snapshot_id, order.order_id),
                snapshot_id=snapshot.snapshot_id,
                order_id=order.order_id,
                instrument_id=order.instrument_id,
                venue=order.venue,
                side=order.side.value,
                quantity=order.remaining_quantity,
                order_type=order.order_type,
                limit_price=order.limit_price,
                status=order.state,
                submitted_at=order.as_of,
                source_ref=order.source,
                metadata_json={
                    "section": order.section.value,
                    "reference_price": order.reference_price,
                    "multiplier": order.multiplier,
                    "currency": order.currency,
                    "reserved_capacity": order.reserved_capacity,
                    "reduces_position": order.reduces_position,
                    "quality": order.quality.value,
                    "metadata": dict(order.metadata),
                },
            )
        )
    for commitment in snapshot.commitments:
        session.add(
            PortfolioCommitmentRow(
                id=fingerprint(
                    "commitment_row", snapshot.snapshot_id, commitment.commitment_id
                ),
                snapshot_id=snapshot.snapshot_id,
                commitment_id=commitment.commitment_id,
                kind=commitment.kind.value,
                amount=commitment.amount,
                currency=commitment.currency,
                as_of=commitment.as_of,
                source_ref=commitment.source,
                metadata_json={
                    "section": commitment.section.value,
                    "resolved": commitment.resolved,
                    "included_in_reserved_capacity": commitment.included_in_reserved_capacity,
                    "quality": commitment.quality.value,
                    "description": commitment.description,
                },
            )
        )
    session.flush()
    return row


def save_exposure(session: Session, exposure: ExposureSnapshot) -> None:
    for kind, items in (
        ("position", exposure.instrument_exposures),
        ("order", exposure.order_exposures),
    ):
        for item in items:
            row_id = fingerprint(
                "expo_row", exposure.exposure_snapshot_id, item.instrument_id, kind
            )
            if session.get(PortfolioExposureRow, row_id) is not None:
                continue
            session.add(
                PortfolioExposureRow(
                    id=row_id,
                    exposure_snapshot_id=exposure.exposure_snapshot_id,
                    portfolio_snapshot_id=exposure.portfolio_snapshot_id,
                    instrument_id=item.instrument_id,
                    instrument_kind=kind,
                    section=item.section.value,
                    asset_class=item.asset_class.value,
                    venue=item.venue,
                    currency=item.currency,
                    base_currency=item.base_currency,
                    market_value=item.market_value,
                    gross_market_value=item.gross_market_value,
                    delta_equivalent=item.delta_equivalent,
                    delta=item.delta,
                    gamma=item.gamma,
                    vega=item.vega,
                    theta=item.theta,
                    underlying_price=item.underlying_price,
                    max_loss=item.max_loss,
                    margin_requirement=item.margin_requirement,
                    expected_payoff=item.expected_payoff,
                    liquidity_horizon_days=item.liquidity_horizon_days,
                    method=item.method,
                    model_version=item.model_version,
                    quality=item.quality.value,
                    as_of=item.as_of,
                    factor_exposures=dict(item.factor_exposures),
                    currency_exposures=dict(item.currency_exposures),
                    uncertainty_flags=list(item.uncertainty_flags),
                )
            )
    session.flush()


def save_budget(session: Session, budget: RiskBudgetProposal) -> PortfolioRiskBudgetRow:
    existing = session.get(PortfolioRiskBudgetRow, budget.budget_id)
    if existing is not None:
        return existing
    row = PortfolioRiskBudgetRow(
        id=budget.budget_id,
        budget_id=budget.budget_id,
        portfolio_snapshot_id=budget.portfolio_snapshot_id,
        method=budget.method,
        total_risk_budget=budget.total_risk_budget,
        section_budgets=_json(budget.section_budgets),
        policy_version=budget.policy_version,
        inputs_fingerprint=fingerprint("budget_inputs", budget),
        created_at=budget.created_at,
    )
    session.add(row)
    session.flush()
    return row


def save_proposal(
    session: Session, proposal: AllocationProposal
) -> PortfolioProposalRow:
    existing = session.get(PortfolioProposalRow, proposal.proposal_id)
    if existing is not None:
        return existing
    row = PortfolioProposalRow(
        id=proposal.proposal_id,
        proposal_id=proposal.proposal_id,
        portfolio_snapshot_id=proposal.portfolio_snapshot_id,
        exposure_snapshot_id=proposal.exposure_snapshot_id,
        risk_budget_proposal_id=proposal.risk_budget_proposal_id,
        policy_version=proposal.policy_version,
        optimizer_version=proposal.optimizer_version,
        method=proposal.method.value,
        solver_status=proposal.solver_status.value,
        approval_state=proposal.approval_state.value,
        action=proposal.action.value,
        stress_report_id=proposal.stress_report_id,
        stress_passed=proposal.stress_passed,
        data_quality_status=proposal.data_quality_status.value,
        estimated_turnover=proposal.estimated_turnover,
        expires_at=proposal.expires_at,
        created_at=proposal.created_at,
        input_cutoff_time=proposal.input_cutoff_time,
        idempotency_key=proposal.idempotency_key,
        requires_human_approval=proposal.requires_human_approval,
        payload=_json(proposal),
    )
    session.add(row)
    for target in proposal.proposed_targets:
        session.add(
            PortfolioProposalTargetRow(
                id=fingerprint("target_row", proposal.proposal_id, target.instrument_id),
                proposal_id=proposal.proposal_id,
                instrument_id=target.instrument_id,
                section=target.section.value,
                current_weight=target.current_weight,
                proposed_weight=target.proposed_weight,
                current_exposure=target.current_exposure,
                proposed_exposure=target.proposed_exposure,
                delta_exposure=target.delta_exposure,
                action=target.action.value,
                reason_codes=list(target.reason_codes),
            )
        )
    for result in proposal.constraint_results:
        session.add(
            PortfolioConstraintResultRow(
                id=fingerprint(
                    "constraint_row",
                    proposal.proposal_id,
                    result.name,
                    result.unit,
                ),
                proposal_id=proposal.proposal_id,
                name=result.name,
                severity=result.severity.value,
                passed=result.passed,
                actual=result.actual,
                limit=result.limit,
                unit=result.unit,
                message=result.message,
            )
        )
    session.flush()
    return row


def save_stress_report(session: Session, report: StressReport) -> PortfolioStressRunRow:
    existing = session.get(PortfolioStressRunRow, report.report_id)
    if existing is not None:
        return existing
    row = PortfolioStressRunRow(
        id=report.report_id,
        report_id=report.report_id,
        portfolio_snapshot_id=report.portfolio_snapshot_id,
        exposure_snapshot_id=report.exposure_snapshot_id,
        model_version=report.model_version,
        worst_case_loss=report.worst_case_loss,
        worst_scenario_id=report.worst_scenario_id,
        passed=report.passed,
        created_at=report.created_at,
        payload=_json(report),
    )
    session.add(row)
    session.flush()
    return row


def save_rebalance(
    session: Session, rebalance: RebalanceProposal
) -> PortfolioRebalanceRow:
    existing = session.get(PortfolioRebalanceRow, rebalance.rebalance_id)
    if existing is not None:
        return existing
    row = PortfolioRebalanceRow(
        id=rebalance.rebalance_id,
        rebalance_id=rebalance.rebalance_id,
        allocation_proposal_id=rebalance.allocation_proposal_id,
        portfolio_snapshot_id=rebalance.portfolio_snapshot_id,
        action=rebalance.action.value,
        validation_state=rebalance.validation_state.value,
        approval_state=rebalance.approval_state.value,
        required_capital=dict(rebalance.required_capital),
        expires_at=rebalance.expires_at,
        created_at=rebalance.created_at,
        idempotency_key=rebalance.idempotency_key,
        payload=_json(rebalance),
    )
    session.add(row)
    session.flush()
    return row


def save_reconciliation_issue(
    session: Session, issue: ReconciliationIssue
) -> PortfolioReconciliationIssueRow:
    existing = session.get(PortfolioReconciliationIssueRow, issue.issue_id)
    if existing is not None:
        return existing
    row = PortfolioReconciliationIssueRow(
        id=issue.issue_id,
        issue_id=issue.issue_id,
        source_systems=list(issue.source_systems),
        entity_type=issue.entity_type,
        entity_ref=issue.entity_ref,
        expected_value=dict(issue.expected_value),
        observed_value=dict(issue.observed_value),
        unit=issue.unit,
        detected_at=issue.detected_at,
        severity=issue.severity.value,
        status=issue.status.value,
        difference=issue.difference,
        resolution_ref=issue.resolution_ref,
        responsible=issue.responsible,
        blocks_risk_increase=issue.blocks_risk_increase,
    )
    session.add(row)
    session.flush()
    return row


def save_validation(
    session: Session,
    result: PortfolioValidationResult,
    *,
    human_approval_ref: str | None = None,
) -> PortfolioDecisionAuditRow:
    decision_id = fingerprint(
        "decision", result.proposal_id, result.decision.value, result.validated_at
    )
    existing = session.get(PortfolioDecisionAuditRow, decision_id)
    if existing is not None:
        return existing
    row = PortfolioDecisionAuditRow(
        id=decision_id,
        decision_id=decision_id,
        proposal_id=result.proposal_id,
        decision=result.decision.value,
        policy_version=result.policy_version,
        authorization_id=result.authorization_id,
        reason_codes=list(result.reason_codes),
        checks=[
            {
                "code": check.code,
                "passed": check.passed,
                "detail": check.detail,
                "hard": check.hard,
            }
            for check in result.checks
        ],
        validated_at=result.validated_at,
        execution_permitted=result.execution_permitted,
        human_approval_ref=human_approval_ref,
    )
    session.add(row)
    session.flush()
    return row


def save_alert(session: Session, alert: PortfolioAlert) -> PortfolioAlertRow:
    existing = session.get(PortfolioAlertRow, alert.alert_id)
    if existing is not None:
        return existing
    row = PortfolioAlertRow(
        id=alert.alert_id,
        alert_id=alert.alert_id,
        severity=alert.severity.value,
        code=alert.code,
        message=alert.message,
        detected_at=alert.detected_at,
        context=dict(alert.context),
    )
    session.add(row)
    session.flush()
    return row


def audit_questions(
    *,
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    proposal: AllocationProposal,
    stress_report: StressReport | None,
    validation: PortfolioValidationResult,
    execution_ref: str | None = None,
) -> Mapping[str, Any]:
    """Answer the ten audit questions without inventing unavailable facts."""
    return {
        "what_axel_knew": {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_time": snapshot.snapshot_time.isoformat(),
            "source_cutoff_time": snapshot.source_cutoff_time.isoformat(),
            "positions": [position.position_id for position in snapshot.positions],
            "open_orders": [order.order_id for order in snapshot.open_orders],
            "commitments": [
                commitment.commitment_id for commitment in snapshot.commitments
            ],
        },
        "missing_or_stale": {
            "data_quality": snapshot.data_quality_status.value,
            "completeness": snapshot.completeness_status.value,
            "known_limitations": list(snapshot.known_limitations),
            "exposure_failures": [
                {
                    "instrument_id": failure.instrument_id,
                    "reason": failure.reason,
                    "detail": failure.detail,
                }
                for failure in exposure.failures
            ],
        },
        "included_positions": [position.position_id for position in snapshot.positions],
        "model_and_config": {
            "snapshot_calculation_version": snapshot.calculation_version,
            "exposure_normalization_version": exposure.normalization_version,
            "optimizer_version": proposal.optimizer_version,
            "policy_version": proposal.policy_version,
            "proposal_method": proposal.method.value,
        },
        "constraints_evaluated": [
            {
                "name": check.name,
                "passed": check.passed,
                "severity": check.severity.value,
                "actual": check.actual,
                "limit": check.limit,
                "unit": check.unit,
            }
            for check in proposal.constraint_results
        ],
        "scenarios_run": (
            []
            if stress_report is None
            else [
                {
                    "scenario_id": result.scenario_id,
                    "version": result.scenario_version,
                    "passed": result.passed,
                    "loss": result.total_loss,
                    "hypothetical": result.hypothetical,
                }
                for result in stress_report.results
            ]
        ),
        "optimizer_proposal": {
            "proposal_id": proposal.proposal_id,
            "solver_status": proposal.solver_status.value,
            "action": proposal.action.value,
            "targets": [
                {
                    "instrument_id": target.instrument_id,
                    "proposed_weight": target.proposed_weight,
                    "delta_exposure": target.delta_exposure,
                }
                for target in proposal.proposed_targets
            ],
        },
        "deterministic_decision": {
            "decision": validation.decision.value,
            "authorized": validation.authorized,
            "reason_codes": list(validation.reason_codes),
            "authorization_id": validation.authorization_id,
        },
        "execution": {
            "execution_ref": execution_ref,
            "execution_permitted_by_validator": validation.execution_permitted,
            "note": "This package does not submit orders.",
        },
        "realized_vs_estimated": {
            "estimated_turnover": proposal.estimated_turnover,
            "estimated_cost": proposal.estimated_costs.total_cost,
            "realized_cost": None,
            "note": "Realized outcomes are populated by the execution and accounting systems.",
        },
    }


__all__ = [
    "audit_questions",
    "save_alert",
    "save_budget",
    "save_exposure",
    "save_proposal",
    "save_rebalance",
    "save_reconciliation_issue",
    "save_snapshot",
    "save_stress_report",
    "save_validation",
]
