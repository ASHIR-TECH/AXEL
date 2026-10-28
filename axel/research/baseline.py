"""Baseline comparisons for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BaselineResult:
    baseline_id: str
    name: str
    metrics: dict[str, Any] = field(default_factory=dict)
    period_start: str | None = None
    period_end: str | None = None
    comparable: bool = True
    notes: str = ""


def default_baselines() -> list[BaselineResult]:
    return [
        BaselineResult(baseline_id="cash", name="No-trade / Cash", metrics={"return": 0.0, "sharpe": 0.0}),
        BaselineResult(baseline_id="buyhold", name="Buy-and-Hold", metrics={"return": 0.0, "sharpe": 0.0}),
        BaselineResult(baseline_id="simple_rule", name="Simple Rule-based", metrics={"return": 0.0, "sharpe": 0.0}),
        BaselineResult(baseline_id="incumbent", name="Existing AXEL Strategy", metrics={"return": 0.0, "sharpe": 0.0}),
    ]


def compare_to_baselines(candidate_metrics: dict[str, Any], baselines: list[BaselineResult]) -> dict[str, Any]:
    comparisons = {}
    cand_sharpe = candidate_metrics.get("sharpe", 0.0)
    cand_return = candidate_metrics.get("return", 0.0)
    for b in baselines:
        b_sharpe = b.metrics.get("sharpe", 0.0)
        b_return = b.metrics.get("return", 0.0)
        comparisons[b.baseline_id] = {
            "baseline_sharpe": b_sharpe,
            "baseline_return": b_return,
            "sharpe_excess": cand_sharpe - b_sharpe,
            "return_excess": cand_return - b_return,
            "beats": cand_sharpe > b_sharpe or cand_return > b_return,
            "comparable": b.comparable,
        }
    return {
        "candidate": candidate_metrics,
        "baselines": [b.__dict__ for b in baselines],
        "comparisons": comparisons,
    }
