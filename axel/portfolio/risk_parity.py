"""
Equal Risk Contribution (ERC) / risk-parity allocation.

Adapted from the risk-parity workflow in the Sunday Quant Scientist series
(https://github.com/QuantScience/sunday-quant-scientist, QS023-risk-parity),
rewritten as a dependency-free deterministic solver.

ERC chooses long-only weights so that every asset contributes the same share of
portfolio volatility. The solver minimises Spinu's convex objective

    f(w) = 0.5 * w' * Sigma * w - (1/n) * sum(log(w_i))

with Newton steps. The log barrier keeps weights strictly positive and makes the
optimum unique; weights are normalised to sum to one at the end.

This produces an allocation *proposal*. AXEL's deterministic risk gates still
decide whether and how much is executable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from axel.portfolio.linalg import is_symmetric, mat_vec, quadratic_form
from axel.portfolio.risk import risk_contribution_percentages


@dataclass(frozen=True)
class ERCResult:
    """Risk-parity weights and the resulting risk-contribution shares."""

    weights: tuple[float, ...]
    risk_contributions: tuple[float, ...]
    iterations: int
    converged: bool


def _validate_covariance(covariance: Sequence[Sequence[float]]) -> list[list[float]]:
    matrix = [[float(value) for value in row] for row in covariance]
    if not matrix:
        raise ValueError("covariance must be non-empty")
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        raise ValueError("covariance must be square")
    for row in matrix:
        for value in row:
            if not math.isfinite(value):
                raise ValueError("covariance contains a non-finite value")
    if not is_symmetric(matrix):
        raise ValueError("covariance must be symmetric")
    if any(matrix[i][i] <= 0.0 for i in range(size)):
        raise ValueError("covariance diagonal must be strictly positive")
    return matrix


def _solve(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    """Solve ``matrix @ x = vector`` with partial-pivoting Gaussian elimination."""
    size = len(matrix)
    augmented = [
        [float(value) for value in matrix[row]] + [float(vector[row])]
        for row in range(size)
    ]
    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot_row][column]) < 1e-14:
            raise ValueError("linear system is singular")
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
    return [augmented[row][-1] for row in range(size)]


def solve_equal_risk_contribution(
    covariance: Sequence[Sequence[float]],
    *,
    max_iterations: int = 200,
    tolerance: float = 1e-10,
) -> ERCResult:
    """Solve for long-only ERC weights with Newton's method."""
    matrix = _validate_covariance(covariance)
    size = len(matrix)
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    if size == 1:
        return ERCResult((1.0,), (1.0,), 0, True)

    weights = [1.0 / math.sqrt(matrix[i][i]) for i in range(size)]
    total = sum(weights)
    weights = [weight / total for weight in weights]
    barrier = 1.0 / size

    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        sigma_w = mat_vec(matrix, weights)
        gradient = [
            sigma_w[i] - barrier / weights[i] for i in range(size)
        ]
        if max(abs(value) for value in gradient) < tolerance:
            converged = True
            break

        hessian = [row[:] for row in matrix]
        for i in range(size):
            hessian[i][i] += barrier / (weights[i] * weights[i])
        step = _solve(hessian, [-value for value in gradient])

        # Backtracking keeps the log barrier feasible and the objective falling.
        current = quadratic_form(weights, matrix) / 2.0 - barrier * sum(
            math.log(weight) for weight in weights
        )
        scale = 1.0
        while scale > 1e-12:
            candidate = [weights[i] + scale * step[i] for i in range(size)]
            if all(value > 0.0 for value in candidate):
                objective = quadratic_form(candidate, matrix) / 2.0 - barrier * sum(
                    math.log(value) for value in candidate
                )
                if objective <= current + 1e-12:
                    weights = candidate
                    break
            scale /= 2.0
        else:
            break

    total = sum(weights)
    normalized = tuple(weight / total for weight in weights)
    contributions = risk_contribution_percentages(normalized, matrix)
    return ERCResult(
        weights=normalized,
        risk_contributions=contributions,
        iterations=iterations,
        converged=converged,
    )


def equal_risk_contribution(
    covariance: Sequence[Sequence[float]],
    *,
    max_iterations: int = 200,
    tolerance: float = 1e-10,
) -> tuple[float, ...]:
    """Long-only ERC weights aligned to the input covariance order."""
    return solve_equal_risk_contribution(
        covariance, max_iterations=max_iterations, tolerance=tolerance
    ).weights
