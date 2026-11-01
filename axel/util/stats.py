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


def skewness(values: Sequence[float]) -> float:
    """Adjusted Fisher-Pearson sample skewness (0.0 when undefined).

    Symmetric returns give 0; a long right tail gives a positive value. The
    small-sample correction is the one used by pandas/Excel, so a metric
    reported here can be compared with a vendor tearsheet without rescaling.
    """
    count = len(values)
    if count < 3:
        return 0.0
    average = mean(values)
    deviation = sample_std(values)
    if deviation == 0:
        return 0.0
    standardized = sum(((value - average) / deviation) ** 3 for value in values)
    return count / ((count - 1) * (count - 2)) * standardized


def excess_kurtosis(values: Sequence[float]) -> float:
    """Unbiased Fisher excess kurtosis (normal distribution -> 0.0).

    Fat-tailed return series report a positive value. Returning excess rather
    than raw kurtosis keeps the "normal == 0" convention explicit at the call
    site instead of relying on an implicit +3 offset.
    """
    count = len(values)
    if count < 4:
        return 0.0
    average = mean(values)
    deviation = sample_std(values)
    if deviation == 0:
        return 0.0
    standardized = sum(((value - average) / deviation) ** 4 for value in values)
    first = count * (count + 1) / ((count - 1) * (count - 2) * (count - 3)) * standardized
    second = 3.0 * (count - 1) ** 2 / ((count - 2) * (count - 3))
    return first - second


def downside_deviation(values: Sequence[float], *, target: float = 0.0) -> float:
    """Root-mean-square shortfall below ``target`` (per-period, not annualised).

    Unlike standard deviation, upside surprises do not increase this measure,
    which is why it is the denominator of the Sortino ratio.
    """
    if not values:
        return 0.0
    squared = [min(value - target, 0.0) ** 2 for value in values]
    return (sum(squared) / len(squared)) ** 0.5


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
    "downside_deviation",
    "elapsed_years",
    "excess_kurtosis",
    "implied_periods_per_year",
    "log_returns",
    "mean",
    "median",
    "population_std",
    "quantile",
    "sample_std",
    "simple_returns",
    "skewness",
    "variance",
]