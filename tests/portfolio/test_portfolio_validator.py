"""Deterministic authorization and governance-boundary tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from support import (
    NOW,
    make_budget,
    make_covariance,
    make_proposal_bundle,
    make_risk_report,
    make_stress_report,
    make_validation_context,
    make_validation_policy,
)

from axel.core.types import Section
from axel.portfolio.proposal import is_proposal_fresh
from axel.portfolio.types import (
    DataQuality,
    IssueSeverity,
    ProposalAction,
    ReconciliationIssue,
    SectionReadiness,
    SolverStatus,
)
from axel.portfolio.validator import (
    ExecutionMode,
    ValidationCode,
    validate_allocation_proposal,
    validate_rebalance_proposal,
)


def _kwargs(
    *,
    proposal,
    snapshot,
    exposure,
    policy,
    now=NOW,
    **overrides,
):
    values = {
        "validation_policy": make_validation_policy(),
        "stress_report": make_stress_report(snapshot, exposure, policy, now=now),
        "risk_report": make_risk_report(snapshot),
        "covariance": make_covariance(),
        "section_by_instrument": {
            "stock_a": Section.STOCKS,
            "stock_b": Section.STOCKS,
        },
        "expected_returns": (0.10, 0.12),
        "eligible_instruments": ("stock_a", "stock_b"),
        "available_cash": 100_000.0,
        "fully_invested": True,
        "allow_short": False,
    }
    values.update(overrides)
    return values


def _validate(
    proposal,
    rebalance,
    snapshot,
    exposure,
    policy,
    *,
    now=NOW,
    context=None,
    budget=None,
    **overrides,
):
    budget = budget or make_budget(snapshot, exposure, policy, now=now)
    return validate_allocation_proposal(
        proposal,
        snapshot,
        exposure,
        budget,
        policy,
        context or make_validation_context(now=now),
        **_kwargs(
            proposal=proposal,
            snapshot=snapshot,
            exposure=exposure,
            policy=policy,
            now=now,
            **overrides,
        ),
    )


def test_happy_path_allocation_and_rebalance_are_authorized() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    result = _validate(proposal, rebalance, snapshot, exposure, policy)
    assert result.decision.value == "accept"
    assert result.authorized
    assert result.execution_permitted
    assert result.authorization_id
    assert result.requires_human_approval
    assert "AUTHORIZED" in result.reason_codes
    assert result.validated_at == NOW

    budget = make_budget(snapshot, exposure, policy)
    rebalance_result = validate_rebalance_proposal(
        rebalance,
        proposal,
        snapshot,
        exposure,
        budget,
        policy,
        make_validation_context(),
        **_kwargs(
            proposal=proposal,
            snapshot=snapshot,
            exposure=exposure,
            policy=policy,
        ),
    )
    assert rebalance_result.authorized
    assert rebalance_result.proposal_id == rebalance.rebalance_id


def test_proposal_types_have_no_order_submission_surface() -> None:
    proposal, rebalance, *_ = make_proposal_bundle()
    assert not hasattr(proposal, "submit_order")
    assert not hasattr(proposal, "orders")
    assert not hasattr(rebalance, "submit_order")
    assert not hasattr(rebalance, "orders")
    assert proposal.approval_state.value == "pending_validation"
    assert rebalance.validation_state.value == "pending_validation"


def test_proposal_idempotency_key_is_deterministic_for_same_inputs() -> None:
    first, *_ = make_proposal_bundle()
    second, *_ = make_proposal_bundle()
    assert first.idempotency_key == second.idempotency_key
    assert first.proposal_id == second.proposal_id
    assert is_proposal_fresh(first, NOW)
    assert not is_proposal_fresh(first, first.expires_at + timedelta(seconds=1))


def test_stale_snapshot_rejects_risk_increase() -> None:
    stale_now = NOW - timedelta(seconds=400)
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle(now=stale_now)
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        now=NOW,
        stress_report=make_stress_report(snapshot, exposure, policy, now=stale_now),
    )
    assert result.decision.value == "reject"
    assert "SNAPSHOT_STALE" in result.reason_codes
    assert not result.execution_permitted


def test_kill_switch_blocks_increase_but_allows_reduction() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    blocked = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        context=make_validation_context(kill_switch_active=True),
    )
    assert blocked.decision.value == "reject"
    assert "KILL_SWITCH_ACTIVE" in blocked.reason_codes

    reduction_targets = tuple(
        replace(
            target,
            action=ProposalAction.REDUCE,
            proposed_weight=0.05,
            proposed_exposure=5_000.0,
            delta_exposure=-5_000.0,
        )
        for target in proposal.proposed_targets
    )
    reduced = replace(
        proposal,
        action=ProposalAction.REDUCE,
        proposed_targets=reduction_targets,
        estimated_turnover=0.10,
    )
    allowed = _validate(
        reduced,
        rebalance,
        snapshot,
        exposure,
        policy,
        context=make_validation_context(kill_switch_active=True, human_approval_ref=None),
        fully_invested=False,
    )
    assert allowed.decision.value == "accept"
    assert allowed.authorized
    assert not allowed.requires_human_approval


def test_missing_human_approval_requires_review_not_authorization() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        context=make_validation_context(human_approval_ref=None),
    )
    assert result.decision.value == "require_review"
    assert not result.authorized
    assert not result.execution_permitted
    assert "APPROVAL_REQUIRED" in result.reason_codes
    assert result.requires_human_approval


def test_policy_can_waive_human_approval_only_explicitly() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        context=make_validation_context(human_approval_ref=None),
        validation_policy=make_validation_policy(require_human_approval_for_increase=False),
    )
    assert result.decision.value == "accept"
    assert result.authorized


def test_duplicate_idempotency_key_is_rejected() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        context=make_validation_context(
            seen_idempotency_keys=frozenset({proposal.idempotency_key})
        ),
    )
    assert result.decision.value == "reject"
    assert "DUPLICATE_IDEMPOTENCY_KEY" in result.reason_codes


def test_missing_stress_report_fails_closed() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        stress_report=None,
    )
    assert result.decision.value == "reject"
    assert "MISSING_STRESS_REPORT" in result.reason_codes


def test_failed_stress_report_is_rejected() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    failing = replace(
        make_stress_report(snapshot, exposure, policy),
        passed=False,
    )
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        stress_report=failing,
    )
    assert result.decision.value == "reject"
    assert "STRESS_FAILED" in result.reason_codes


def test_mismatched_stress_provenance_is_rejected() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    report = replace(
        make_stress_report(snapshot, exposure, policy),
        portfolio_snapshot_id="other_snapshot",
    )
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        stress_report=report,
    )
    assert result.decision.value == "reject"
    assert "PROVENANCE_MISMATCH" in result.reason_codes


def test_budget_mismatch_is_rejected_independently() -> None:
    proposal, _rebalance, snapshot, exposure, policy = make_proposal_bundle()
    budget = replace(
        make_budget(snapshot, exposure, policy),
        total_risk_budget=200_000.0,
    )
    result = validate_allocation_proposal(
        proposal,
        snapshot,
        exposure,
        budget,
        policy,
        make_validation_context(),
        **_kwargs(
            proposal=proposal,
            snapshot=snapshot,
            exposure=exposure,
            policy=policy,
        ),
    )
    assert result.decision.value == "reject"
    assert "BUDGET_RECONCILIATION_FAILED" in result.reason_codes


def test_open_reconciliation_issue_blocks_increase() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    issue = ReconciliationIssue(
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
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        context=make_validation_context(reconciliation_issues=(issue,)),
    )
    assert result.decision.value == "reject"
    assert "RECONCILIATION_BLOCK" in result.reason_codes


def test_degraded_data_quality_blocks_increase() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    degraded = replace(snapshot, data_quality_status=DataQuality.DEGRADED)
    result = _validate(
        proposal,
        rebalance,
        degraded,
        exposure,
        policy,
        stress_report=make_stress_report(degraded, exposure, policy),
    )
    assert result.decision.value == "reject"
    assert "DATA_QUALITY_DEGRADED" in result.reason_codes


def test_paper_only_section_blocks_live_increase() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    statuses = dict(snapshot.section_status)
    statuses[Section.STOCKS.value] = replace(
        statuses[Section.STOCKS.value],
        readiness=SectionReadiness.PAPER_ONLY,
    )
    paper_only = replace(snapshot, section_status=statuses)
    result = _validate(
        proposal,
        rebalance,
        paper_only,
        exposure,
        policy,
        stress_report=make_stress_report(paper_only, exposure, policy),
    )
    assert result.decision.value == "reject"
    assert "SECTION_NOT_ELIGIBLE" in result.reason_codes


def test_paper_only_section_is_allowed_in_paper_mode() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    original_budget = make_budget(snapshot, exposure, policy)
    statuses = dict(snapshot.section_status)
    statuses[Section.STOCKS.value] = replace(
        statuses[Section.STOCKS.value],
        readiness=SectionReadiness.PAPER_ONLY,
    )
    paper_only = replace(snapshot, section_status=statuses)
    result = _validate(
        proposal,
        rebalance,
        paper_only,
        exposure,
        policy,
        context=make_validation_context(execution_mode=ExecutionMode.PAPER),
        budget=original_budget,
        stress_report=make_stress_report(paper_only, exposure, policy),
    )
    assert result.decision.value == "accept"


def test_solver_failure_and_universe_mismatch_are_rejected() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    failed = replace(proposal, solver_status=SolverStatus.SOLVER_ERROR)
    result = _validate(failed, rebalance, snapshot, exposure, policy)
    assert result.decision.value == "reject"
    assert "SOLVER_FAILED" in result.reason_codes

    outside = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        eligible_instruments=("stock_a",),
    )
    assert outside.decision.value == "reject"
    assert "UNSUPPORTED_INSTRUMENT" in outside.reason_codes


def test_missing_covariance_fails_closed_for_increase() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    result = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        covariance=None,
    )
    assert result.decision.value == "reject"
    assert "SCHEMA_INVALID" in result.reason_codes


def test_provenance_and_expiry_are_rejected() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    wrong = replace(proposal, portfolio_snapshot_id="other_snapshot")
    result = _validate(wrong, rebalance, snapshot, exposure, policy)
    assert result.decision.value == "reject"
    assert "PROVENANCE_MISMATCH" in result.reason_codes

    expired = _validate(
        proposal,
        rebalance,
        snapshot,
        exposure,
        policy,
        context=make_validation_context(now=proposal.expires_at + timedelta(seconds=1)),
    )
    assert expired.decision.value == "reject"
    assert "PROPOSAL_EXPIRED" in expired.reason_codes


def test_rebalance_capital_and_change_mismatch_are_rejected() -> None:
    proposal, rebalance, snapshot, exposure, policy = make_proposal_bundle()
    budget = make_budget(snapshot, exposure, policy)
    kwargs = _kwargs(
        proposal=proposal,
        snapshot=snapshot,
        exposure=exposure,
        policy=policy,
    )
    underfunded = replace(
        rebalance,
        required_capital={"total": 200_000.0},
    )
    result = validate_rebalance_proposal(
        underfunded,
        proposal,
        snapshot,
        exposure,
        budget,
        policy,
        make_validation_context(),
        **kwargs,
    )
    assert result.decision.value == "reject"
    assert "INSUFFICIENT_CAPITAL" in result.reason_codes

    first = rebalance.changes[0]
    mismatched = replace(
        rebalance,
        changes=(replace(first, delta_exposure=first.delta_exposure + 1.0),),
    )
    result = validate_rebalance_proposal(
        mismatched,
        proposal,
        snapshot,
        exposure,
        budget,
        policy,
        make_validation_context(),
        **kwargs,
    )
    assert result.decision.value == "reject"
    assert "REBALANCE_MISMATCH" in result.reason_codes


def test_validation_codes_are_stable_strings() -> None:
    assert ValidationCode.AUTHORIZED.value == "AUTHORIZED"
    assert ValidationCode.KILL_SWITCH_ACTIVE.value == "KILL_SWITCH_ACTIVE"
