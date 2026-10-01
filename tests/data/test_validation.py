from datetime import UTC, datetime, timedelta

import pytest

from axel.validation import (
    AdmissionCriteria,
    Fold,
    benjamini_hochberg,
    bonferroni_threshold,
    deflated_sharpe_ratio,
    evaluate_admission,
    expected_max_of_normals,
    expected_max_sharpe,
    holdout_fold,
    needs_leakage_audit,
    norm_cdf,
    norm_ppf,
    parameter_variants,
    perturbed_values,
    probabilistic_sharpe_ratio,
    robustness_report,
    sign_stable,
    walk_forward_folds,
)


def test_walk_forward_folds_cover_expected_windows() -> None:
    folds = walk_forward_folds(100, train_size=40, test_size=10)
    assert len(folds) == 6
    assert folds[0] == Fold(train=(0, 40), test=(40, 50))
    assert folds[1].train == (10, 50)
    assert all(fold.test[1] <= 100 for fold in folds)


def test_holdout_fold_splits_chronologically() -> None:
    fold = holdout_fold(100, test_fraction=0.25)
    assert fold.train == (0, 75)
    assert fold.test == (75, 100)


def test_perturbation_and_sign_stability() -> None:
    assert perturbed_values(0.5, pct=0.2) == (0.4, 0.6)
    variants = parameter_variants({"lookback": 20, "threshold": 1.5}, pct=0.2)
    assert len(variants) == 4
    assert sign_stable([0.3, 0.25, 0.35])
    assert not sign_stable([0.3, -0.01, 0.35])
    report = robustness_report(0.3, [-0.01, 0.4])
    assert report.stable is False
    assert report.worst == -0.01


def test_multiple_testing_helpers() -> None:
    assert expected_max_of_normals(1) == 0.0
    assert expected_max_of_normals(100) > expected_max_of_normals(10)
    assert bonferroni_threshold(0.05, 10) == pytest.approx(0.005)
    rejected = benjamini_hochberg([0.001, 0.008, 0.5, 0.9], alpha=0.05)
    assert rejected == [0, 1]


def test_normal_distribution_helpers_round_trip() -> None:
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(norm_ppf(0.975)) == pytest.approx(0.975, abs=1e-4)


def test_deflated_sharpe_is_below_probabilistic_for_selection() -> None:
    raw = probabilistic_sharpe_ratio(1.5, 0.0, n_returns=252)
    deflated = deflated_sharpe_ratio(
        1.5, n_trials=50, variance_of_sr=0.25, n_returns=252
    )
    assert 0.0 < deflated < raw
    assert expected_max_sharpe(1, 0.25) == 0.0


def test_admission_gates_pass_and_fail() -> None:
    good = {
        "trades": 150.0,
        "expectancy_r": 0.3,
        "sharpe": 1.2,
        "max_drawdown": -0.1,
    }
    decision = evaluate_admission(good, deflated_sharpe=0.97, regimes=3)
    assert decision.admitted
    assert decision.reasons == ()

    weak = {**good, "trades": 40.0, "expectancy_r": -0.05}
    failed = evaluate_admission(weak, deflated_sharpe=0.97, regimes=3)
    assert not failed.admitted
    assert "trade_count" in failed.reasons
    assert "expectancy_floor" in failed.reasons


def test_suspicious_expectancy_requires_audit_not_promotion() -> None:
    assert needs_leakage_audit(1.5)
    assert not needs_leakage_audit(0.4)
    criteria = AdmissionCriteria()
    assert criteria.min_deflated_sharpe == 0.95


def test_fold_datetimes_maps_indices() -> None:
    from axel.validation import fold_datetimes

    timestamps = [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index) for index in range(10)]
    fold = Fold(train=(0, 6), test=(6, 10))
    windows = fold_datetimes([fold], timestamps)
    assert windows[0][0] == timestamps[0]
    assert windows[0][3] == timestamps[9]
