"""
Financial turbulence index (Mahalanobis distance of returns).

Adapted from the turbulence calculation in FinRL
(https://github.com/AI4Finance-Foundation/FinRL,
finrl/meta/preprocessor/preprocessors.py). FinRL uses pandas/numpy; this is a
dependency-free, deterministic reimplementation for AXEL's risk layer.

Turbulence measures how far today's cross-sectional return vector sits from the
historical mean, scaled by the historical covariance:

    d_t = (r_t - mu)' * Sigma^-1 * (r_t - mu)

It is a *diagnostic*: a high reading flags a regime where correlations and
volatility have broken down. It does not place or size orders by itself.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Matrix = list[list[float]]
Vector = list[float]

_SINGULAR_TOLERANCE = 1e-12


def _validate_history(history: Sequence[Sequence[float]], *, minimum: int = 2) -> int:
    if len(history) < minimum:
        raise ValueError(f"at least {minimum} historical observations are required")
    dimension = len(history[0])
    if dimension == 0:
        raise ValueError("historical observations must not be empty")
    if any(len(row) != dimension for row in history):
        raise ValueError("all historical observations must have the same dimension")
    return dimension


def mean_vector(history: Sequence[Sequence[float]]) -> Vector:
    """Column-wise mean of a return history."""
    dimension = _validate_history(history)
    count = len(history)
    return [sum(row[column] for row in history) / count for column in range(dimension)]


def covariance_matrix(history: Sequence[Sequence[float]]) -> Matrix:
    """Sample covariance matrix (n - 1 denominator) of a return history."""
    dimension = _validate_history(history)
    count = len(history)
    means = mean_vector(history)
    matrix: Matrix = [[0.0] * dimension for _ in range(dimension)]
    for row in history:
        centred = [row[column] - means[column] for column in range(dimension)]
        for i in range(dimension):
            for j in range(dimension):
                matrix[i][j] += centred[i] * centred[j]
    scale = count - 1
    return [[value / scale for value in row] for row in matrix]


def invert_matrix(matrix: Sequence[Sequence[float]]) -> Matrix:
    """Invert a square matrix with Gauss-Jordan elimination and partial pivoting."""
    size = len(matrix)
    if size == 0 or any(len(row) != size for row in matrix):
        raise ValueError("matrix must be square and non-empty")

    augmented: Matrix = [
        [float(value) for value in row] + [1.0 if i == j else 0.0 for j in range(size)]
        for i, row in enumerate(matrix)
    ]

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        pivot = augmented[pivot_row][column]
        if abs(pivot) < _SINGULAR_TOLERANCE:
            raise ValueError("matrix is singular and cannot be inverted")
        if pivot_row != column:
            augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]

        pivot = augmented[column][column]
        augmented[column] = [value / pivot for value in augmented[column]]

        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column], strict=True)
            ]

    return [row[size:] for row in augmented]


def turbulence_index(
    current_returns: Sequence[float],
    history: Sequence[Sequence[float]],
    *,
    ridge: float = 0.0,
) -> float:
    """Mahalanobis distance of ``current_returns`` from the history distribution.

    ``ridge`` adds a small value to the covariance diagonal, which keeps the
    inversion well-defined for nearly collinear return series.
    """
    dimension = _validate_history(history)
    if len(current_returns) != dimension:
        raise ValueError("current_returns must match the history dimension")
    if ridge < 0.0:
        raise ValueError("ridge must be non-negative")

    means = mean_vector(history)
    covariance = covariance_matrix(history)
    if ridge:
        for i in range(dimension):
            covariance[i][i] += ridge

    inverse = invert_matrix(covariance)
    deviation = [current_returns[i] - means[i] for i in range(dimension)]
    scaled = [
        sum(inverse[i][j] * deviation[j] for j in range(dimension)) for i in range(dimension)
    ]
    distance = sum(deviation[i] * scaled[i] for i in range(dimension))
    # Floating-point cancellation can make a near-zero distance slightly negative.
    return max(0.0, distance)


def rolling_turbulence(
    returns: Sequence[Sequence[float]],
    *,
    window: int = 252,
    min_periods: int | None = None,
    ridge: float = 0.0,
) -> tuple[float | None, ...]:
    """Rolling turbulence for each row, using the prior ``window`` rows as history.

    The first ``min_periods`` entries (default: ``window``) are ``None`` because
    there is not enough history to estimate a covariance matrix.
    """
    if window < 2:
        raise ValueError("window must be at least 2")
    if len(returns) < 2:
        raise ValueError("at least two return observations are required")
    dimension = _validate_history(returns)
    if any(len(row) != dimension for row in returns):
        raise ValueError("all return observations must have the same dimension")

    minimum = window if min_periods is None else min_periods
    if minimum < 2:
        raise ValueError("min_periods must be at least 2")

    readings: list[float | None] = []
    for index, current in enumerate(returns):
        if index < minimum:
            readings.append(None)
            continue
        start = max(0, index - window)
        readings.append(turbulence_index(current, returns[start:index], ridge=ridge))
    return tuple(readings)


def is_turbulent(value: float | None, threshold: float) -> bool:
    """Whether a turbulence reading breaches a configured threshold."""
    if threshold < 0.0:
        raise ValueError("threshold must be non-negative")
    return value is not None and math.isfinite(value) and value >= threshold
