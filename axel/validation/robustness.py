"""Parameter perturbation and expectancy-sign stability checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def perturb(value: float, *, pct: float = 0.2, direction: int = 1) -> float:
    if pct < 0:
        raise ValueError("pct cannot be negative")
    return value * (1.0 + direction * pct)


def perturbed_values(value: float, *, pct: float = 0.2) -> tuple[float, float]:
    return (perturb(value, pct=pct, direction=-1), perturb(value, pct=pct, direction=1))


def parameter_variants(
    params: Mapping[str, float], *, pct: float = 0.2
) -> list[dict[str, float]]:
    """One-at-a-time +/- perturbation of each numeric parameter."""
    variants: list[dict[str, float]] = []
    for key, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for direction in (-1, 1):
            variant = dict(params)
            variant[key] = perturb(float(value), pct=pct, direction=direction)
            variants.append(variant)
    return variants


def sign_stable(values: Sequence[float]) -> bool:
    """True if all values share a sign (zeros ignored)."""
    signs = {(value > 0) - (value < 0) for value in values if value != 0}
    return len(signs) <= 1


@dataclass(frozen=True)
class RobustnessReport:
    base: float
    worst: float
    best: float
    stable: bool

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "base": self.base,
            "worst": self.worst,
            "best": self.best,
            "stable": self.stable,
        }


def robustness_report(base: float, variants: Sequence[float]) -> RobustnessReport:
    sample = [base, *variants]
    return RobustnessReport(
        base=base,
        worst=min(sample),
        best=max(sample),
        stable=sign_stable(sample),
    )
