"""Unit tests for statistical reliability, DSR, multiple testing, and calibration metrics."""

from __future__ import annotations

import pytest

from axel.research.statistics import (
    brier_score,
    calibration_metrics,
    deflated_sharpe_ratio,
    log_loss,
    multiple_testing_correction,
)


def test_deflated_sharpe_ratio_bounds_and_deflation():
    # Deflated Sharpe Ratio must be a probability in [0, 1]
    dsr_single = deflated_sharpe_ratio(observed_sharpe=2.0, n=250, num_trials=1)
    assert 0.0 <= dsr_single <= 1.0

    # Deflating across 100 trials must lower the statistical confidence vs 1 trial
    dsr_many = deflated_sharpe_ratio(observed_sharpe=2.0, n=250, num_trials=100)
    assert 0.0 <= dsr_many <= 1.0
    assert dsr_many < dsr_single


def test_multiple_testing_correction_fdr_and_bonferroni():
    p_vals = [0.001, 0.02, 0.04, 0.05, 0.10]
    bonf = multiple_testing_correction(p_vals, method="bonferroni")
    assert bonf[0] == pytest.approx(0.005)
    assert bonf[3] == pytest.approx(0.25)

    bh = multiple_testing_correction(p_vals, method="fdr_bh")
    assert bh[0] <= bh[1] <= bh[2]
    # BH p-values should be <= Bonferroni
    for b_p, bh_p in zip(bonf, bh, strict=True):
        assert bh_p <= b_p + 1e-9


def test_brier_score_and_log_loss():
    forecasts = [0.9, 0.8, 0.2, 0.1]
    outcomes = [1, 1, 0, 0]

    bs = brier_score(forecasts, outcomes)
    assert bs < 0.05  # highly accurate forecasts should have low Brier score

    ll = log_loss(forecasts, outcomes)
    assert ll < 0.3  # highly accurate forecasts have low log loss


def test_calibration_metrics_and_reliability_table():
    forecasts = [0.1, 0.15, 0.45, 0.55, 0.85, 0.95]
    outcomes = [0, 0, 0, 1, 1, 1]

    cal = calibration_metrics(forecasts, outcomes, n_bins=5)
    assert "brier_score" in cal
    assert "log_loss" in cal
    assert "expected_calibration_error" in cal
    assert "max_calibration_error" in cal
    assert len(cal["reliability_table"]) == 5
