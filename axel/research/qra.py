"""Quant Research Agent orchestration - Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from axel.core.ids import generate_id
from axel.research.baseline import compare_to_baselines, default_baselines
from axel.research.experiment import ExperimentConfig, ExperimentRunner
from axel.research.hypothesis import Hypothesis, HypothesisRegistry
from axel.research.report import Recommendation, ResearchReport, build_research_report


@dataclass(frozen=True)
class QuantResearchAgent:
    hypothesis_registry: HypothesisRegistry = field(default_factory=HypothesisRegistry)
    experiment_runner: ExperimentRunner = field(default_factory=ExperimentRunner)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def propose_hypothesis(self, title: str, question: str, **kwargs: Any) -> Hypothesis:
        h = self.hypothesis_registry.create(title=title, question=question, **kwargs)
        return self.hypothesis_registry.register(h.hypothesis_id)

    def design_experiment(self, hypothesis: Hypothesis, **kwargs: Any) -> ExperimentConfig:
        run_id = kwargs.pop("run_id", generate_id("run"))
        return ExperimentConfig(
            run_id=run_id,
            hypothesis_id=hypothesis.hypothesis_id,
            hypothesis_version=hypothesis.version,
            **kwargs,
        )

    def run_experiment(self, config: ExperimentConfig) -> Any:
        self.experiment_runner.submit(config)
        return self.experiment_runner.run(config.run_id)

    def evaluate(self, run_id: str, hypothesis_id: str, hypothesis_version: int) -> ResearchReport:
        run = self.experiment_runner.get(run_id)
        candidate_metrics = run.metrics if run else {"sharpe": 0.0, "return": 0.0}
        baseline_cmp = compare_to_baselines(candidate_metrics, default_baselines())
        report = build_research_report(
            report_id=generate_id("rpt"),
            run_id=run_id,
            hypothesis_id=hypothesis_id,
            hypothesis_version=hypothesis_version,
            baseline_results=baseline_cmp,
            candidate_results=candidate_metrics,
            recommendation=Recommendation.INCONCLUSIVE,
            limitations=["Research-only; no guarantee of future performance."],
        )
        return report

    def propose_code_change(self, report: ResearchReport, **kwargs: Any) -> dict[str, Any]:
        return {
            "type": "code_change_proposal",
            "report_id": report.report_id,
            "hypothesis_id": report.hypothesis_id,
            "requires_independent_review": True,
            "can_auto_merge": False,
            **kwargs,
        }
