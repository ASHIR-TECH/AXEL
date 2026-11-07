"""Risk-adjusted and distribution metrics for equity curves and trades."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from axel.data.ml.metrics import (
    average_loss,
    average_win,
    calmar,
    conditional_value_at_risk,
    downside_deviation,
    omega_ratio,
    payoff_ratio,
    recovery_factor,
    return_excess_kurtosis,
    return_skewness,
    summarize,
    tail_ratio,
    ulcer_index,
    value_at_risk,
)
from axel.util.stats import downside_deviation as raw_downside_deviation
from axel.util.stats import excess_kurtosis, skewness

START = datetime(2026, 1, 1, tzinfo=UTC)


def _curve(returns: list[float], *, start: float = 100.0) -> list[tuple[datetime, float]]:
    curve = [(START, start)]
    value = start
    for index, period_return in enumerate(returns, start=1):
        value *= 1.0 + period_return
        curve.append((START + timedelta(days=index), value))
    return curve


@dataclass(frozen=True)
class _Trade:
    pnl: float
    r_multiple: float


class TestDistributionStatistics:
    def test_symmetric_values_have_zero_skewness(self) -> None:
        assert skewness([-2.0, -1.0, 0.0, 1.0, 2.0]) == pytest.approx(0.0)

    def test_right_tail_is_positively_skewed(self) -> None:
        assert skewness([0.0, 0.0, 0.0, 0.0, 10.0]) > 0

    def test_short_series_is_defined_as_zero(self) -> None:
        assert skewness([1.0, 2.0]) == 0.0
        assert excess_kurtosis([1.0, 2.0, 3.0]) == 0.0

    def test_constant_series_has_no_shape(self) -> None:
        assert skewness([1.0, 1.0, 1.0, 1.0]) == 0.0
        assert excess_kurtosis([1.0, 1.0, 1.0, 1.0]) == 0.0

    def test_fat_tails_have_positive_excess_kurtosis(self) -> None:
        values = [0.0] * 96 + [-8.0, 8.0, -9.0, 9.0]
        assert excess_kurtosis(values) > 0

    def test_downside_deviation_ignores_upside(self) -> None:
        assert raw_downside_deviation([0.01, 0.02, 0.03]) == 0.0
        assert raw_downside_deviation([-0.01, 0.02]) == pytest.approx((0.0001 / 2) ** 0.5)


class TestCurveMetrics:
    def test_downside_deviation_ignores_upside_surprises(self) -> None:
        calm = _curve([0.01, -0.01, 0.01, -0.01])
        jumpy = _curve([0.20, -0.01, 0.20, -0.01])
        assert downside_deviation(jumpy) == pytest.approx(downside_deviation(calm))

    def test_calmar_is_zero_without_drawdown(self) -> None:
        assert calmar(_curve([0.01, 0.01, 0.01])) == 0.0

    def test_calmar_is_positive_after_drawdown_and_recovery(self) -> None:
        curve = _curve([0.01, -0.10, 0.20, 0.10])
        assert calmar(curve) > 0

    def test_omega_is_infinite_with_only_gains(self) -> None:
        assert omega_ratio(_curve([0.01, 0.02])) == float("inf")

    def test_omega_is_zero_with_only_losses(self) -> None:
        assert omega_ratio(_curve([-0.01, -0.02])) == 0.0

    def test_omega_is_positive_for_mixed_returns(self) -> None:
        assert omega_ratio(_curve([0.02, -0.01, 0.03])) > 1.0

    def test_tail_ratio_rewards_a_larger_right_tail(self) -> None:
        assert tail_ratio(_curve([-0.01, -0.005, 0.0, 0.005, 0.02])) > 1.0

    def test_tail_ratio_rejects_bad_quantiles(self) -> None:
        with pytest.raises(ValueError):
            tail_ratio(_curve([0.01]), upper=0.5, lower=0.6)

    def test_ulcer_index_is_zero_for_a_monotonic_curve(self) -> None:
        assert ulcer_index(_curve([0.01, 0.01, 0.01])) == 0.0

    def test_ulcer_index_penalises_underwater_time(self) -> None:
        one_period = _curve([-0.10])
        long_drawdown = _curve([-0.10, -0.10, -0.10])
        assert ulcer_index(one_period) > 0
        assert ulcer_index(long_drawdown) > ulcer_index(one_period)

    def test_recovery_factor_is_zero_without_drawdown(self) -> None:
        assert recovery_factor(_curve([0.01, 0.01])) == 0.0

    def test_recovery_factor_relates_return_to_drawdown(self) -> None:
        assert recovery_factor(_curve([0.20, -0.10])) > 0

    def test_return_shape_helpers_use_equity_returns(self) -> None:
        assert return_skewness(_curve([0.01, 0.01, 0.01])) == 0.0
        assert return_excess_kurtosis(_curve([0.01, 0.01, 0.01])) == 0.0


class TestTailLossMetrics:
    def test_var_and_cvar_are_positive_loss_fractions(self) -> None:
        curve = _curve([-0.05, -0.02, 0.01, 0.03, -0.10])
        var = value_at_risk(curve)
        cvar = conditional_value_at_risk(curve)
        assert var > 0
        assert cvar >= var

    def test_tail_metrics_are_zero_for_a_gaining_curve(self) -> None:
        curve = _curve([0.01, 0.02, 0.03])
        assert value_at_risk(curve) == 0.0
        assert conditional_value_at_risk(curve) == 0.0

    def test_confidence_must_be_in_range(self) -> None:
        with pytest.raises(ValueError):
            value_at_risk(_curve([0.01]), confidence=1.0)
        with pytest.raises(ValueError):
            conditional_value_at_risk(_curve([0.01]), confidence=0.4)


class TestTradeMetrics:
    def test_average_win_and_loss(self) -> None:
        trades = [_Trade(10.0, 1.0), _Trade(-5.0, -0.5), _Trade(20.0, 2.0)]
        assert average_win(trades) == pytest.approx(15.0)
        assert average_loss(trades) == pytest.approx(5.0)

    def test_payoff_ratio(self) -> None:
        trades = [_Trade(10.0, 1.0), _Trade(-5.0, -0.5)]
        assert payoff_ratio(trades) == pytest.approx(2.0)

    def test_payoff_ratio_is_zero_without_losses(self) -> None:
        assert payoff_ratio([_Trade(10.0, 1.0)]) == 0.0

    def test_empty_trades_are_total(self) -> None:
        assert average_win([]) == 0.0
        assert average_loss([]) == 0.0
        assert payoff_ratio([]) == 0.0


def test_summarize_exposes_the_new_metrics() -> None:
    curve = _curve([0.02, -0.01, 0.03, -0.02])
    summary = summarize(curve, [_Trade(10.0, 1.0), _Trade(-4.0, -0.4)])
    for key in (
        "calmar",
        "omega",
        "tail_ratio",
        "downside_deviation",
        "ulcer_index",
        "recovery_factor",
        "var_95",
        "cvar_95",
        "skewness",
        "excess_kurtosis",
        "average_win",
        "average_loss",
        "payoff_ratio",
    ):
        assert key in summary
