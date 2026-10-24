"""Stress-scenario construction and application tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from support import (
    NOW,
    make_exposure,
    make_option_position,
    make_policy,
    make_prediction_position,
    make_prediction_registry,
    make_registry,
    make_snapshot,
)

from axel.core.types import Section
from axel.portfolio.normalize import build_exposure_snapshot
from axel.portfolio.state import DEFAULT_FRESHNESS
from axel.portfolio.stress import (
    DEFAULT_STRESS_SCENARIOS,
    ScenarioFamily,
    StressScenario,
    apply_scenario,
    default_scenarios,
    run_stress_suite,
)


def test_default_scenarios_are_versioned_and_hypothetical() -> None:
    scenarios = default_scenarios()
    assert len(scenarios) >= 12
    assert all(isinstance(scenario, StressScenario) for scenario in scenarios)
    assert all(scenario.hypothetical for scenario in scenarios)
    assert all(scenario.probability is None for scenario in scenarios)
    assert {scenario.family for scenario in scenarios} >= {
        ScenarioFamily.EQUITY_SELLOFF,
        ScenarioFamily.FX,
        ScenarioFamily.CRYPTO,
        ScenarioFamily.VOLATILITY,
        ScenarioFamily.LIQUIDITY,
        ScenarioFamily.PREDICTION,
        ScenarioFamily.COMBINED,
    }
    assert DEFAULT_STRESS_SCENARIOS == scenarios


def test_stress_suite_passes_for_clean_linear_portfolio() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    policy = make_policy()
    report = run_stress_suite(snapshot, exposure, policy=policy, now=NOW)
    assert report.passed
    assert len(report.results) == len(DEFAULT_STRESS_SCENARIOS)
    assert report.worst_case_loss > 0
    assert report.worst_scenario_id is not None
    assert report.result_for(report.worst_scenario_id) is not None
    assert report.limitations
    assert all(result.hypothetical for result in report.results)


def test_stress_suite_empty_is_not_a_pass() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    report = run_stress_suite(snapshot, exposure, scenarios=(), now=NOW)
    assert not report.passed
    assert report.worst_case_loss == 0.0
    assert report.worst_scenario_id is None
    assert "empty" in report.warnings[0]


def test_stress_suite_rejects_mismatched_exposure() -> None:
    snapshot = make_snapshot()
    exposure = replace(make_exposure(snapshot), portfolio_snapshot_id="other_snapshot")
    with pytest.raises(ValueError, match="does not match"):
        run_stress_suite(snapshot, exposure, now=NOW)


def test_prediction_unknown_max_loss_fails_closed() -> None:
    snapshot = make_snapshot(positions=(make_prediction_position(),))
    exposure = build_exposure_snapshot(
        snapshot,
        make_prediction_registry(),
        now=NOW,
        freshness=DEFAULT_FRESHNESS,
    )
    prediction = replace(exposure.instrument_exposures[0], max_loss=None)
    exposure = replace(exposure, instrument_exposures=(prediction,))
    scenario = StressScenario(
        scenario_id="event_loss",
        version="1.0",
        name="Event loss",
        family=ScenarioFamily.PREDICTION,
        description="Prediction settles at maximum loss.",
        prediction_loss_fraction=1.0,
        requires_full_revaluation=False,
    )
    result = apply_scenario(exposure, scenario, snapshot=snapshot, policy=make_policy(), now=NOW)
    assert not result.passed
    assert any(check.name == "prediction_max_loss_defined" for check in result.breaches)


def test_option_scenario_uses_full_revaluation() -> None:
    snapshot = make_snapshot(positions=(make_option_position(),))
    registry = make_registry(include_option=True)
    exposure = build_exposure_snapshot(
        snapshot,
        registry,
        now=NOW,
        freshness=DEFAULT_FRESHNESS,
    )
    policy = make_policy(global_max_stress_loss=50_000.0)
    report = run_stress_suite(snapshot, exposure, policy=policy, now=NOW)
    assert report.passed
    selloff = report.result_for("eq_selloff_20")
    assert selloff is not None
    assert selloff.full_revaluation_count == 1
    assert selloff.total_loss > 0
    assert any("Black-Scholes" in assumption for assumption in selloff.assumptions)


def test_option_scenario_without_contract_inputs_uses_greeks_and_can_fail() -> None:
    snapshot = make_snapshot(positions=(make_option_position(),))
    registry = make_registry(include_option=True)
    exposure = build_exposure_snapshot(
        snapshot,
        registry,
        now=NOW,
        freshness=DEFAULT_FRESHNESS,
    )
    no_option = replace(
        snapshot,
        positions=(replace(snapshot.positions[0], option=None),),
    )
    scenario = StressScenario(
        scenario_id="vol_spike",
        version="1.0",
        name="Vol spike",
        family=ScenarioFamily.VOLATILITY,
        description="Volatility rises.",
        section_returns={Section.OPTIONS.value: -0.05},
        volatility_shock=0.10,
        requires_full_revaluation=True,
    )
    result = apply_scenario(exposure, scenario, snapshot=no_option, policy=make_policy(), now=NOW)
    assert not result.passed
    assert any(
        check.name == "option_full_revaluation_available" for check in result.breaches
    )
    assert result.greeks_count == 1


def test_scenario_validation_rejects_impossible_returns_and_probabilities() -> None:
    with pytest.raises(ValueError, match="cannot be <= -100%"):
        StressScenario(
            scenario_id="bad",
            version="1.0",
            name="Bad",
            family=ScenarioFamily.EQUITY_SELLOFF,
            description="bad",
            section_returns={Section.STOCKS.value: -1.0},
        )
    with pytest.raises(ValueError, match="probability"):
        StressScenario(
            scenario_id="bad_prob",
            version="1.0",
            name="Bad probability",
            family=ScenarioFamily.EQUITY_SELLOFF,
            description="bad",
            probability=1.5,
        )


def test_hypothetical_label_is_forced_without_probability() -> None:
    scenario = StressScenario(
        scenario_id="hyp",
        version="1.0",
        name="Hypothetical",
        family=ScenarioFamily.HYPOTHETICAL,
        description="no probability",
        hypothetical=False,
    )
    assert scenario.hypothetical


def test_liquidity_and_funding_costs_are_added() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    scenario = StressScenario(
        scenario_id="costs",
        version="1.0",
        name="Costs",
        family=ScenarioFamily.LIQUIDITY,
        description="Costs only.",
        liquidity_cost_bps=100.0,
        funding_rate_shock=0.10,
        funding_horizon_days=10.0,
        requires_full_revaluation=False,
    )
    result = apply_scenario(exposure, scenario, snapshot=snapshot, now=NOW)
    assert result.liquidity_cost > 0
    assert result.total_loss >= result.liquidity_cost
    assert result.section_loss[Section.STOCKS.value] > 0
