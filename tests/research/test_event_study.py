"""Tests for the pure-stdlib market-model event study."""

import math

import pytest

from axel.research.event_study import (
    abnormal_returns,
    bootstrap_mean_ci,
    cross_sectional_summary,
    cumulative_abnormal_return,
    fit_market_model,
    one_sample_t_test,
    run_event_study,
    student_t_two_sided_p_value,
)


def test_market_model_recovers_exact_linear_relationship() -> None:
    market = [-0.02, -0.01, 0.0, 0.01, 0.02, 0.03]
    stock = [0.001 + 1.5 * value for value in market]

    fit = fit_market_model(stock, market)

    assert fit.alpha == pytest.approx(0.001)
    assert fit.beta == pytest.approx(1.5)
    assert fit.r_squared == pytest.approx(1.0)
    assert fit.residual_std == pytest.approx(0.0)
    assert fit.n_obs == len(market)


def test_abnormal_returns_are_residuals() -> None:
    market = [-0.02, -0.01, 0.0, 0.01, 0.02, 0.03]
    stock = [0.001 + 1.5 * value for value in market]
    fit = fit_market_model(stock, market)

    residuals = abnormal_returns(stock, market, fit)

    assert residuals == pytest.approx((0.0,) * len(market))


def test_flat_market_cannot_be_fitted() -> None:
    with pytest.raises(ValueError):
        fit_market_model([0.01, 0.02, 0.03], [0.0, 0.0, 0.0])


def test_market_model_validates_inputs() -> None:
    with pytest.raises(ValueError):
        fit_market_model([0.01, 0.02], [0.01, 0.02])
    with pytest.raises(ValueError):
        fit_market_model([0.01, 0.02, 0.03], [0.01, 0.02])
    with pytest.raises(ValueError):
        abnormal_returns([0.01], [0.01, 0.02], fit_market_model([0, 1, 2], [0, 1, 2]))


def test_cumulative_abnormal_return_windows() -> None:
    abnormal = (0.01, 0.02, 0.03, 0.04)

    assert cumulative_abnormal_return(abnormal) == pytest.approx(0.10)
    assert cumulative_abnormal_return(abnormal, 0, 1) == pytest.approx(0.03)
    assert cumulative_abnormal_return(abnormal, 2, 3) == pytest.approx(0.07)
    with pytest.raises(ValueError):
        cumulative_abnormal_return(())
    with pytest.raises(ValueError):
        cumulative_abnormal_return(abnormal, 2, 1)


def test_student_t_p_value_known_values() -> None:
    assert student_t_two_sided_p_value(0.0, 10) == 1.0
    # t = 2.228 with 10 df is the 5% two-sided critical value.
    assert student_t_two_sided_p_value(2.228, 10) == pytest.approx(0.05, abs=1e-3)
    assert student_t_two_sided_p_value(1.96, 1_000_000) == pytest.approx(0.05, abs=1e-3)
    assert student_t_two_sided_p_value(-2.228, 10) == pytest.approx(
        student_t_two_sided_p_value(2.228, 10)
    )
    with pytest.raises(ValueError):
        student_t_two_sided_p_value(1.0, 0)


def test_one_sample_t_test() -> None:
    assert one_sample_t_test([0.01]) == (0.0, 1.0)
    assert one_sample_t_test([0.02, 0.02, 0.02])[0] == math.inf
    assert one_sample_t_test([0.0, 0.0]) == (0.0, 1.0)

    t_stat, p_value = one_sample_t_test([0.01, 0.02, 0.03, 0.04, 0.05])
    assert t_stat > 0
    assert 0.0 < p_value < 0.05


def test_bootstrap_ci_is_seeded_and_contains_mean() -> None:
    values = [0.01, -0.02, 0.03, 0.04, -0.01, 0.02, 0.05, 0.0]

    first = bootstrap_mean_ci(values, n_bootstrap=500, seed=7)
    second = bootstrap_mean_ci(values, n_bootstrap=500, seed=7)

    assert first == second
    assert first[0] <= sum(values) / len(values) <= first[1]
    assert first[0] < first[1]


def test_bootstrap_validation() -> None:
    with pytest.raises(ValueError):
        bootstrap_mean_ci([])
    with pytest.raises(ValueError):
        bootstrap_mean_ci([0.1], n_bootstrap=0)
    with pytest.raises(ValueError):
        bootstrap_mean_ci([0.1], confidence=1.0)


def test_run_event_study_measures_injected_abnormal_return() -> None:
    n = 200
    market = [0.001 * math.sin(i / 5.0) for i in range(n)]
    stock = [0.0005 + 0.8 * value for value in market]
    event_index = 150
    stock[event_index] += 0.05

    result = run_event_study(
        stock,
        market,
        event_index,
        estimation_start=-120,
        estimation_end=-11,
        event_start=0,
        event_end=5,
        n_bootstrap=300,
        seed=3,
    )

    assert result.fit.beta == pytest.approx(0.8, abs=0.05)
    assert result.n_obs == 6
    assert result.abnormal_returns[0] == pytest.approx(0.05, abs=0.005)
    assert result.cumulative_abnormal_return == pytest.approx(0.05, abs=0.005)
    assert result.t_stat > 0
    assert result.p_value < 0.5


def test_run_event_study_rejects_out_of_range_windows() -> None:
    stock = [0.01] * 10
    market = [0.005] * 10

    with pytest.raises(ValueError):
        run_event_study(stock, market, 2, estimation_start=-100, estimation_end=-11)
    with pytest.raises(ValueError):
        run_event_study(stock, market, 8, event_end=10)


def test_cross_sectional_summary() -> None:
    cars = [0.02, 0.01, -0.005, 0.03, 0.015]

    summary = cross_sectional_summary(cars, n_bootstrap=500, seed=11)

    assert summary.mean_car == pytest.approx(0.014)
    assert summary.median_car == pytest.approx(0.015)
    assert summary.hit_rate == pytest.approx(0.8)
    assert summary.n_events == 5
    assert summary.ci_low <= summary.mean_car <= summary.ci_high

    with pytest.raises(ValueError):
        cross_sectional_summary([])
