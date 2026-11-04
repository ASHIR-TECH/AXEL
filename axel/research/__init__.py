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
from axel.research.drift import (
    DriftMonitor,
    DriftReport,
    FeatureDriftResult,
    feature_drift_report,
    population_stability_index,
    psi_alert_level,
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
from axel.research.model_registry import (
    DeploymentState,
    ModelRecord,
    ModelRegistry,
    PerformanceSnapshot,
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
    "BaselineResult",
    "CodeChangeProposal",
    "DatasetLineage",
    "DatasetRegistry",
    "DatasetVersion",
    "DeploymentState",
    "DriftMonitor",
    "DriftReport",
    "ExperimentConfig",
    "ExperimentRun",
    "ExperimentRunner",
    "ExperimentStatus",
    "FeatureDefinition",
    "FeatureDriftResult",
    "FeatureRegistry",
    "FeatureVersion",
    "GovernanceGate",
    "Hypothesis",
    "HypothesisRegistry",
    "HypothesisStatus",
    "HypothesisVersion",
    "ModelRecord",
    "ModelRegistry",
    "PerformanceSnapshot",
    "PointInTimeChecker",
    "PromotionState",
    "ProposalStatus",
    "QuantResearchAgent",
    "Recommendation",
    "ResearchReport",
    "ValidationProtocol",
    "brier_score",
    "build_research_report",
    "calibration_metrics",
    "compare_to_baselines",
    "default_baselines",
    "deflated_sharpe_ratio",
    "ensure_utc",
    "expected_max_sharpe",
    "feature_drift_report",
    "holdout_split",
    "is_restricted_path",
    "log_loss",
    "multiple_testing_correction",
    "perturb_parameters",
    "population_stability_index",
    "probabilistic_sharpe_ratio",
    "psi_alert_level",
    "purged_embargo_split",
    "sharpe_ratio_stderr",
    "submit_proposal",
    "time_split",
    "walk_forward_evaluate",
]
