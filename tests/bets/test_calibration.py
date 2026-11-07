"""Tests for bets-specific calibration diagnostics."""

import json

import pytest

from axel.bets.calibration import (
    brier_score,
    brier_skill_score,
    calibration_report,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_curve,
    wilson_interval,
)


def test_perfectly_calibrated_binary_forecasts() -> None:
    forecasts = [0.0, 0.0, 1.0, 1.0]
    outcomes = [0, 0, 1, 1]

    curve = reliability_curve(forecasts, outcomes, n_bins=10)
    assert len(curve) == 10
    assert expected_calibration_error(curve) == 0.0
    assert maximum_calibration_error(curve) == 0.0
    assert brier_score(forecasts, outcomes) == 0.0
    assert brier_skill_score(forecasts, outcomes) == 1.0


def test_overconfident_forecasts_are_penalised() -> None:
    forecasts = [0.9] * 8 + [0.1] * 2
    outcomes = [0] * 8 + [1] * 2

    curve = reliability_curve(forecasts, outcomes, n_bins=10)
    assert expected_calibration_error(curve) == pytest.approx(0.9)
    assert maximum_calibration_error(curve) == pytest.approx(0.9)
    assert brier_skill_score(forecasts, outcomes) < 0.0


def test_bin_edges_put_one_in_the_last_bin() -> None:
    curve = reliability_curve([0.0, 0.1, 1.0], [0, 0, 1], n_bins=10)

    assert curve[0].count == 1
    assert curve[1].count == 1
    assert curve[9].count == 1
    assert curve[9].mean_forecast == 1.0


def test_base_rate_forecast_has_zero_skill() -> None:
    outcomes = [0, 1, 0, 1]
    forecasts = [0.5] * 4

    assert brier_skill_score(forecasts, outcomes) == pytest.approx(0.0)
    assert brier_score(forecasts, outcomes) == pytest.approx(0.25)


def test_degenerate_sample_reports_neutral_skill() -> None:
    assert brier_skill_score([0.2, 0.3], [0, 0]) == 0.0


def test_wilson_interval_bounds_and_symmetry() -> None:
    low, high = wilson_interval(5, 10)
    assert 0.0 <= low < 0.5 < high <= 1.0
    assert (low + high) == pytest.approx(1.0, abs=1e-9)

    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert 0.0 < high < 0.5

    low, high = wilson_interval(10, 10)
    assert 0.5 < low < 1.0
    assert high == 1.0


def test_wilson_interval_rejects_invalid_counts() -> None:
    with pytest.raises(ValueError):
        wilson_interval(0, 0)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_input_validation() -> None:
    with pytest.raises(ValueError):
        reliability_curve([], [])
    with pytest.raises(ValueError):
        reliability_curve([0.5], [0, 1])
    with pytest.raises(ValueError):
        reliability_curve([1.2], [1])
    with pytest.raises(ValueError):
        reliability_curve([0.5], [2])
    with pytest.raises(ValueError):
        reliability_curve([0.5], [1], n_bins=0)


def test_calibration_report_is_json_friendly() -> None:
    report = calibration_report([0.2, 0.8, 0.6], [0, 1, 1], n_bins=5)

    assert report["n"] == 3
    assert set(report) == {
        "n",
        "brier_score",
        "brier_skill_score",
        "expected_calibration_error",
        "max_calibration_error",
        "reliability_curve",
    }
    assert len(report["reliability_curve"]) == 5
    json.dumps(report)


def test_empty_bins_are_reported_without_values() -> None:
    curve = reliability_curve([0.05], [1], n_bins=10)

    empty = curve[5]
    assert empty.count == 0
    assert empty.mean_forecast is None
    assert empty.observed_rate is None
    assert empty.wilson_low is None
    assert empty.wilson_high is None
