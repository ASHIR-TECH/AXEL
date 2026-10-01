"""Admission gates: a strategy may not advance on hope, only on evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class AdmissionCriteria:
    """Committing thresholds from the Phase 2 PRD."""

    min_trades: int = 100
    min_expectancy_r: float = 0.1
    max_expectancy_r: float = 0.5
    min_sharpe: float = 0.7
    min_max_drawdown: float = -0.20
    min_regimes: int = 2
    min_deflated_sharpe: float = 0.95


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    checks: tuple[tuple[str, bool], ...]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "admitted": self.admitted,
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
        }


def evaluate_admission(
    metrics: Mapping[str, float],
    *,
    deflated_sharpe: float,
    regimes: int,
    criteria: AdmissionCriteria | None = None,
) -> AdmissionDecision:
    rules = criteria or AdmissionCriteria()
    trades = metrics.get("trades", 0.0)
    expectancy = metrics.get("expectancy_r", 0.0)
    sharpe = metrics.get("sharpe", 0.0)
    drawdown = metrics.get("max_drawdown", 0.0)
    checks = (
        ("trade_count", trades >= rules.min_trades),
        ("expectancy_floor", expectancy >= rules.min_expectancy_r),
        ("expectancy_ceiling", expectancy <= rules.max_expectancy_r),
        ("sharpe", sharpe > rules.min_sharpe),
        ("drawdown", drawdown >= rules.min_max_drawdown),
        ("regimes", regimes >= rules.min_regimes),
        ("deflated_sharpe", deflated_sharpe >= rules.min_deflated_sharpe),
    )
    reasons = tuple(name for name, passed in checks if not passed)
    return AdmissionDecision(admitted=not reasons, checks=checks, reasons=reasons)


def needs_leakage_audit(expectancy_r: float, *, threshold: float = 1.0) -> bool:
    """Unrealistically good expectancy triggers an audit, never promotion."""
    return expectancy_r > threshold
