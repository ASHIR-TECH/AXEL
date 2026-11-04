"""Optimizer, baseline, stability, and independent hard-constraint tests."""

from __future__ import annotations

import pytest
from support import (
    NOW,
    make_budget,
    make_covariance,
    make_exposure,
    make_policy,
    make_snapshot,
    stock_position,
)

from axel.core.types import Section
from axel.portfolio.constraints import (
    evaluate_allocation_constraints,
    failed_hard_constraints,
    hard_constraints_pass,
)
from axel.portfolio.optimizer import (
    compare_with_baseline,
    generate_allocation,
    stability_analysis,
)
from axel.portfolio.types import (
    OptimizationConfig,
    OptimizationMethod,
    SolverStatus,
)


def _section_map() -> dict[str, Section]:
    return {"stock_a": Section.STOCKS, "stock_b": Section.STOCKS}


def test_equal_weight_allocation_succeeds_and_passes_constraints() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    policy = make_policy()
    budget = make_budget(snapshot, exposure, policy)
    result = generate_allocation(
        snapshot=snapshot,
        exposure=exposure,
        budget=budget,
        config=OptimizationConfig(method=OptimizationMethod.EQUAL_WEIGHT),
        covariance=make_covariance(),
        instrument_ids=("stock_a", "stock_b"),
        section_by_instrument=_section_map(),
        current_weights=(0.1, 0.1),
        policy=policy,
        stress_passed=True,
        eligible_instruments=("stock_a", "stock_b"),
        available_cash=100_000.0,
        now=NOW,
    )
    assert result.status == SolverStatus.OPTIMAL
    assert result.weights == pytest.approx((0.5, 0.5))
    assert result.turnover == pytest.approx(0.8)
    assert hard_constraints_pass(result.constraint_results)
    assert result.expected_volatility is not None
    assert result.expected_volatility > 0


def test_minimum_variance_improves_on_equal_weight() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    policy = make_policy()
    budget = make_budget(snapshot, exposure, policy)
    equal = generate_allocation(
        snapshot=snapshot,
        exposure=exposure,
        budget=budget,
        config=OptimizationConfig(method=OptimizationMethod.EQUAL_WEIGHT),
        covariance=make_covariance(),
        instrument_ids=("stock_a", "stock_b"),
        section_by_instrument=_section_map(),
        current_weights=(0.5, 0.5),
        policy=policy,
        stress_passed=True,
        eligible_instruments=("stock_a", "stock_b"),
        available_cash=100_000.0,
        now=NOW,
    )
    minimum = generate_allocation(
        snapshot=snapshot,
        exposure=exposure,
        budget=budget,
        config=OptimizationConfig(
            method=OptimizationMethod.MINIMUM_VARIANCE,
            max_iterations=2_000,
            tolerance=1e-8,
        ),
        covariance=make_covariance(),
        instrument_ids=("stock_a", "stock_b"),
        section_by_instrument=_section_map(),
        current_weights=(0.5, 0.5),
        policy=policy,
        stress_passed=True,
        eligible_instruments=("stock_a", "stock_b"),
        available_cash=100_000.0,
        now=NOW,
    )
    assert equal.successful
    assert minimum.successful
    assert minimum.weights[0] > minimum.weights[1]
    assert minimum.expected_volatility < equal.expected_volatility
    comparison = compare_with_baseline(minimum, equal)
    assert comparison.weight_distance > 0
    assert comparison.volatility_difference < 0


def test_optimizer_returns_typed_failure_not_empty_allocation() -> None:
    stale = make_snapshot(statuses=())
    exposure = make_exposure(stale)
    policy = make_policy()
    budget = make_budget(stale, exposure, policy)
    result = generate_allocation(
        snapshot=stale,
        exposure=exposure,
        budget=budget,
        config=OptimizationConfig(method=OptimizationMethod.EQUAL_WEIGHT),
        covariance=make_covariance(),
        instrument_ids=("stock_a", "stock_b"),
        section_by_instrument=_section_map(),
        current_weights=(0.1, 0.1),
        policy=policy,
        now=NOW,
    )
    assert result.status in {SolverStatus.STALE_DATA, SolverStatus.MISSING_EXPOSURE}
    assert result.weights is None
    assert not result.successful


def test_equal_weight_rejects_non_comparable_sections() -> None:
    snapshot = make_snapshot(
        positions=(
            stock_position("stock_a"),
            stock_position("stock_b"),
        )
    )
    exposure = make_exposure(snapshot)
    policy = make_policy()
    budget = make_budget(snapshot, exposure, policy)
    result = generate_allocation(
        snapshot=snapshot,
        exposure=exposure,
        budget=budget,
        config=OptimizationConfig(
            method=OptimizationMethod.EQUAL_WEIGHT,
            require_comparable_assets=True,
        ),
        covariance=make_covariance(),
        instrument_ids=("stock_a", "stock_b"),
        section_by_instrument={"stock_a": Section.STOCKS, "stock_b": Section.OPTIONS},
        current_weights=(0.1, 0.1),
        policy=policy,
        now=NOW,
    )
    assert result.status == SolverStatus.UNSUPPORTED_INSTRUMENT
    assert result.weights is None


def test_stability_analysis_reports_perturbed_cases() -> None:
    result = stability_analysis(
        base_weights=(0.5, 0.5),
        covariance=make_covariance(),
        expected_returns=(0.10, 0.12),
        config=OptimizationConfig(method=OptimizationMethod.MINIMUM_VARIANCE),
        perturbations=(0.05, 0.10),
    )
    assert result.perturbed_cases >= 1
    assert result.max_weight_distance >= result.mean_weight_distance
    assert not result.warnings


def test_hard_constraint_failures_are_independent_of_solver() -> None:
    policy = make_policy(
        max_single_instrument_weight=0.4,
        global_max_turnover=0.5,
        global_max_concentration_hhi=0.25,
        section_caps=(("stocks", 50_000.0),),
        global_max_stress_loss=1_000.0,
    )
    results = evaluate_allocation_constraints(
        instrument_ids=("stock_a", "stock_b"),
        weights=(0.5, 0.5),
        current_weights=(0.1, 0.1),
        covariance=make_covariance(),
        policy=policy,
        equity=100_000.0,
        section_by_instrument=_section_map(),
        stress_passed=False,
    )
    names = {result.name for result in results if not result.passed}
    assert "max_single_instrument_weight" in names
    assert "max_turnover" in names
    assert "max_concentration_hhi" in names
    assert "section_cap:stocks" in names
    assert "stress_suite_passed" in names
    assert not hard_constraints_pass(results)
    assert len(failed_hard_constraints(results)) >= 5


def test_covariance_validity_is_a_hard_constraint() -> None:
    results = evaluate_allocation_constraints(
        instrument_ids=("stock_a", "stock_b"),
        weights=(0.5, 0.5),
        current_weights=(0.5, 0.5),
        covariance=((1.0, 0.2), (0.1, 1.0)),
        policy=make_policy(),
        equity=100_000.0,
        section_by_instrument=_section_map(),
        stress_passed=True,
    )
    assert any(
        result.name == "covariance_validity" and not result.passed
        for result in results
    )
