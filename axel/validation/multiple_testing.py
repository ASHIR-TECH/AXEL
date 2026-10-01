"""Multiple-testing accounting for strategy search."""

from __future__ import annotations

from collections.abc import Sequence
from math import log, sqrt


def expected_max_of_normals(n_trials: int) -> float:
    """Expected maximum of ``n_trials`` independent standard normals."""
    if n_trials < 2:
        return 0.0
    return sqrt(2.0 * log(n_trials))


def bonferroni_threshold(alpha: float, n_trials: int) -> float:
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    return alpha / n_trials


def benjamini_hochberg(pvalues: Sequence[float], *, alpha: float = 0.05) -> list[int]:
    """Return indices rejected by the Benjamini-Hochberg FDR procedure."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if any(not 0.0 <= value <= 1.0 for value in pvalues):
        raise ValueError("p-values must be within [0, 1]")
    total = len(pvalues)
    if total == 0:
        return []
    ranked = sorted(range(total), key=lambda index: pvalues[index])
    cutoff_rank = 0
    for rank, index in enumerate(ranked, start=1):
        if pvalues[index] <= rank / total * alpha:
            cutoff_rank = rank
    rejected = [ranked[position] for position in range(cutoff_rank)]
    return sorted(rejected)


def multiple_testing_summary(observed_sharpe: float, *, n_trials: int) -> dict[str, float]:
    """Compare an observed Sharpe to the expected best of ``n_trials`` under the null."""
    benchmark = expected_max_of_normals(n_trials)
    return {
        "observed_sharpe": observed_sharpe,
        "n_trials": float(n_trials),
        "expected_max_z": benchmark,
        "margin": observed_sharpe - benchmark,
        "beats_multiple_testing": float(observed_sharpe > benchmark),
    }
