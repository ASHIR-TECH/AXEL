"""Quant Research Agent (QRA) - Phase 10 Subsystem."""

from __future__ import annotations

from axel.research.baseline import BaselineResult, compare_to_baselines, default_baselines
from axel.research.dataset import DatasetLineage, DatasetRegistry, DatasetVersion, PointInTimeChecker, ensure_utc
from axel.research.experiment import ExperimentConfig, ExperimentRun, ExperimentRunner, ExperimentStatus
from axel.research.feature import FeatureDefinition, FeatureRegistry, FeatureVersion
from axel.research.governance import GovernanceGate, PromotionState
from axel.research.hypothesis import (
    Hypothesis,
    HypothesisRegistry,
    HypothesisStatus,
    HypothesisVersion,
    VALID_STATUS_TRANSITIONS,
)
from axel.research.pr import (
    RESTRICTED_PATH_PREFIXES,
    CodeChangeProposal,
    ProposalStatus,
    is_restricted_path,
    submit_proposal,
)
from axel.research.qra import QuantResearchAgent
from axel.research.report import Recommendation, ResearchReport, build_research_report
from axel.research.statistics import (
    brier_score,
    calibration_metrics,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    log_loss,
    multiple_testing_correction,
    probabilistic_sharpe_ratio,
    sharpe_ratio_stderr,
)
from axel.research.validation import (
    ValidationProtocol,
    holdout_split,
    perturb_parameters,
    purged_embargo_split,
    time_split,
    walk_forward_evaluate,
)

__all__ = [
    # Hypothesis
    "Hypothesis",
    "HypothesisRegistry",
    "HypothesisStatus",
    "HypothesisVersion",
    "VALID_STATUS_TRANSITIONS",
    # Dataset
    "DatasetLineage",
    "DatasetRegistry",
    "DatasetVersion",
    "PointInTimeChecker",
    "ensure_utc",
    # Feature
    "FeatureDefinition",
    "FeatureRegistry",
    "FeatureVersion",
    # Experiment
    "ExperimentConfig",
    "ExperimentRun",
    "ExperimentRunner",
    "ExperimentStatus",
    # Baseline
    "BaselineResult",
    "compare_to_baselines",
    "default_baselines",
    # Validation
    "ValidationProtocol",
    "holdout_split",
    "perturb_parameters",
    "purged_embargo_split",
    "time_split",
    "walk_forward_evaluate",
    # Statistics
    "brier_score",
    "calibration_metrics",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "log_loss",
    "multiple_testing_correction",
    "probabilistic_sharpe_ratio",
    "sharpe_ratio_stderr",
    # Report
    "Recommendation",
    "ResearchReport",
    "build_research_report",
    # PR & Governance
    "CodeChangeProposal",
    "ProposalStatus",
    "RESTRICTED_PATH_PREFIXES",
    "is_restricted_path",
    "submit_proposal",
    "GovernanceGate",
    "PromotionState",
    # Orchestrator
    "QuantResearchAgent",
]
