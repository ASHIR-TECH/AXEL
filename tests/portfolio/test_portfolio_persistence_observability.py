"""Persistence, audit, metrics, events, and alert tests."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker
from support import (
    NOW,
    make_budget,
    make_covariance,
    make_policy,
    make_proposal_bundle,
    make_risk_report,
    make_stress_report,
    make_validation_context,
    make_validation_policy,
)

from axel.core.types import Section
from axel.db.session import init_db
from axel.portfolio.db_models import (
    PortfolioAlertRow,
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
    PortfolioStressRunRow,
)
from axel.portfolio.observability import (
    EventType,
    MetricUnit,
    PortfolioEvent,
    PortfolioMetric,
    alert_for_blocking_issue,
    alert_for_stress_failure,
    alert_for_validation_rejection,
    proposal_event,
    proposal_metrics,
    reconciliation_metrics,
    render_prometheus,
    section_metrics,
    snapshot_built_event,
    snapshot_metrics,
    stress_event,
    stress_metrics,
    validation_event,
    validation_metrics,
)
from axel.portfolio.persistence import (
    audit_questions,
    save_alert,
    save_budget,
    save_exposure,
    save_proposal,
    save_rebalance,
    save_reconciliation_issue,
    save_snapshot,
    save_stress_report,
    save_validation,
)
from axel.portfolio.types import (
    IssueSeverity,
    ProposalAction,
    ReconciliationIssue,
)
from axel.portfolio.validator import validate_allocation_proposal


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session, engine
    finally:
        session.close()
        engine.dispose()


def _bundle():
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    budget = make_budget(snapshot, exposure, policy)
    stress = make_stress_report(snapshot, exposure, policy)
    risk = make_risk_report(snapshot)
    result = validate_allocation_proposal(
        proposal,
        snapshot,
        exposure,
        budget,
        policy,
        make_validation_context(),
        validation_policy=make_validation_policy(),
        stress_report=stress,
        risk_report=risk,
        covariance=make_covariance(),
        section_by_instrument={
            "stock_a": Section.STOCKS,
            "stock_b": Section.STOCKS,
        },
        expected_returns=(0.10, 0.12),
        eligible_instruments=("stock_a", "stock_b"),
        available_cash=100_000.0,
    )
    return proposal, rebalance, snapshot, exposure, budget, stress, result


def _issue() -> ReconciliationIssue:
    return ReconciliationIssue(
        issue_id="issue_1",
        source_systems=("broker", "axel"),
        entity_type="position",
        entity_ref="pos_stock_a",
        expected_value={"quantity": 100.0},
        observed_value={"quantity": 90.0},
        unit="quantity",
        detected_at=NOW,
        severity=IssueSeverity.CRITICAL,
        difference=10.0,
    )


def test_init_db_registers_phase11_tables(db_session) -> None:
    _session, engine = db_session
    tables = set(inspect(engine).get_table_names())
    assert "portfolio_snapshots" in tables
    assert "portfolio_proposals" in tables
    assert "portfolio_decision_audits" in tables
    assert "portfolio_stress_runs" in tables
    assert "portfolio_rebalances" in tables


def test_persistence_round_trip_and_idempotency(db_session) -> None:
    session, _engine = db_session
    proposal, rebalance, snapshot, exposure, budget, stress, result = _bundle()
    save_snapshot(session, snapshot)
    save_exposure(session, exposure)
    save_budget(session, budget)
    save_proposal(session, proposal)
    save_stress_report(session, stress)
    save_rebalance(session, rebalance)
    save_validation(session, result, human_approval_ref="approval-test-1")
    save_reconciliation_issue(session, _issue())
    save_alert(session, alert_for_blocking_issue(_issue(), now=NOW))
    session.commit()

    assert session.scalar(select(func.count()).select_from(PortfolioSnapshotRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioPositionRow)) == 2
    assert session.scalar(select(func.count()).select_from(PortfolioExposureRow)) == 2
    assert session.scalar(select(func.count()).select_from(PortfolioRiskBudgetRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioProposalRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioProposalTargetRow)) == 2
    assert session.scalar(select(func.count()).select_from(PortfolioStressRunRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioRebalanceRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioDecisionAuditRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioReconciliationIssueRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioAlertRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioOpenOrderRow)) == 0

    # Saving the same immutable objects is idempotent.
    save_snapshot(session, snapshot)
    save_budget(session, budget)
    save_proposal(session, proposal)
    save_stress_report(session, stress)
    save_rebalance(session, rebalance)
    save_validation(session, result)
    session.commit()
    assert session.scalar(select(func.count()).select_from(PortfolioSnapshotRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioProposalRow)) == 1
    assert session.scalar(select(func.count()).select_from(PortfolioDecisionAuditRow)) == 1


def test_audit_answers_ten_questions_without_fabricating_outcomes(db_session) -> None:
    session, _engine = db_session
    proposal, _rebalance, snapshot, exposure, budget, stress, result = _bundle()
    save_snapshot(session, snapshot)
    save_exposure(session, exposure)
    save_budget(session, budget)
    save_proposal(session, proposal)
    save_stress_report(session, stress)
    save_validation(session, result)
    session.commit()

    answers = audit_questions(
        snapshot=snapshot,
        exposure=exposure,
        proposal=proposal,
        stress_report=stress,
        validation=result,
        execution_ref=None,
    )
    assert set(answers) == {
        "what_axel_knew",
        "missing_or_stale",
        "included_positions",
        "model_and_config",
        "constraints_evaluated",
        "scenarios_run",
        "optimizer_proposal",
        "deterministic_decision",
        "execution",
        "realized_vs_estimated",
    }
    assert answers["execution"]["execution_permitted_by_validator"] is True
    assert answers["execution"]["execution_ref"] is None
    assert answers["realized_vs_estimated"]["realized_cost"] is None
    assert answers["deterministic_decision"]["authorized"] is True
    assert len(answers["scenarios_run"]) == len(stress.results)


def test_metrics_are_deterministic_and_renderable(db_session) -> None:
    _session, _engine = db_session
    proposal, _rebalance, snapshot, exposure, _budget, stress, result = _bundle()
    metrics = (
        snapshot_metrics(snapshot, exposure, now=NOW)
        + proposal_metrics(proposal, now=NOW)
        + validation_metrics(result)
        + stress_metrics(stress)
        + reconciliation_metrics((_issue(),), now=NOW)
        + section_metrics(exposure, now=NOW)
    )
    assert metrics
    assert all(isinstance(metric, PortfolioMetric) for metric in metrics)
    assert all(metric.unit in MetricUnit for metric in metrics)
    rendered = render_prometheus(metrics)
    assert "axel_portfolio_equity" in rendered
    assert "axel_portfolio_proposal_turnover" in rendered
    assert "axel_portfolio_stress_scenario_loss" in rendered
    assert "axel_portfolio_reconciliation_blocking_issues 1" in rendered
    assert render_prometheus(metrics) == rendered

    escaped = render_prometheus(
        (
            PortfolioMetric(
                "axel_test",
                1.0,
                MetricUnit.COUNT,
                {"label": 'a"b\nc'},
                NOW,
            ),
        )
    )
    assert 'label="a\\"b\\nc"' in escaped


def test_events_are_stable_and_idempotent() -> None:
    proposal, _rebalance, snapshot, exposure, _budget, stress, result = _bundle()
    snapshot_event = snapshot_built_event(snapshot, exposure)
    proposal_evt = proposal_event(proposal)
    stress_evt = stress_event(stress)
    validation_evt = validation_event(result)
    assert isinstance(snapshot_event, PortfolioEvent)
    assert snapshot_event.event_type == EventType.SNAPSHOT_BUILT
    assert proposal_evt.event_type == EventType.ALLOCATION_PROPOSED
    assert stress_evt.event_type == EventType.STRESS_COMPLETED
    assert validation_evt.event_type == EventType.PROPOSAL_VALIDATED
    assert snapshot_event.event_id == snapshot_built_event(snapshot, exposure).event_id
    assert proposal_evt.event_id == proposal_event(proposal).event_id
    assert proposal_evt.as_dict()["event_type"] == "portfolio.allocation.proposed"
    assert proposal_evt.as_dict()["payload"]["target_count"] == 2


def test_alerts_cover_reconciliation_stress_and_rejection() -> None:
    proposal, _rebalance, snapshot, exposure, budget, stress, _result = _bundle()
    issue_alert = alert_for_blocking_issue(_issue(), now=NOW)
    assert issue_alert.severity == IssueSeverity.CRITICAL
    assert issue_alert.code == "RECONCILIATION_BLOCKING_ISSUE"
    stress_alert = alert_for_stress_failure(
        type(stress)(
            **{
                **stress.__dict__,
                "passed": False,
                "worst_case_loss": 1_000.0,
            }
        ),
        now=NOW,
    )
    assert stress_alert.code == "STRESS_SUITE_FAILED"
    rejected = validate_allocation_proposal(
        proposal,
        snapshot,
        exposure,
        budget,
        make_policy(),
        make_validation_context(),
        validation_policy=make_validation_policy(),
        stress_report=None,
        risk_report=make_risk_report(snapshot),
        covariance=make_covariance(),
        section_by_instrument={
            "stock_a": Section.STOCKS,
            "stock_b": Section.STOCKS,
        },
        eligible_instruments=("stock_a", "stock_b"),
        available_cash=100_000.0,
    )
    assert rejected.decision.value == "reject"
    rejection_alert = alert_for_validation_rejection(rejected, now=NOW)
    assert rejection_alert is not None
    assert rejection_alert.code == "PROPOSAL_REJECTED"
    assert "MISSING_STRESS_REPORT" in rejection_alert.message


def test_proposal_event_marks_deferred_proposals() -> None:
    proposal, _rebalance, *_ = make_proposal_bundle()
    deferred = type(proposal)(
        **{
            **proposal.__dict__,
            "action": ProposalAction.DEFER,
            "proposed_targets": (),
        }
    )
    event = proposal_event(deferred)
    assert event.event_type == EventType.PROPOSAL_DEFERRED
