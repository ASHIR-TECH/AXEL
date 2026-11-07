"""
Bets-specific forecast calibration diagnostics — pure and deterministic.

Complements ``axel.research.statistics.calibration_metrics`` with the pieces a
betting desk needs before trusting a probability model:

* a reliability curve with Wilson confidence intervals per bin,
* Expected / Maximum Calibration Error derived from that curve,
* the Brier skill score against the base-rate (climatology) forecast.

Inspired by the calibration and review tooling in AI-Trader
(https://github.com/TauricResearch/AI-Trader, research/). No I/O, no orders.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReliabilityBin:
    """One probability bucket of a reliability curve."""

    low: float
    high: float
    count: int
    mean_forecast: float | None
    observed_rate: float | None
    wilson_low: float | None
    wilson_high: float | None


def _validate(forecasts: Sequence[float], outcomes: Sequence[int | float]) -> None:
    if not forecasts or len(forecasts) != len(outcomes):
        raise ValueError("forecasts and outcomes must be non-empty and of equal length")
    for forecast in forecasts:
        if not 0.0 <= float(forecast) <= 1.0:
            raise ValueError(f"forecast must be in [0,1], got {forecast}")
    for outcome in outcomes:
        if float(outcome) not in (0.0, 1.0):
            raise ValueError(f"outcome must be 0 or 1, got {outcome}")


def wilson_interval(successes: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (deterministic z)."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be in [0, {n}], got {successes}")
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    margin = (z / denominator) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def reliability_curve(
    forecasts: Sequence[float],
    outcomes: Sequence[int | float],
    *,
    n_bins: int = 10,
) -> tuple[ReliabilityBin, ...]:
    """Bucket forecasts into half-open probability bins and measure realised frequency."""
    _validate(forecasts, outcomes)
    if n_bins <= 0:
        raise ValueError(f"n_bins must be positive, got {n_bins}")

    width = 1.0 / n_bins
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for forecast, outcome in zip(forecasts, outcomes, strict=True):
        value = float(forecast)
        index = min(int(value / width), n_bins - 1)
        buckets[index].append((value, float(outcome)))

    curve: list[ReliabilityBin] = []
    for index, items in enumerate(buckets):
        low, high = index * width, (index + 1) * width
        count = len(items)
        if count == 0:
            curve.append(ReliabilityBin(low, high, 0, None, None, None, None))
            continue
        mean_forecast = sum(item[0] for item in items) / count
        observed_rate = sum(item[1] for item in items) / count
        successes = round(sum(item[1] for item in items))
        wilson_low, wilson_high = wilson_interval(successes, count)
        curve.append(
            ReliabilityBin(
                low=low,
                high=high,
                count=count,
                mean_forecast=round(mean_forecast, 6),
                observed_rate=round(observed_rate, 6),
                wilson_low=round(wilson_low, 6),
                wilson_high=round(wilson_high, 6),
            )
        )
    return tuple(curve)


def expected_calibration_error(curve: Sequence[ReliabilityBin]) -> float:
    """Count-weighted mean gap between forecast and observed frequency."""
    total = sum(item.count for item in curve)
    if total == 0:
        return 0.0
    error = 0.0
    for item in curve:
        if item.count == 0 or item.mean_forecast is None or item.observed_rate is None:
            continue
        error += item.count / total * abs(item.mean_forecast - item.observed_rate)
    return error


def maximum_calibration_error(curve: Sequence[ReliabilityBin]) -> float:
    """Largest per-bin gap between forecast and observed frequency."""
    gaps = [
        abs(item.mean_forecast - item.observed_rate)
        for item in curve
        if item.count > 0 and item.mean_forecast is not None and item.observed_rate is not None
    ]
    return max(gaps, default=0.0)


def brier_score(forecasts: Sequence[float], outcomes: Sequence[int | float]) -> float:
    """Mean squared error of probabilistic forecasts against binary outcomes."""
    _validate(forecasts, outcomes)
    return sum(
        (float(forecast) - float(outcome)) ** 2
        for forecast, outcome in zip(forecasts, outcomes, strict=True)
    ) / len(forecasts)


def brier_skill_score(forecasts: Sequence[float], outcomes: Sequence[int | float]) -> float:
    """Brier skill score versus always predicting the base rate (1.0 is perfect)."""
    _validate(forecasts, outcomes)
    base_rate = sum(float(outcome) for outcome in outcomes) / len(outcomes)
    reference = sum((base_rate - float(outcome)) ** 2 for outcome in outcomes) / len(outcomes)
    if reference == 0.0:
        # A degenerate sample has no skill to measure; report neutral skill.
        return 0.0
    return 1.0 - brier_score(forecasts, outcomes) / reference


def calibration_report(
    forecasts: Sequence[float],
    outcomes: Sequence[int | float],
    *,
    n_bins: int = 10,
) -> dict[str, Any]:
    """JSON-friendly calibration summary for dashboards and review notes."""
    curve = reliability_curve(forecasts, outcomes, n_bins=n_bins)
    return {
        "n": len(forecasts),
        "brier_score": round(brier_score(forecasts, outcomes), 6),
        "brier_skill_score": round(brier_skill_score(forecasts, outcomes), 6),
        "expected_calibration_error": round(expected_calibration_error(curve), 6),
        "max_calibration_error": round(maximum_calibration_error(curve), 6),
        "reliability_curve": [asdict(item) for item in curve],
    }
