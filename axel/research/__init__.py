"""Quant Research Agent (QRA) - Phase 10 Subsystem."""

from __future__ import annotations

from axel.research.baseline import BaselineResult, compare_to_baselines, default_baselines
from axel.research.dataset import (
    DatasetLineage,
    DatasetRegistry,
    DatasetVersion,
    PointInTimeChecker,
    ensure_utc,
)
from axel.research.experiment import (
    ExperimentConfig,
    ExperimentRun,
    ExperimentRunner,
    ExperimentStatus,
)
from axel.research.feature import FeatureDefinition, FeatureRegistry, FeatureVersion
from axel.research.governance import GovernanceGate, PromotionState
from axel.research.hypothesis import (
    VALID_STATUS_TRANSITIONS,
    Hypothesis,
    HypothesisRegistry,
    HypothesisStatus,
    HypothesisVersion,
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
    "RESTRICTED_PATH_PREFIXES",
    "VALID_STATUS_TRANSITIONS",
    # Baseline
    "BaselineResult",
    # PR & Governance
    "CodeChangeProposal",
    # Dataset
    "DatasetLineage",
    "DatasetRegistry",
    "DatasetVersion",
    # Experiment
    "ExperimentConfig",
    "ExperimentRun",
    "ExperimentRunner",
    "ExperimentStatus",
    # Feature
    "FeatureDefinition",
    "FeatureRegistry",
    "FeatureVersion",
    "GovernanceGate",
    # Hypothesis
    "Hypothesis",
    "HypothesisRegistry",
    "HypothesisStatus",
    "HypothesisVersion",
    "PointInTimeChecker",
    "PromotionState",
    "ProposalStatus",
    # Orchestrator
    "QuantResearchAgent",
    # Report
    "Recommendation",
    "ResearchReport",
    # Validation
    "ValidationProtocol",
    # Statistics
    "brier_score",
    "build_research_report",
    "calibration_metrics",
    "compare_to_baselines",
    "default_baselines",
    "deflated_sharpe_ratio",
    "ensure_utc",
    "expected_max_sharpe",
    "holdout_split",
    "is_restricted_path",
    "log_loss",
    "multiple_testing_correction",
    "perturb_parameters",
    "probabilistic_sharpe_ratio",
    "purged_embargo_split",
    "sharpe_ratio_stderr",
    "submit_proposal",
    "time_split",
    "walk_forward_evaluate",
]
