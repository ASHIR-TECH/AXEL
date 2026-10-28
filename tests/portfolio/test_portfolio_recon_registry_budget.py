"""Reconciliation, registry, and risk-budget tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from support import (
    NOW,
    all_live_statuses,
    make_budget,
    make_exposure,
    make_policy,
    make_registry,
    make_snapshot,
    stock_position,
)

from axel.core.types import Section
from axel.portfolio.budget import (
    available_risk_capital,
    propose_risk_budgets,
    validate_budget_reconciliation,
)
from axel.portfolio.recon import (
    ReconciliationTolerance,
    blocks_new_risk,
    critical_open_issues,
    merge_issues,
    reconcile_positions,
    reconciliation_status_from_issues,
    resolve_issue,
    summarize_issues,
)
from axel.portfolio.registry import (
    DuplicateInstrumentError,
    InstrumentRegistry,
    UnknownInstrumentError,
    factor_kind_for_section,
    factor_model_covariance,
)
from axel.portfolio.types import (
    DataQuality,
    FactorKind,
    InstrumentEligibility,
    ReconciliationStatus,
)


def test_reconcile_positions_detects_quantity_and_value_drift() -> None:
    internal = stock_position("stock_a", quantity=100.0, price=100.0)
    authoritative = stock_position("stock_a", quantity=90.0, price=100.0)
    issues = reconcile_positions((internal,), (authoritative,), NOW)
    assert issues
    assert all(issue.entity_type == "position" for issue in issues)
    assert any(issue.unit == "quantity" for issue in issues)
    assert any(issue.difference == pytest.approx(10.0) for issue in issues)
    assert blocks_new_risk(issues)
    assert reconciliation_status_from_issues(issues) == ReconciliationStatus.MISMATCH
    assert critical_open_issues(issues) == issues
    summary = summarize_issues(issues)
    assert summary["open"] == len(issues)
    assert summary["blocking"] == len(issues)


def test_reconcile_positions_tolerance_absorbs_small_drift() -> None:
    internal = stock_position("stock_a", quantity=100.0, price=100.0)
    authoritative = stock_position("stock_a", quantity=100.0, price=100.0)
    issues = reconcile_positions(
        (internal,),
        (authoritative,),
        NOW,
        tolerance=ReconciliationTolerance(absolute=0.01, relative=1e-6),
    )
    assert issues == ()


def test_reconciliation_issue_resolution_and_idempotent_merge() -> None:
    internal = stock_position("stock_a", quantity=100.0, price=100.0)
    authoritative = stock_position("stock_a", quantity=90.0, price=100.0)
    issues = reconcile_positions((internal,), (authoritative,), NOW)
    resolved = resolve_issue(
        issues[0],
        resolution_ref="broker-statement-1",
        responsible="ops",
    )
    assert resolved.status.value == "resolved"
    assert not resolved.blocks_new_risk
    merged = merge_issues((resolved,), issues)
    assert merged[0].status.value == "resolved"
    assert len(merged) == len(issues)


def test_registry_duplicate_and_unknown_are_explicit() -> None:
    registry = make_registry()
    spec = registry.require("stock_a")
    with pytest.raises(DuplicateInstrumentError):
        registry.register(replace(spec, symbol="DIFFERENT"))
    with pytest.raises(UnknownInstrumentError):
        registry.require("missing")
    assert registry.instrument_ids == ("stock_a", "stock_b")
    assert factor_kind_for_section("options") == FactorKind.VOLATILITY
    assert factor_kind_for_section("unknown") == FactorKind.CREDIT


def test_universe_validation_rejects_research_only_and_expired() -> None:
    registry = InstrumentRegistry()
    spec = make_registry().require("stock_a")
    registry.register(replace(spec, eligibility=InstrumentEligibility.RESEARCH_ONLY))
    validation = registry.validate_universe(("stock_a",), as_of=NOW)
    assert not validation.is_valid
    assert validation.issues[0].code == "RESEARCH_ONLY"
    research = registry.validate_universe(("stock_a",), as_of=NOW, allow_research=True)
    assert research.is_valid
    expired = registry.validate_universe(
        ("stock_a",),
        as_of=NOW + timedelta(days=10_000),
        allow_research=True,
    )
    assert expired.is_valid  # no expiry on a stock


def test_factor_model_covariance_is_psd() -> None:
    covariance = factor_model_covariance(
        [[1.0, 0.5], [0.8, -0.2]],
        [[0.04, 0.01], [0.01, 0.09]],
        [0.01, 0.02],
    )
    from axel.portfolio.returns import covariance_diagnostics

    assert covariance_diagnostics(covariance).is_valid
    with pytest.raises(ValueError):
        factor_model_covariance([[1.0]], [[0.04]], [0.01, 0.02])


def test_available_risk_capital_applies_all_caps() -> None:
    snapshot = make_snapshot()
    policy = make_policy(global_max_gross_exposure=50_000.0)
    cap, binding, warnings = available_risk_capital(snapshot, policy)
    assert cap == pytest.approx(50_000.0)
    assert "global_max_gross_exposure" in binding
    assert warnings == ()


def test_available_risk_capital_haircuts_incomplete_snapshot() -> None:
    snapshot = make_snapshot(
        statuses=tuple(
            replace(status, data_quality=DataQuality.DEGRADED)
            for status in all_live_statuses()
        )
    )
    policy = make_policy()
    cap, binding, warnings = available_risk_capital(snapshot, policy)
    assert cap == pytest.approx(50_000.0)
    assert "incomplete_data_haircut" in binding
    assert warnings


def test_risk_budget_proposal_reconciles_and_has_all_sections() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    policy = make_policy()
    budget = make_budget(snapshot, exposure, policy)
    assert budget.is_valid
    assert budget.total_risk_budget == pytest.approx(100_000.0)
    assert {item.section for item in budget.section_budgets} == set(Section)
    validation = validate_budget_reconciliation(budget, policy)
    assert validation.passed
    assert budget.budget_for(Section.STOCKS).risk_budget == pytest.approx(20_000.0)


def test_budget_reconciliation_rejects_over_cap() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    policy = make_policy(global_max_gross_exposure=100_000.0)
    budget = make_budget(snapshot, exposure, policy)
    over = replace(budget, total_risk_budget=200_000.0)
    validation = validate_budget_reconciliation(over, policy)
    assert not validation.passed
    assert any("global_budget_cap" in check.name for check in validation.checks)


def test_budget_with_no_eligible_sections_is_invalid() -> None:
    snapshot = make_snapshot(
        statuses=tuple(
            replace(status, readiness=status.readiness.DISABLED)
            for status in all_live_statuses()
        )
    )
    exposure = make_exposure(snapshot)
    policy = make_policy()
    budget = propose_risk_budgets(snapshot, exposure, policy, now=NOW)
    assert not budget.is_valid
    assert budget.total_risk_budget == 0.0
    assert "no_eligible_sections" in budget.binding_constraints
