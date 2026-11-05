"""
Hierarchical Risk Parity (HRP) allocation.

Adapted from the HRP workflow in the Sunday Quant Scientist series
(https://github.com/QuantScience/sunday-quant-scientist, QS022-hrp), rewritten
as a dependency-free deterministic implementation.

HRP (Lopez de Prado, 2016) avoids inverting the covariance matrix:

1. Convert covariance to correlation and then to the distance
   ``d_ij = sqrt(0.5 * (1 - corr_ij))``.
2. Build a single-linkage hierarchical clustering tree.
3. Quasi-diagonalise the tree into a leaf order.
4. Recursively bisect the order, splitting capital between the two halves in
   inverse proportion to their inverse-variance portfolio variance.

The result is long-only, fully invested, and deterministic. It is an allocation
*proposal*; AXEL's deterministic risk gates still decide what is executable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from axel.portfolio.linalg import is_symmetric


@dataclass(frozen=True)
class HRPResult:
    """HRP weights plus the clustering artefacts used to produce them."""

    instrument_ids: tuple[str, ...]
    weights: tuple[float, ...]
    order: tuple[int, ...]
    linkage: tuple[tuple[int, int, float], ...]


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


def correlation_from_covariance(
    covariance: Sequence[Sequence[float]],
) -> list[list[float]]:
    """Correlation matrix implied by a covariance matrix (clipped to [-1, 1])."""
    matrix = _validate_covariance(covariance)
    size = len(matrix)
    standard_deviations = [math.sqrt(matrix[i][i]) for i in range(size)]
    correlation: list[list[float]] = []
    for i in range(size):
        row = []
        for j in range(size):
            if i == j:
                row.append(1.0)
                continue
            value = matrix[i][j] / (standard_deviations[i] * standard_deviations[j])
            row.append(max(-1.0, min(1.0, value)))
        correlation.append(row)
    return correlation


def distance_matrix(correlation: Sequence[Sequence[float]]) -> list[list[float]]:
    """Correlation distance ``sqrt(0.5 * (1 - corr))`` used by HRP clustering."""
    size = len(correlation)
    if size == 0 or any(len(row) != size for row in correlation):
        raise ValueError("correlation must be square and non-empty")
    return [
        [math.sqrt(max(0.0, 0.5 * (1.0 - float(correlation[i][j])))) for j in range(size)]
        for i in range(size)
    ]


def _single_linkage(distances: Sequence[Sequence[float]]) -> tuple[list[tuple[int, int, float]], list[int]]:
    """Naive O(n^3) single-linkage clustering; returns merges and leaf order."""
    size = len(distances)
    clusters: dict[int, list[int]] = {index: [index] for index in range(size)}
    active = list(range(size))
    merges: list[tuple[int, int, float]] = []
    children: dict[int, tuple[int, int]] = {}
    next_id = size

    while len(active) > 1:
        best: tuple[float, int, int] | None = None
        for position, left in enumerate(active):
            for right in active[position + 1 :]:
                distance = min(distances[i][j] for i in clusters[left] for j in clusters[right])
                candidate = (distance, left, right)
                if best is None or candidate < best:
                    best = candidate
        assert best is not None
        distance, left, right = best
        merged = next_id
        next_id += 1
        clusters[merged] = clusters[left] + clusters[right]
        children[merged] = (left, right)
        merges.append((left, right, distance))
        active = [item for item in active if item not in (left, right)]
        active.append(merged)

    root = active[0]
    order: list[int] = []

    def _visit(node: int) -> None:
        if node in children:
            left, right = children[node]
            _visit(left)
            _visit(right)
        else:
            order.append(node)

    _visit(root)
    return merges, order


def cluster_variance(
    covariance: Sequence[Sequence[float]],
    indices: Sequence[int],
) -> float:
    """Variance of the inverse-variance portfolio formed from ``indices``."""
    if not indices:
        raise ValueError("indices must be non-empty")
    inverse_variances = [1.0 / covariance[index][index] for index in indices]
    total = sum(inverse_variances)
    if total <= 0.0:
        raise ValueError("inverse variances must sum to a positive value")
    weights = [value / total for value in inverse_variances]
    variance = 0.0
    for i, left in enumerate(indices):
        for j, right in enumerate(indices):
            variance += weights[i] * weights[j] * covariance[left][right]
    return variance


def _hrp(
    matrix: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], list[tuple[int, int, float]], list[int]]:
    size = len(matrix)
    if size == 1:
        return (1.0,), [], [0]

    correlation = correlation_from_covariance(matrix)
    merges, order = _single_linkage(distance_matrix(correlation))

    weights = [0.0] * size
    stack: list[tuple[list[int], float]] = [(order, 1.0)]
    while stack:
        indices, allocation = stack.pop()
        if len(indices) == 1:
            weights[indices[0]] = allocation
            continue
        midpoint = len(indices) // 2
        left, right = indices[:midpoint], indices[midpoint:]
        left_variance = cluster_variance(matrix, left)
        right_variance = cluster_variance(matrix, right)
        total_variance = left_variance + right_variance
        left_share = 0.5 if total_variance <= 0.0 else 1.0 - left_variance / total_variance
        stack.append((left, allocation * left_share))
        stack.append((right, allocation * (1.0 - left_share)))

    total = sum(weights)
    return tuple(weight / total for weight in weights), merges, order


def hrp_weights(covariance: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Long-only HRP weights aligned to the input covariance order."""
    matrix = _validate_covariance(covariance)
    weights, _, _ = _hrp(matrix)
    return weights


def hierarchical_risk_parity(
    covariance: Sequence[Sequence[float]],
    *,
    instrument_ids: Sequence[str] | None = None,
) -> HRPResult:
    """HRP weights with optional instrument labels and clustering artefacts."""
    matrix = _validate_covariance(covariance)
    size = len(matrix)
    if instrument_ids is None:
        labels = tuple(str(index) for index in range(size))
    else:
        if len(instrument_ids) != size:
            raise ValueError("instrument_ids must match the covariance dimension")
        labels = tuple(instrument_ids)

    weights, merges, order = _hrp(matrix)
    return HRPResult(
        instrument_ids=labels,
        weights=weights,
        order=tuple(order),
        linkage=tuple(merges),
    )
