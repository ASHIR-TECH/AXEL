"""Evaluation harness for forecast models.

A model is not accepted because it trains. This module scores predictions on
held-out data, compares the result with AXEL's deterministic trend baseline, and
applies the same admission policy the rest of the system uses.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from axel.data.backtester import BacktestResult, summarize_returns, trend_returns
from axel.data.models import Bar
from axel.data.validation import ValidationPolicy, validate_strategy


@dataclass(frozen=True)
class Prediction:
    """One forecast and the return that actually followed it."""

    symbol: str
    as_of: datetime
    expected_return: float
    probability_up: float
    realized_return: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability_up <= 1.0:
            raise ValueError("probability_up must be in [0, 1]")


@dataclass(frozen=True)
class EvaluationReport:
    observations: int
    brier: float | None
    mae: float | None
    directional_accuracy: float | None
    trades: int
    mean_return: float
    sharpe: float
    max_drawdown: float
    baseline_sharpe: float
    beats_baseline: bool
    approved: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def load_predictions_csv(path: Path) -> list[Prediction]:
    """Load ``symbol,as_of,expected_return,probability_up,realized_return``."""
    required = {"symbol", "as_of", "expected_return", "probability_up", "realized_return"}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"predictions CSV must contain {sorted(required)}")
        return [
            Prediction(
                symbol=row["symbol"],
                as_of=datetime.fromisoformat(row["as_of"]),
                expected_return=float(row["expected_return"]),
                probability_up=float(row["probability_up"]),
                realized_return=float(row["realized_return"]),
            )
            for row in reader
        ]


def brier_score(predictions: list[Prediction]) -> float | None:
    if not predictions:
        return None
    return sum(
        (item.probability_up - (1.0 if item.realized_return > 0 else 0.0)) ** 2
        for item in predictions
    ) / len(predictions)


def mean_absolute_error(predictions: list[Prediction]) -> float | None:
    if not predictions:
        return None
    return sum(abs(item.expected_return - item.realized_return) for item in predictions) / len(
        predictions
    )


def directional_accuracy(predictions: list[Prediction]) -> float | None:
    usable = [item for item in predictions if item.expected_return != 0.0]
    if not usable:
        return None
    correct = sum(
        1
        for item in usable
        if math.copysign(1.0, item.expected_return) == math.copysign(1.0, item.realized_return)
    )
    return correct / len(usable)


def strategy_returns(
    predictions: list[Prediction],
    *,
    min_expected_return: float = 0.0,
    fee_bps: float = 1.0,
    slippage_bps: float = 1.0,
) -> list[float]:
    """Convert forecasts into signed, cost-adjusted per-trade returns."""
    cost = (fee_bps + slippage_bps) / 10_000.0
    returns: list[float] = []
    for item in predictions:
        if abs(item.expected_return) < min_expected_return:
            continue
        sign = 1.0 if item.expected_return > 0 else -1.0
        returns.append(sign * item.realized_return - cost)
    return returns


def evaluate(
    predictions: list[Prediction],
    *,
    baseline_sharpe: float = 0.0,
    policy: ValidationPolicy | None = None,
    fee_bps: float = 1.0,
    slippage_bps: float = 1.0,
) -> EvaluationReport:
    """Score predictions and apply the deterministic admission gate."""
    returns = strategy_returns(
        predictions, fee_bps=fee_bps, slippage_bps=slippage_bps
    )
    result: BacktestResult = summarize_returns(returns)
    validation = validate_strategy(result, policy)
    reasons = list(validation.reasons)
    if result.sharpe <= baseline_sharpe:
        reasons.append("DOES_NOT_BEAT_TREND_BASELINE")
    return EvaluationReport(
        observations=len(predictions),
        brier=brier_score(predictions),
        mae=mean_absolute_error(predictions),
        directional_accuracy=directional_accuracy(predictions),
        trades=result.trades,
        mean_return=result.expectancy_r,
        sharpe=result.sharpe,
        max_drawdown=result.max_drawdown,
        baseline_sharpe=baseline_sharpe,
        beats_baseline=result.sharpe > baseline_sharpe,
        approved=not reasons,
        reasons=tuple(reasons),
    )


def trend_baseline_sharpe(
    bars: list[Bar], *, fee_bps: float = 1.0, slippage_bps: float = 1.0
) -> float:
    """Sharpe of AXEL's deterministic trend baseline on the same bars."""
    try:
        return trend_returns(bars, fee_bps=fee_bps, slippage_bps=slippage_bps).sharpe
    except ValueError:
        return 0.0


__all__ = [
    "EvaluationReport",
    "Prediction",
    "brier_score",
    "directional_accuracy",
    "evaluate",
    "load_predictions_csv",
    "mean_absolute_error",
    "strategy_returns",
    "trend_baseline_sharpe",
]
