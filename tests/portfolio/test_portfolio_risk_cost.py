"""Risk-measure and transaction-cost tests."""

from __future__ import annotations

import pytest
from support import NOW

from axel.core.types import OrderSide
from axel.portfolio.cost import (
    DEFAULT_COST_MODEL,
    CostInput,
    CostModelError,
    component_shares,
    cost_as_return,
    cost_is_material,
    estimate_rebalance_cost,
    estimate_trade_cost,
    turnover,
)
from axel.portfolio.risk import (
    aggregate_risk_report,
    component_risk_contributions,
    drawdown_series,
    expected_shortfall,
    hhi,
    historical_var,
    liquidity_horizon_days,
    marginal_risk_contributions,
    max_drawdown,
    normal_var,
    risk_contribution_percentages,
    scenario_cvar,
    verify_risk_contribution_invariant,
)
from axel.portfolio.types import AssetClass


def test_component_contributions_reconcile_to_volatility() -> None:
    covariance = [[0.04, 0.01], [0.01, 0.09]]
    weights = (0.6, 0.4)
    components = component_risk_contributions(weights, covariance)
    from axel.portfolio.returns import portfolio_volatility

    volatility = portfolio_volatility(weights, covariance)
    assert sum(components) == pytest.approx(volatility, abs=1e-12)
    assert verify_risk_contribution_invariant(weights, covariance)
    percentages = risk_contribution_percentages(weights, covariance)
    assert sum(percentages) == pytest.approx(1.0, abs=1e-10)
    marginal = marginal_risk_contributions(weights, covariance)
    assert len(marginal) == 2


def test_component_contributions_can_be_negative_for_hedges() -> None:
    covariance = [[0.04, -0.03], [-0.03, 0.09]]
    components = component_risk_contributions((0.8, 0.2), covariance)
    assert any(value < 0 for value in components)


def test_historical_var_and_expected_shortfall_ordering() -> None:
    losses = tuple(0.01 * index for index in range(1, 21))
    var_95 = historical_var(losses, 0.95)
    es_95 = expected_shortfall(losses, 0.95)
    assert var_95 > 0
    assert es_95 >= var_95
    assert historical_var((), 0.95) == 0.0
    assert expected_shortfall((), 0.95) == 0.0
    with pytest.raises(ValueError):
        expected_shortfall(losses, 1.5)


def test_scenario_cvar_uses_probabilities_and_respects_worst_case() -> None:
    scenarios = (-0.10, -0.04, 0.02, 0.05)
    probabilities = (0.1, 0.4, 0.4, 0.1)
    cvar = scenario_cvar(scenarios, probabilities, 0.9)
    assert cvar >= 0.04
    with pytest.raises(ValueError):
        scenario_cvar(scenarios, (0.5, 0.5, 0.5, 0.5), 0.9)


def test_drawdown_and_hhi() -> None:
    values = (100.0, 110.0, 105.0, 120.0, 90.0, 95.0)
    assert max_drawdown(values) == pytest.approx(0.25)
    assert len(drawdown_series(values)) == len(values)
    assert hhi((0.5, 0.5)) == pytest.approx(0.5)
    assert hhi(()) == 0.0


def test_liquidity_horizon_and_normal_var() -> None:
    assert liquidity_horizon_days(100.0, 10.0) == pytest.approx(10.0)
    with pytest.raises(ValueError):
        liquidity_horizon_days(100.0, 0.0)
    assert normal_var(0.01, 0.02, 0.95) > 0
    with pytest.raises(ValueError):
        normal_var(-0.01, 0.02, 0.95)


def test_aggregate_risk_report_flags_unknown_max_loss_and_margin() -> None:
    report = aggregate_risk_report(
        instrument_ids=("a", "b"),
        weights=(0.5, 0.5),
        covariance=[[0.04, 0.01], [0.01, 0.09]],
        portfolio_returns=tuple(0.002 * ((index % 7) - 3) for index in range(40)),
        portfolio_values=tuple(100.0 + index for index in range(40)),
        section_gross_exposure={"stocks": 100.0},
        gross_exposure=100.0,
        net_exposure=100.0,
        max_loss=None,
        margin_requirement=None,
        liquidity_horizon_days=None,
    )
    assert report.volatility > 0
    assert len(report.contributions) == 2
    assert any("maximum loss" in warning for warning in report.warnings)
    assert any("margin" in warning for warning in report.warnings)
    assert report.to_summary().volatility == pytest.approx(report.volatility)


def _cost_input(
    instrument_id: str = "stock_a",
    *,
    quantity: float = 10.0,
    price: float = 100.0,
    side: OrderSide = OrderSide.BUY,
    spread_bps: float | None = 10.0,
    fee_bps: float | None = 2.0,
) -> CostInput:
    return CostInput(
        instrument_id=instrument_id,
        venue="test",
        asset_class=AssetClass.EQUITY,
        side=side,
        quantity=quantity,
        price=price,
        multiplier=1.0,
        currency="USD",
        base_currency="USD",
        as_of=NOW,
        adv=10_000.0,
        volatility=0.20,
        spread_bps=spread_bps,
        fee_bps=fee_bps,
        holding_days=5.0,
    )


def test_trade_cost_components_and_turnover() -> None:
    estimate = estimate_trade_cost(_cost_input(), now=NOW)
    assert estimate.expected_notional == pytest.approx(1_000.0)
    assert estimate.spread_cost == pytest.approx(0.5)
    assert estimate.fee_cost == pytest.approx(0.2)
    assert estimate.total_cost > estimate.fee_cost
    assert estimate.total_bps > 0
    assert turnover((0.5, 0.5), (0.6, 0.4)) == pytest.approx(0.2)
    with pytest.raises(CostModelError):
        turnover((0.5,), (0.5, 0.5))


def test_rebalance_cost_summary_and_component_shares() -> None:
    summary = estimate_rebalance_cost(
        (_cost_input("a"), _cost_input("b", side=OrderSide.SELL)),
        now=NOW,
    )
    assert set(summary.by_instrument) == {"a", "b"}
    assert summary.total_cost == pytest.approx(sum(summary.by_instrument.values()))
    shares = component_shares(summary)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert cost_as_return(summary.total_cost, 100_000.0) > 0
    assert cost_is_material(summary, 100_000.0, threshold_bps=0.0)


def test_missing_cost_inputs_degrade_coverage_but_do_not_fabricate_zero() -> None:
    trade = _cost_input(spread_bps=None, fee_bps=None)
    estimate = estimate_trade_cost(trade, DEFAULT_COST_MODEL, now=NOW)
    assert estimate.coverage.value == "degraded"
    assert any("spread_bps missing" in warning for warning in estimate.warnings)
    assert any("fee_bps missing" in warning for warning in estimate.warnings)
    assert estimate.total_cost > 0
