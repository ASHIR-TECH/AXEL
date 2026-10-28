"""Point-in-time, walk-forward, performance, and multiple-testing tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from support import NOW

from axel.portfolio.research import (
    ExperimentRecord,
    ExperimentRegistry,
    LookaheadError,
    PerformanceMetrics,
    ResearchValidationError,
    WalkForwardWindow,
    block_bootstrap_mean_ci,
    deflated_sharpe_ratio,
    performance_metrics,
    point_in_time_available,
    probabilistic_sharpe_ratio,
    require_point_in_time,
    walk_forward_windows,
)


def _window(offset_days: int, train_days: int = 10, test_days: int = 5) -> WalkForwardWindow:
    start = NOW + timedelta(days=offset_days)
    train_end = start + timedelta(days=train_days)
    test_start = train_end
    return WalkForwardWindow(
        train_start=start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_start + timedelta(days=test_days),
    )


def _metrics(sharpe: float) -> PerformanceMetrics:
    return PerformanceMetrics(
        periods=60,
        total_return=0.10,
        annualized_return=0.20,
        annualized_volatility=0.15,
        sharpe=sharpe,
        sortino=1.5,
        calmar=1.2,
        max_drawdown=0.10,
        tracking_error=0.05,
        turnover=0.5,
        total_cost=0.001,
        net_return=0.10,
        warnings=(),
    )


def test_point_in_time_check_rejects_lookahead() -> None:
    assert point_in_time_available(
        available_at=NOW - timedelta(seconds=1), decision_at=NOW
    )
    assert not point_in_time_available(
        available_at=NOW + timedelta(seconds=1), decision_at=NOW
    )
    with pytest.raises(LookaheadError):
        require_point_in_time(
            available_at=NOW + timedelta(seconds=1), decision_at=NOW
        )


def test_walk_forward_windows_are_ordered_and_embargoed() -> None:
    windows = walk_forward_windows(
        start=NOW,
        end=NOW + timedelta(days=30),
        train_days=10,
        test_days=5,
        step_days=5,
        embargo_days=1,
    )
    assert len(windows) == 3
    for window in windows:
        assert window.train_end <= window.test_start
        assert window.test_start - window.train_end == timedelta(days=1)
        assert window.is_out_of_sample(window.test_start)
        assert not window.is_out_of_sample(window.train_start)
    assert windows[0].test_end <= windows[1].test_start
    with pytest.raises(ResearchValidationError, match="complete window"):
        walk_forward_windows(
            start=NOW,
            end=NOW + timedelta(days=5),
            train_days=10,
            test_days=5,
        )


def test_walk_forward_window_rejects_overlap_and_bad_embargo() -> None:
    with pytest.raises(ResearchValidationError, match="train_start"):
        WalkForwardWindow(
            train_start=NOW,
            train_end=NOW + timedelta(days=10),
            test_start=NOW + timedelta(days=5),
            test_end=NOW + timedelta(days=15),
        )
    with pytest.raises(ResearchValidationError, match="embargo"):
        WalkForwardWindow(
            train_start=NOW,
            train_end=NOW + timedelta(days=10),
            test_start=NOW + timedelta(days=12),
            test_end=NOW + timedelta(days=15),
        )


def test_performance_metrics_use_net_returns_and_explicit_conventions() -> None:
    returns = (0.01, -0.005, 0.02, 0.01, -0.01, 0.015, 0.0, 0.005) * 8
    benchmark = (0.005, -0.002, 0.01, 0.006, -0.004, 0.008, 0.0, 0.003) * 8
    costs = (0.001,) * len(returns)
    metrics = performance_metrics(
        returns,
        benchmark_returns=benchmark,
        risk_free_returns=(0.0001,) * len(returns),
        costs=costs,
        turnover=(0.1,) * len(returns),
        periods_per_year=252.0,
    )
    assert metrics.periods == len(returns)
    assert metrics.total_cost == pytest.approx(0.001 * len(returns))
    assert metrics.net_return < metrics.total_return + 1e-12
    assert metrics.sharpe is not None
    assert metrics.sortino is not None
    assert metrics.max_drawdown >= 0
    assert metrics.tracking_error is not None
    assert any("Sortino" in warning for warning in metrics.warnings)
    with pytest.raises(ResearchValidationError):
        performance_metrics(())
    with pytest.raises(ResearchValidationError):
        performance_metrics((0.01,), costs=(0.001, 0.002))
    with pytest.raises(ResearchValidationError):
        performance_metrics((-1.0,))


def test_block_bootstrap_is_deterministic_and_reports_interval() -> None:
    values = tuple(0.001 * ((index % 9) - 4) for index in range(90))
    first = block_bootstrap_mean_ci(values, samples=300, block_size=5, seed=7)
    second = block_bootstrap_mean_ci(values, samples=300, block_size=5, seed=7)
    assert first == second
    assert first.lower <= first.estimate <= first.upper
    assert first.samples == 300
    assert first.warnings
    with pytest.raises(ResearchValidationError):
        block_bootstrap_mean_ci(())


def test_probabilistic_and_deflated_sharpe_are_bounded() -> None:
    psr = probabilistic_sharpe_ratio(0.10, observations=120, skewness=-0.2, kurtosis=4.0)
    dsr = deflated_sharpe_ratio(
        0.10,
        observations=120,
        trials=20,
        skewness=-0.2,
        kurtosis=4.0,
    )
    assert 0.0 <= dsr <= psr <= 1.0
    with pytest.raises(ResearchValidationError):
        probabilistic_sharpe_ratio(0.1, observations=1)
    with pytest.raises(ResearchValidationError):
        deflated_sharpe_ratio(0.1, observations=120, trials=0)


def test_experiment_registry_selects_only_after_baseline_and_respects_holdout() -> None:
    registry = ExperimentRegistry()
    baseline = ExperimentRecord(
        experiment_id="baseline_1",
        method="equal_weight",
        parameters={},
        train_window=_window(0),
        test_window=_window(20),
        metrics=_metrics(0.4),
    )
    candidate = ExperimentRecord(
        experiment_id="candidate_1",
        method="minimum_variance",
        parameters={"shrinkage": 0.2},
        train_window=_window(0),
        test_window=_window(20),
        metrics=_metrics(0.7),
    )
    registry.register(baseline)
    registry.register(candidate)
    selection = registry.select_candidate(baseline_method="equal_weight")
    assert selection.selected == candidate
    assert selection.baseline == baseline
    assert selection.multiple_testing_count == 2
    assert selection.deflated_probability is not None
    assert selection.warnings

    used = replace(candidate, experiment_id="candidate_2", holdout_used=True)
    holdout_registry = ExperimentRegistry()
    holdout_registry.register(baseline)
    holdout_registry.register(used)
    selection = holdout_registry.select_candidate(baseline_method="equal_weight")
    assert selection.selected is None
    assert not selection.holdout_untouched
    assert any("holdout" in warning for warning in selection.warnings)

    with pytest.raises(ResearchValidationError):
        registry.register(replace(baseline, metrics=_metrics(0.1)))
    with pytest.raises(ResearchValidationError):
        ExperimentRegistry().select_candidate(baseline_method="equal_weight")


def test_experiment_record_rejects_test_window_that_starts_in_training() -> None:
    train = _window(0)
    bad_test = WalkForwardWindow(
        train_start=NOW,
        train_end=NOW + timedelta(days=5),
        test_start=NOW + timedelta(days=5),
        test_end=NOW + timedelta(days=25),
    )
    with pytest.raises(ResearchValidationError, match="overlaps"):
        ExperimentRecord(
            experiment_id="bad",
            method="bad",
            parameters={},
            train_window=train,
            test_window=bad_test,
            metrics=_metrics(0.1),
        )
