"""Unit tests for validation protocols, parameter perturbation, and baseline comparison."""

from __future__ import annotations

from datetime import datetime, timedelta

from axel.research.baseline import (
    BaselineResult,
    compare_to_baselines,
    default_baselines,
)
from axel.research.validation import (
    holdout_split,
    perturb_parameters,
    purged_embargo_split,
    time_split,
    walk_forward_evaluate,
)


def test_purged_embargo_split_drops_leakage_window():
    base = datetime(2026, 1, 1)
    dates = [base + timedelta(days=i) for i in range(100)]

    raw_train, raw_val, raw_test = time_split(dates, train_ratio=0.6, val_ratio=0.2)
    purged_train, embargoed_val, embargoed_test = purged_embargo_split(
        dates,
        train_ratio=0.6,
        val_ratio=0.2,
        purge_days=5,
        embargo_days=2,
    )

    # Purged train should have dropped dates within 5 days of val start
    assert len(purged_train) < len(raw_train)
    # Embargoed val should have dropped dates within 2 days of train end
    assert len(embargoed_val) < len(raw_val)


def test_walk_forward_evaluate_folds():
    base = datetime(2026, 1, 1)
    dates = [base + timedelta(days=i) for i in range(300)]
    folds = walk_forward_evaluate(dates, window_size=100, step_size=50)

    assert len(folds) > 1
    for f in folds:
        assert f["train_size"] == 100
        assert f["test_size"] <= 50
        # No overlap between train and test in each fold
        assert max(f["train"]) < min(f["test"])


def test_parameter_perturbation():
    params = {"lookback": 10, "threshold": 0.05, "direction": "long"}
    variants = perturb_parameters(params, perturbation_ratio=0.2)

    # Baseline + 2 variants per numeric parameter (lookback and threshold)
    assert len(variants) >= 5
    assert {"lookback": 12, "threshold": 0.05, "direction": "long"} in variants
    assert {"lookback": 8, "threshold": 0.05, "direction": "long"} in variants


def test_baseline_comparison_rules():
    baselines = [
        BaselineResult(
            baseline_id="cash",
            name="Cash",
            metrics={"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0},
        ),
        BaselineResult(
            baseline_id="buyhold",
            name="BuyHold",
            metrics={"return": 0.05, "sharpe": 0.5, "max_drawdown": 0.15},
        ),
    ]

    # Candidate with negative Sharpe and tiny return must NOT beat cash baseline
    bad_cand = {"sharpe": -1.2, "return": 0.001, "max_drawdown": 0.25}
    cmp_bad = compare_to_baselines(bad_cand, baselines)
    assert cmp_bad["comparisons"]["cash"]["beats"] is False
    assert cmp_bad["overall_beats_comparable_baselines"] is False

    # Outperforming candidate
    good_cand = {"sharpe": 1.4, "return": 0.15, "max_drawdown": 0.08}
    cmp_good = compare_to_baselines(good_cand, baselines)
    assert cmp_good["comparisons"]["cash"]["beats"] is True
    assert cmp_good["comparisons"]["buyhold"]["beats"] is True
    assert cmp_good["overall_beats_comparable_baselines"] is True
