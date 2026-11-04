"""Quant Research Agent (QRA) lifecycle orchestration for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from axel.core.ids import generate_id
from axel.data.models import Bar
from axel.research.baseline import compare_to_baselines, default_baselines
from axel.research.dataset import DatasetRegistry, PointInTimeChecker
from axel.research.experiment import ExperimentConfig, ExperimentRun, ExperimentRunner
from axel.research.feature import FeatureRegistry
from axel.research.hypothesis import Hypothesis, HypothesisRegistry
from axel.research.model_registry import ModelRecord, ModelRegistry
from axel.research.pr import CodeChangeProposal, submit_proposal
from axel.research.report import Recommendation, ResearchReport, build_research_report
from axel.research.statistics import deflated_sharpe_ratio


@dataclass(frozen=True)
class QuantResearchAgent:
    """Untrusted research proposal generator (PRD §3.1).

    Proposes hypotheses, configurations, and reviewable PRs.
    Possesses NO production execution or trading authorization authority.
    """

    hypothesis_registry: HypothesisRegistry = field(default_factory=HypothesisRegistry)
    dataset_registry: DatasetRegistry = field(default_factory=DatasetRegistry)
    feature_registry: FeatureRegistry = field(default_factory=FeatureRegistry)
    model_registry: ModelRegistry = field(default_factory=ModelRegistry)
    experiment_runner: ExperimentRunner = field(init=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        pit_checker = PointInTimeChecker(self.dataset_registry)
        runner = ExperimentRunner(
            hypothesis_registry=self.hypothesis_registry,
            pit_checker=pit_checker,
        )
        object.__setattr__(self, "experiment_runner", runner)

    def propose_hypothesis(
        self,
        title: str,
        question: str,
        economic_rationale: str = "",
        proposed_mechanism: str = "",
        target_definition: str = "",
        **kwargs: Any,
    ) -> Hypothesis:
        h = self.hypothesis_registry.create(
            title=title,
            question=question,
            economic_rationale=economic_rationale,
            proposed_mechanism=proposed_mechanism,
            target_definition=target_definition,
            **kwargs,
        )
        return self.hypothesis_registry.register(h.hypothesis_id)

    def design_experiment(self, hypothesis: Hypothesis, **kwargs: Any) -> ExperimentConfig:
        run_id = kwargs.pop("run_id", generate_id("run"))
        return ExperimentConfig(
            run_id=run_id,
            hypothesis_id=hypothesis.hypothesis_id,
            hypothesis_version=hypothesis.version,
            **kwargs,
        )

    def run_experiment(
        self,
        config: ExperimentConfig,
        bars: list[Bar] | None = None,
        returns: list[float] | None = None,
    ) -> ExperimentRun:
        self.experiment_runner.submit(config)
        return self.experiment_runner.run(config.run_id, bars=bars, returns=returns)

    def evaluate(
        self,
        run_id: str,
        hypothesis_id: str,
        hypothesis_version: int,
        num_trials: int = 1,
    ) -> ResearchReport:
        run = self.experiment_runner.get(run_id)
        candidate_metrics = run.metrics if run else {"sharpe": 0.0, "expectancy_r": 0.0, "trades": 0}

        baselines = default_baselines()
        baseline_cmp = compare_to_baselines(candidate_metrics, baselines)

        cand_sharpe = float(candidate_metrics.get("sharpe", 0.0))
        n_trades = int(candidate_metrics.get("trades", 0))

        dsr_val = deflated_sharpe_ratio(
            observed_sharpe=cand_sharpe,
            n=max(n_trades, 2),
            num_trials=max(num_trials, 1),
        )

        stat_results = {
            "observed_sharpe": cand_sharpe,
            "num_trials": num_trials,
            "sample_size": n_trades,
            "dsr": round(dsr_val, 4),
        }

        # Deterministic evidence-based recommendation rule
        if not baseline_cmp.get("overall_beats_comparable_baselines", False) or cand_sharpe <= 0.0:
            rec = Recommendation.REJECT
        elif dsr_val > 0.50 and n_trades >= 30:
            rec = Recommendation.SUBMIT_FOR_REVIEW
        else:
            rec = Recommendation.INCONCLUSIVE

        report = build_research_report(
            report_id=generate_id("rpt"),
            run_id=run_id,
            hypothesis_id=hypothesis_id,
            hypothesis_version=hypothesis_version,
            baseline_results=baseline_cmp,
            candidate_results=candidate_metrics,
            statistical_results=stat_results,
            recommendation=rec,
            limitations=[
                "Research evaluation only. Backtest metrics do not guarantee live profitability.",
                "Slippage and fees modeled as fixed bps; actual market impact may vary.",
            ],
            required_reviewer_decisions=[
                "Verify point-in-time dataset lineage.",
                "Confirm no look-ahead leakage in feature computation.",
                "Check risk limits and parameter sensitivity.",
                "Approve or deny transition to paper validation.",
            ],
        )
        return report

    def register_model_candidate(
        self,
        report: ResearchReport,
        model_id: str,
        version: str,
        owner: str = "qra",
        purpose: str = "",
        asset_classes: list[str] | None = None,
        known_limitations: list[str] | None = None,
        **kwargs: Any,
    ) -> ModelRecord:
        """Register an evaluated experiment report as a candidate model in the ModelRegistry (§6.4, §10.1)."""
        run = self.experiment_runner.get(report.run_id)
        train_data: list[tuple[str, str]] = run.config.dataset_versions if run else []
        feat_versions: list[tuple[str, str]] = run.config.feature_versions if run else []
        commit = run.config.code_commit if run else ""
        m_hash = run.config.config_hash if run else ""

        record = ModelRecord(
            model_id=model_id,
            version=version,
            owner=owner,
            purpose=purpose or f"Research model evaluated for hypothesis {report.hypothesis_id}",
            asset_classes=asset_classes or ["stocks"],
            training_data=train_data,
            feature_versions=feat_versions,
            experiment_history=[report.run_id],
            evaluation_results={
                "candidate_metrics": report.candidate_results,
                "baseline_results": report.baseline_results,
                "statistical_results": report.statistical_results,
                "recommendation": report.recommendation.value,
            },
            known_limitations=known_limitations or report.limitations,
            code_commit=commit,
            model_hash=m_hash,
            **kwargs,
        )
        self.model_registry.register(record)
        return record

    def propose_code_change(
        self,
        report: ResearchReport,
        source_branch: str,
        files_changed: list[str],
        summary: str,
        intended_behavior_change: str,
        test_evidence_ref: str,
        rollback_plan: str = "Revert branch",
        risk_assessment: str = "Standard research strategy variant",
        confirmation_restricted_not_modified: bool = True,
        **kwargs: Any,
    ) -> CodeChangeProposal:
        proposal = CodeChangeProposal(
            proposal_id=generate_id("pr"),
            report_id=report.report_id,
            hypothesis_id=report.hypothesis_id,
            experiment_run_id=report.run_id,
            source_branch=source_branch,
            files_changed=files_changed,
            summary=summary,
            intended_behavior_change=intended_behavior_change,
            test_evidence_ref=test_evidence_ref,
            rollback_plan=rollback_plan,
            risk_assessment=risk_assessment,
            confirmation_restricted_not_modified=confirmation_restricted_not_modified,
            created_by="qra",
            **kwargs,
        )
        return submit_proposal(proposal)
