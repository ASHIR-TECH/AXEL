"""Validation: walk-forward, robustness, multiple-testing and admission gates."""

from __future__ import annotations

from axel.validation.admission import (
    AdmissionCriteria,
    AdmissionDecision,
    evaluate_admission,
    needs_leakage_audit,
)
from axel.validation.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    norm_cdf,
    norm_ppf,
    probabilistic_sharpe_ratio,
)
from axel.validation.multiple_testing import (
    benjamini_hochberg,
    bonferroni_threshold,
    expected_max_of_normals,
    multiple_testing_summary,
)
from axel.validation.report import (
    REPORT_VERSION,
    FoldResult,
    ValidationReport,
    build_validation_report,
    run_fold,
)
from axel.validation.robustness import (
    RobustnessReport,
    parameter_variants,
    perturb,
    perturbed_values,
    robustness_report,
    sign_stable,
)
from axel.validation.walk_forward import Fold, fold_datetimes, holdout_fold, walk_forward_folds

__all__ = [
    "REPORT_VERSION",
    "AdmissionCriteria",
    "AdmissionDecision",
    "Fold",
    "FoldResult",
    "RobustnessReport",
    "ValidationReport",
    "benjamini_hochberg",
    "bonferroni_threshold",
    "build_validation_report",
    "deflated_sharpe_ratio",
    "evaluate_admission",
    "expected_max_of_normals",
    "expected_max_sharpe",
    "fold_datetimes",
    "holdout_fold",
    "multiple_testing_summary",
    "needs_leakage_audit",
    "norm_cdf",
    "norm_ppf",
    "parameter_variants",
    "perturb",
    "perturbed_values",
    "probabilistic_sharpe_ratio",
    "robustness_report",
    "run_fold",
    "sign_stable",
    "walk_forward_folds",
]
