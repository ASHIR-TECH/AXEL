"""Population Stability Index and drift detection for Phase 14.

Implements §10.3 of the Developer 2 spec.  PSI is a drift indicator
that compares actual vs. expected feature distributions.  It does NOT
prove a model is unsafe -- it flags conditions for human review.

Smoothing: when a bin has zero observations, a small constant (epsilon)
is added to both numerator and denominator to avoid log(0) and division
by zero.  The default epsilon = 1e-4 (documented per §10.3 requirement).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

# --- PSI thresholds (industry convention) ---
PSI_STABLE = 0.10
PSI_MODERATE = 0.25


def _bin_proportions(
    values: Sequence[float],
    edges: Sequence[float],
) -> list[float]:
    """Count the proportion of *values* in each bin defined by *edges*.

    ``edges`` has ``n_bins + 1`` elements; values < edges[0] go in bin 0,
    values >= edges[-1] go in the last bin.
    """
    n_bins = len(edges) - 1
    if n_bins <= 0:
        return []
    counts = [0] * n_bins
    for v in values:
        placed = False
        for i in range(n_bins):
            if v < edges[i + 1]:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[n_bins - 1] += 1
    total = sum(counts) or 1
    return [c / total for c in counts]


def _quantile_edges(values: Sequence[float], n_bins: int) -> list[float]:
    """Return *n_bins + 1* edges from the quantiles of *values*."""
    if not values or n_bins <= 0:
        return []
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    edges: list[float] = []
    for i in range(n_bins + 1):
        idx = min(int(i * n / n_bins), n - 1)
        edges.append(sorted_vals[idx])
    # Ensure first and last edges cover the full range
    edges[0] = sorted_vals[0] - 1.0
    edges[-1] = sorted_vals[-1] + 1.0
    return edges


def population_stability_index(
    expected: Sequence[float],
    actual: Sequence[float],
    n_bins: int = 10,
    epsilon: float = 1e-4,
) -> float:
    """Compute the Population Stability Index (§10.3).

    Parameters
    ----------
    expected : reference distribution (e.g. training data).
    actual   : current distribution (e.g. recent predictions).
    n_bins   : number of equal-frequency bins built from *expected*.
    epsilon  : smoothing constant for empty bins (documented requirement).

    Returns
    -------
    PSI value.  Industry convention:
      < 0.10 → stable
      0.10–0.25 → moderate drift
      > 0.25 → significant drift
    """
    if not expected or not actual:
        return 0.0
    edges = _quantile_edges(expected, n_bins)
    if not edges:
        return 0.0
    e_props = _bin_proportions(expected, edges)
    a_props = _bin_proportions(actual, edges)

    psi = 0.0
    for e_i, a_i in zip(e_props, a_props):
        # Apply smoothing to avoid log(0) and division by zero
        e_s = e_i + epsilon
        a_s = a_i + epsilon
        psi += (a_s - e_s) * math.log(a_s / e_s)
    return psi


def psi_alert_level(psi: float) -> str:
    """Classify a PSI value into an alert level."""
    if psi < PSI_STABLE:
        return "stable"
    if psi < PSI_MODERATE:
        return "moderate_drift"
    return "significant_drift"


@dataclass(frozen=True)
class FeatureDriftResult:
    """Drift assessment for a single feature."""
    feature_name: str
    psi: float
    alert_level: str
    reference_mean: float
    current_mean: float
    reference_std: float
    current_std: float


@dataclass(frozen=True)
class DriftReport:
    """Full drift report across multiple features (§10.2)."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    feature_results: list[FeatureDriftResult] = field(default_factory=list)
    overall_alert: str = "stable"
    features_with_drift: list[str] = field(default_factory=list)
    summary: str = ""


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def feature_drift_report(
    reference: dict[str, Sequence[float]],
    current: dict[str, Sequence[float]],
    n_bins: int = 10,
    epsilon: float = 1e-4,
) -> DriftReport:
    """Compare reference and current feature distributions.

    Parameters
    ----------
    reference : dict mapping feature name → reference values
    current   : dict mapping feature name → current values

    Returns a DriftReport with per-feature PSI and overall alert level.
    """
    results: list[FeatureDriftResult] = []
    drifted: list[str] = []
    worst_level = "stable"

    common_features = sorted(set(reference) & set(current))
    for name in common_features:
        ref_vals = reference[name]
        cur_vals = current[name]
        psi = population_stability_index(ref_vals, cur_vals, n_bins, epsilon)
        level = psi_alert_level(psi)
        results.append(FeatureDriftResult(
            feature_name=name,
            psi=psi,
            alert_level=level,
            reference_mean=_mean(ref_vals),
            current_mean=_mean(cur_vals),
            reference_std=_std(ref_vals),
            current_std=_std(cur_vals),
        ))
        if level != "stable":
            drifted.append(name)
        if level == "significant_drift":
            worst_level = "significant_drift"
        elif level == "moderate_drift" and worst_level == "stable":
            worst_level = "moderate_drift"

    return DriftReport(
        feature_results=results,
        overall_alert=worst_level,
        features_with_drift=drifted,
        summary=f"{len(drifted)}/{len(results)} features show drift" if results else "no features compared",
    )


class DriftMonitor:
    """Stateful drift monitor that stores reference distributions (§10.2)."""

    def __init__(self, n_bins: int = 10, epsilon: float = 1e-4) -> None:
        self._n_bins = n_bins
        self._epsilon = epsilon
        self._reference: dict[str, list[float]] = {}
        self._history: list[DriftReport] = []

    def set_reference(self, features: dict[str, Sequence[float]]) -> None:
        self._reference = {k: list(v) for k, v in features.items()}

    def check(self, current: dict[str, Sequence[float]]) -> DriftReport:
        report = feature_drift_report(
            self._reference, current, self._n_bins, self._epsilon
        )
        self._history.append(report)
        return report

    @property
    def history(self) -> list[DriftReport]:
        return list(self._history)

    @property
    def reference_features(self) -> list[str]:
        return sorted(self._reference)
