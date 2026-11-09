"""Tests for forecast scoring and the admission gate."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from axel.training.evaluation import (
    Prediction,
    brier_score,
    directional_accuracy,
    evaluate,
    load_predictions_csv,
    mean_absolute_error,
    strategy_returns,
)

START = datetime(2024, 1, 1, tzinfo=UTC)


def _prediction(expected: float, realized: float, probability: float = 0.6) -> Prediction:
    return Prediction(
        symbol="AAPL",
        as_of=START,
        expected_return=expected,
        probability_up=probability,
        realized_return=realized,
    )


def test_scoring_metrics_are_correct() -> None:
    predictions = [
        _prediction(0.01, 0.02, 0.8),
        _prediction(-0.01, -0.02, 0.2),
        _prediction(0.005, -0.01, 0.7),
    ]
    assert brier_score(predictions) == pytest.approx((0.04 + 0.04 + 0.49) / 3)
    assert mean_absolute_error(predictions) == pytest.approx((0.01 + 0.01 + 0.015) / 3)
    assert directional_accuracy(predictions) == pytest.approx(2 / 3)


def test_strategy_returns_respect_sign_and_costs() -> None:
    returns = strategy_returns([_prediction(0.01, 0.02)], fee_bps=1.0, slippage_bps=1.0)
    assert returns == pytest.approx([0.02 - 0.0002])
    short = strategy_returns([_prediction(-0.01, -0.02)], fee_bps=1.0, slippage_bps=1.0)
    assert short == pytest.approx([0.02 - 0.0002])


def test_evaluate_rejects_model_that_loses_to_baseline() -> None:
    predictions = [_prediction(0.01, -0.01) for _ in range(120)]
    report = evaluate(predictions, baseline_sharpe=1.0)
    assert report.approved is False
    assert "DOES_NOT_BEAT_TREND_BASELINE" in report.reasons
    assert report.observations == 120


def test_load_predictions_csv_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "predictions.csv"
    path.write_text(
        "symbol,as_of,expected_return,probability_up,realized_return\n"
        f"AAPL,{START.isoformat()},0.01,0.7,0.02\n"
    )
    predictions = load_predictions_csv(path)
    assert predictions == [_prediction(0.01, 0.02, 0.7)]
