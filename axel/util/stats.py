"""
Pure-stdlib descriptive statistics shared by every numeric layer.

This module exists so that metrics, features and risk math depend on one
implementation. Before it existed, ``axel.data.ml.metrics`` imported ``mean`` /
``sample_std`` from ``axel.data.features.base`` which inverted the layering: a
backtest metric must never depend on the feature engine.

Every function here is total (empty input returns a defined value, never
raises) and deterministic, so results are reproducible run to run.
"""

from __future__ import annotations

from collections.abc import Sequence

DAYS_PER_YEAR = 365.25


def mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def variance(values: Sequence[float], *, ddof: int = 1) -> float:
    if len(values) <= ddof:
        return 0.0
    average = mean(values)
    return sum((value - average) ** 2 for value in values) / (len(values) - ddof)


def sample_std(values: Sequence[float]) -> float:
    return variance(values, ddof=1) ** 0.5


def population_std(values: Sequence[float]) -> float:
    return variance(values, ddof=0) ** 0.5


def simple_returns(closes: Sequence[float]) -> list[float]:
    """Period-over-period simple returns (length = len(closes) - 1)."""
    return [
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
        if closes[index - 1] != 0
    ]


def log_returns(closes: Sequence[float]) -> list[float]:
    """Continuously-compounded returns, used for volatility and VaR scaling."""
    from math import log

    result: list[float] = []
    for index in range(1, len(closes)):
        previous, current = closes[index - 1], closes[index]
        if previous > 0 and current > 0:
            result.append(log(current / previous))
    return result


def correlation(left: Sequence[float], right: Sequence[float]) -> float:
    """Pearson correlation of two equal-length series, 0.0 when undefined."""
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True)
    )
    denominator = (sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right)) ** 0.5
    if denominator == 0:
        return 0.0
    return numerator / denominator


def covariance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean, right_mean = mean(left), mean(right)
    return sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True)
    ) / (len(left) - 1)


def quantile(values: Sequence[float], probability: float) -> float:
    """
    Linear-interpolation quantile (the "type 7" definition, same as numpy's
    default). Returns 0.0 for empty input and clamps out-of-range probabilities.
    """
    if not values:
        return 0.0
    if probability <= 0:
        return min(values)
    if probability >= 1:
        return max(values)
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def elapsed_years(start_seconds: float, end_seconds: float) -> float:
    """Elapsed years between two epoch-second stamps (365.25-day convention)."""
    span = end_seconds - start_seconds
    if span <= 0:
        return 0.0
    return span / (DAYS_PER_YEAR * 86_400.0)


def implied_periods_per_year(periods: int, years: float, fallback: float) -> float:
    """
    Observations-per-year implied by *actual elapsed time*.

    This is the fix for annualising on a bar count: a series with gaps (holidays,
    missing sessions, halted names) has fewer observations per year than a dense
    one, so multiplying its per-observation volatility by sqrt(252) overstates
    annualised risk. Counting sessions instead of observations does not.
    """
    if periods <= 0 or years <= 0:
        return fallback
    return periods / years


__all__ = [
    "DAYS_PER_YEAR",
    "correlation",
    "covariance",
    "elapsed_years",
    "implied_periods_per_year",
    "log_returns",
    "mean",
    "median",
    "population_std",
    "quantile",
    "sample_std",
    "simple_returns",
    "variance",
]