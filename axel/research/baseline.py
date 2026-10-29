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
    ablation: bool = False
    notes: str = ""


def default_baselines() -> list[BaselineResult]:
    return [
        BaselineResult(
            baseline_id="cash",
            name="No-trade / Cash",
            metrics={"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "trades": 0},
            notes="Zero-risk baseline.",
        ),
        BaselineResult(
            baseline_id="buyhold",
            name="Buy-and-Hold Benchmark",
            metrics={"return": 0.08, "sharpe": 0.65, "max_drawdown": 0.18, "trades": 1},
            notes="Passive holding benchmark.",
        ),
        BaselineResult(
            baseline_id="simple_trend",
            name="Simple Moving Average / Trend",
            metrics={"return": 0.06, "sharpe": 0.55, "max_drawdown": 0.14, "trades": 24},
            notes="Naïve 3-day trend-following baseline.",
        ),
        BaselineResult(
            baseline_id="incumbent",
            name="Incumbent Approved Strategy",
            metrics={"return": 0.12, "sharpe": 0.95, "max_drawdown": 0.11, "trades": 60},
            notes="Current production or validated strategy.",
        ),
    ]


def compare_to_baselines(
    candidate_metrics: dict[str, Any],
    baselines: list[BaselineResult],
    primary_metric: str = "sharpe",
) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    cand_sharpe = float(candidate_metrics.get("sharpe", 0.0))
    cand_return = float(candidate_metrics.get("return", candidate_metrics.get("expectancy_r", 0.0)))
    cand_dd = float(candidate_metrics.get("max_drawdown", 0.0))

    all_beaten = len(baselines) > 0

    for b in baselines:
        b_sharpe = float(b.metrics.get("sharpe", 0.0))
        b_return = float(b.metrics.get("return", b.metrics.get("expectancy_r", 0.0)))
        b_dd = float(b.metrics.get("max_drawdown", 0.0))

        sharpe_excess = cand_sharpe - b_sharpe
        return_excess = cand_return - b_return
        drawdown_excess = cand_dd - b_dd

        # Rule per PRD §6.5: A candidate that does not outperform a relevant baseline
        # should not be represented as an improvement merely because its absolute historical return is positive.
        if primary_metric == "sharpe":
            beats = (cand_sharpe > b_sharpe) and (cand_return >= b_return) and (cand_sharpe > 0.0)
        else:
            beats = (cand_return > b_return) and (cand_sharpe >= b_sharpe)

        if not beats and b.comparable:
            all_beaten = False

        comparisons[b.baseline_id] = {
            "name": b.name,
            "baseline_sharpe": b_sharpe,
            "baseline_return": b_return,
            "baseline_drawdown": b_dd,
            "sharpe_excess": sharpe_excess,
            "return_excess": return_excess,
            "drawdown_excess": drawdown_excess,
            "beats": beats,
            "comparable": b.comparable,
            "ablation": b.ablation,
            "notes": b.notes,
        }

    return {
        "candidate": candidate_metrics,
        "comparisons": comparisons,
        "overall_beats_comparable_baselines": all_beaten,
    }
