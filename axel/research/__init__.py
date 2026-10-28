"""Quant Research Agent - Phase 10."""

from __future__ import annotations

from axel.research.hypothesis import Hypothesis, HypothesisRegistry, HypothesisStatus, HypothesisVersion
from axel.research.dataset import DatasetLineage, DatasetRegistry, DatasetVersion, PointInTimeChecker
from axel.research.feature import FeatureDefinition, FeatureRegistry, FeatureVersion
from axel.research.experiment import ExperimentConfig, ExperimentRunner, ExperimentRun, ExperimentStatus
from axel.research.baseline import BaselineResult, compare_to_baselines, default_baselines
from axel.research.validation import ValidationProtocol, holdout_split, purged_embargo_split, time_split, walk_forward_evaluate
from axel.research.statistics import deflated_sharpe_ratio, expected_max_sharpe, multiple_testing_correction, sharpe_ratio_stderr
from axel.research.report import ResearchReport, build_research_report
from axel.research.pr import CodeChangeProposal, ProposalStatus, submit_proposal
from axel.research.governance import GovernanceGate, PromotionState
from axel.research.qra import QuantResearchAgent

__all__ = [
    "Hypothesis", "HypothesisRegistry", "HypothesisStatus", "HypothesisVersion",
    "DatasetLineage", "DatasetRegistry", "DatasetVersion", "PointInTimeChecker",
    "FeatureDefinition", "FeatureRegistry", "FeatureVersion",
    "ExperimentConfig", "ExperimentRunner", "ExperimentRun", "ExperimentStatus",
    "BaselineResult", "compare_to_baselines", "default_baselines",
    "ValidationProtocol", "holdout_split", "purged_embargo_split", "time_split", "walk_forward_evaluate",
    "deflated_sharpe_ratio", "expected_max_sharpe", "multiple_testing_correction", "sharpe_ratio_stderr",
    "ResearchReport", "build_research_report",
    "CodeChangeProposal", "ProposalStatus", "submit_proposal",
    "GovernanceGate", "PromotionState", "QuantResearchAgent",
]
