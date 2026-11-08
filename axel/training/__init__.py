"""Training-side contracts: dataset folds, evaluation and model artifacts.

Nothing in this package is imported by the deterministic risk, execution or
portfolio layers. It prepares data for a model and scores the result; it never
authorizes an order.
"""

from axel.training.artifact import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactError,
    ModelArtifact,
    build_artifact,
    load_artifact,
    sha256_file,
    write_artifact,
)
from axel.training.dataset import (
    FoldPlan,
    load_bars_csv,
    prepare_folds,
    write_folds,
    write_holdout,
)
from axel.training.evaluation import (
    EvaluationReport,
    Prediction,
    brier_score,
    directional_accuracy,
    evaluate,
    load_predictions_csv,
    mean_absolute_error,
    strategy_returns,
    trend_baseline_sharpe,
)
from axel.training.qlib import (
    dump_bin_command,
    dump_qlib_bin,
    typical_price,
    write_qlib_csvs,
)

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ArtifactError",
    "EvaluationReport",
    "FoldPlan",
    "ModelArtifact",
    "Prediction",
    "brier_score",
    "build_artifact",
    "directional_accuracy",
    "dump_bin_command",
    "dump_qlib_bin",
    "evaluate",
    "load_artifact",
    "load_bars_csv",
    "load_predictions_csv",
    "mean_absolute_error",
    "prepare_folds",
    "sha256_file",
    "strategy_returns",
    "trend_baseline_sharpe",
    "typical_price",
    "write_artifact",
    "write_folds",
    "write_holdout",
    "write_qlib_csvs",
]
