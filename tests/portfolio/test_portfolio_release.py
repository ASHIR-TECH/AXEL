"""Shadow-mode evidence and release-gate tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from support import NOW

from axel.portfolio.release import (
    ReleaseGateError,
    ReleaseGateResult,
    ReleaseStatus,
    ShadowCriteria,
    ShadowObservation,
    collect_shadow_metrics,
    evaluate_release_gate,
)
from axel.portfolio.simulation import SimulationStatus
from axel.portfolio.types import ProposalAction, SolverStatus, ValidationDecision


def _criteria(**overrides) -> ShadowCriteria:
    values = {
        "criteria_version": "shadow-2026-10",
        "approved_at": NOW - timedelta(days=1),
        "effective_at": NOW,
        "minimum_shadow_days": 14,
        "minimum_proposals": 20,
        "minimum_simulations": 20,
        "max_solver_failure_rate": 0.0,
        "max_rejection_rate": 0.0,
        "max_stale_rate": 0.0,
        "max_open_reconciliation_issues": 0,
        "minimum_stress_pass_rate": 1.0,
        "max_proposal_age_seconds": 300.0,
        "max_cost_forecast_error_bps": 10.0,
        "minimum_completeness_rate": 1.0,
        "minimum_freshness_rate": 1.0,
        "minimum_reconciliation_rate": 1.0,
    }
    values.update(overrides)
    return ShadowCriteria(**values)


def _observation(index: int, **overrides) -> ShadowObservation:
    values = {
        "observed_at": NOW + timedelta(days=index),
        "snapshot_complete": True,
        "snapshot_fresh": True,
        "reconciled": True,
        "solver_status": SolverStatus.OPTIMAL,
        "proposal_action": ProposalAction.INCREASE,
        "validation_decision": ValidationDecision.ACCEPT,
        "stress_passed": True,
        "proposal_age_seconds": 30.0,
        "estimated_turnover": 0.1,
        "predicted_cost": 100.0,
        "realized_cost": 101.0,
        "simulation_status": SimulationStatus.COMPLETED,
    }
    values.update(overrides)
    return ShadowObservation(**values)


def _observations(count: int = 20) -> tuple[ShadowObservation, ...]:
    return tuple(_observation(index) for index in range(count))


def test_shadow_metrics_aggregate_without_hiding_missing_outcomes() -> None:
    observations = _observations()
    metrics = collect_shadow_metrics(
        observations,
        period_start=NOW,
        period_end=NOW + timedelta(days=20),
    )
    assert metrics.observations == 20
    assert metrics.proposals == 20
    assert metrics.simulations == 20
    assert metrics.shadow_days == pytest.approx(20.0)
    assert metrics.solver_failure_rate == 0.0
    assert metrics.stress_pass_rate == 1.0
    assert metrics.cost_forecast_error_bps == pytest.approx(100.0)
    assert metrics.live_order_count == 0
    assert metrics.warnings == ()


def test_shadow_metrics_flag_missing_realized_costs() -> None:
    observations = tuple(
        _observation(index, realized_cost=None) for index in range(20)
    )
    metrics = collect_shadow_metrics(
        observations,
        period_start=NOW,
        period_end=NOW + timedelta(days=20),
    )
    assert metrics.realized_cost is None
    assert metrics.cost_forecast_error_bps is None
    assert any("realized costs" in warning for warning in metrics.warnings)


def test_release_gate_approves_only_evidence_for_human_review() -> None:
    criteria = _criteria()
    metrics = collect_shadow_metrics(
        tuple(_observation(index, realized_cost=100.05) for index in range(20)),
        period_start=NOW,
        period_end=NOW + timedelta(days=20),
    )
    result = evaluate_release_gate(
        metrics,
        criteria,
        evaluated_at=NOW + timedelta(days=21),
    )
    assert result.status == ReleaseStatus.APPROVED_FOR_HUMAN_REVIEW
    assert result.approved_for_human_review
    assert not result.blockers
    assert not result.live_execution_enabled
    assert result.requires_human_approval
    assert result.criteria_fingerprint.startswith("release_criteria_")
    assert any("human decision" in warning for warning in result.warnings)


def test_release_gate_blocks_every_material_failure() -> None:
    observations = list(_observations())
    observations[0] = replace(
        observations[0],
        snapshot_complete=False,
        snapshot_fresh=False,
        reconciled=False,
        solver_status=SolverStatus.SOLVER_ERROR,
        validation_decision=ValidationDecision.REJECT,
        stress_passed=False,
        proposal_age_seconds=10_000.0,
        live_orders_submitted=True,
        watchdog_halted=True,
        open_reconciliation_issues=1,
        realized_cost=10_000.0,
    )
    metrics = collect_shadow_metrics(
        observations,
        period_start=NOW,
        period_end=NOW + timedelta(days=20),
    )
    result = evaluate_release_gate(
        metrics,
        _criteria(),
        evaluated_at=NOW + timedelta(days=21),
    )
    assert result.status == ReleaseStatus.BLOCKED
    assert {
        "stale_rate_exceeded",
        "solver_failure_rate_exceeded",
        "rejection_rate_exceeded",
        "open_reconciliation_issues",
        "stress_pass_rate_below_minimum",
        "proposal_age_exceeded",
        "cost_forecast_error_exceeded",
        "completeness_rate_below_minimum",
        "freshness_rate_below_minimum",
        "reconciliation_rate_below_minimum",
        "live_orders_observed_in_shadow_mode",
        "watchdog_halt_observed",
    } <= set(result.blockers)
    assert not result.live_execution_enabled


def test_release_gate_blocks_missing_stress_and_cost_evidence() -> None:
    observations = tuple(
        _observation(index, stress_passed=None, realized_cost=None)
        for index in range(20)
    )
    metrics = collect_shadow_metrics(
        observations,
        period_start=NOW,
        period_end=NOW + timedelta(days=20),
    )
    result = evaluate_release_gate(
        metrics,
        _criteria(),
        evaluated_at=NOW + timedelta(days=21),
    )
    assert "missing_stress_evidence" in result.blockers
    assert "missing_realized_cost_evidence" in result.blockers


def test_release_gate_requires_pre_registration_and_complete_period() -> None:
    metrics = collect_shadow_metrics(
        _observations(),
        period_start=NOW,
        period_end=NOW + timedelta(days=20),
    )
    late = _criteria(approved_at=NOW + timedelta(days=1), effective_at=NOW + timedelta(days=2))
    result = evaluate_release_gate(
        metrics,
        late,
        evaluated_at=NOW + timedelta(days=21),
    )
    assert "criteria_not_pre_registered" in result.blockers
    result = evaluate_release_gate(
        metrics,
        _criteria(),
        evaluated_at=NOW + timedelta(days=5),
    )
    assert "shadow_period_not_complete" in result.blockers


def test_shadow_metrics_reject_empty_or_out_of_period_observations() -> None:
    with pytest.raises(ReleaseGateError):
        collect_shadow_metrics(
            (),
            period_start=NOW,
            period_end=NOW + timedelta(days=1),
        )
    with pytest.raises(ReleaseGateError):
        collect_shadow_metrics(
            (_observation(0),),
            period_start=NOW + timedelta(days=1),
            period_end=NOW + timedelta(days=2),
        )


def test_release_gate_cannot_enable_live_execution() -> None:
    metrics = collect_shadow_metrics(
        _observations(),
        period_start=NOW,
        period_end=NOW + timedelta(days=20),
    )
    result = evaluate_release_gate(
        metrics,
        _criteria(),
        evaluated_at=NOW + timedelta(days=21),
    )
    with pytest.raises(ReleaseGateError, match="cannot enable live"):
        ReleaseGateResult(
            **{
                **result.__dict__,
                "live_execution_enabled": True,
            }
        )


def test_shadow_criteria_validate_thresholds() -> None:
    with pytest.raises(ReleaseGateError):
        _criteria(max_solver_failure_rate=1.5)
    with pytest.raises(ReleaseGateError):
        _criteria(minimum_shadow_days=-1)
    with pytest.raises(ReleaseGateError):
        _criteria(
            approved_at=NOW,
            effective_at=NOW - timedelta(days=1),
        )
