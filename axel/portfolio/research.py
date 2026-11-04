"""
P11-QA: point-in-time, walk-forward, and performance-validation helpers.

The functions here are deterministic research utilities, not trading logic.
They make leakage and multiple-testing assumptions explicit so an allocation
method cannot be promoted from an unreported in-sample search. No function
enables live trading or mutates a risk limit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil, isfinite, log, pi, sqrt
from random import Random
from statistics import NormalDist, mean
from typing import Any

from axel.portfolio.types import require_aware


class ResearchValidationError(ValueError):
    """Raised when a research input violates a stated validation rule."""


class LookaheadError(ResearchValidationError):
    """Raised when a feature or label would not have been available in time."""


@dataclass(frozen=True)
class WalkForwardWindow:
    """One train/test split with a strict time ordering and optional embargo."""

    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    embargo: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        for name in ("train_start", "train_end", "test_start", "test_end"):
            object.__setattr__(self, name, require_aware(getattr(self, name), name))
        if not self.train_start < self.train_end <= self.test_start < self.test_end:
            raise ResearchValidationError(
                "walk-forward windows must satisfy train_start < train_end <= "
                "test_start < test_end"
            )
        if self.embargo < timedelta(0):
            raise ResearchValidationError("embargo cannot be negative")
        if self.test_start - self.train_end != self.embargo:
            raise ResearchValidationError("embargo must equal test_start - train_end")

    def is_out_of_sample(self, timestamp: datetime) -> bool:
        moment = require_aware(timestamp, "timestamp")
        return self.test_start <= moment < self.test_end


def walk_forward_windows(
    *,
    start: datetime,
    end: datetime,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
    embargo_days: int = 0,
) -> tuple[WalkForwardWindow, ...]:
    """Build expanding-fixed windows that never let test data enter training."""
    start = require_aware(start, "start")
    end = require_aware(end, "end")
    if start >= end:
        raise ResearchValidationError("start must precede end")
    if train_days < 1 or test_days < 1:
        raise ResearchValidationError("train_days and test_days must be positive")
    if step_days is None:
        step_days = test_days
    if step_days < 1:
        raise ResearchValidationError("step_days must be positive")
    if embargo_days < 0:
        raise ResearchValidationError("embargo_days cannot be negative")
    embargo = timedelta(days=embargo_days)
    windows: list[WalkForwardWindow] = []
    train_start = start
    while True:
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end + embargo
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                embargo=embargo,
            )
        )
        train_start = train_start + timedelta(days=step_days)
    if not windows:
        raise ResearchValidationError("the requested range does not contain a complete window")
    return tuple(windows)


def point_in_time_available(*, available_at: datetime, decision_at: datetime) -> bool:
    """Return whether an observation was available at the decision time."""
    return require_aware(available_at, "available_at") <= require_aware(
        decision_at, "decision_at"
    )


def require_point_in_time(*, available_at: datetime, decision_at: datetime) -> None:
    if not point_in_time_available(available_at=available_at, decision_at=decision_at):
        raise LookaheadError(
            f"observation available at {available_at.isoformat()} was used at "
            f"{decision_at.isoformat()}"
        )


@dataclass(frozen=True)
class PerformanceMetrics:
    """Net performance metrics with explicit conventions and warnings."""

    periods: int
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    max_drawdown: float
    tracking_error: float | None
    turnover: float
    total_cost: float
    net_return: float
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "total_return",
            "annualized_return",
            "annualized_volatility",
            "max_drawdown",
            "turnover",
            "total_cost",
            "net_return",
        ):
            value = getattr(self, name)
            if not isfinite(value):
                raise ResearchValidationError(f"{name} must be finite")


def _drawdown(wealth: Sequence[float]) -> float:
    peak = wealth[0]
    worst = 0.0
    for value in wealth:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def performance_metrics(
    returns: Sequence[float],
    *,
    benchmark_returns: Sequence[float] = (),
    risk_free_returns: Sequence[float] = (),
    costs: Sequence[float] = (),
    turnover: Sequence[float] = (),
    periods_per_year: float = 252.0,
    target_return: float = 0.0,
    confidence: float = 0.95,
) -> PerformanceMetrics:
    """Compute net performance using decimal returns and consistent units.

    ``costs`` must be decimal return costs, not cash amounts. If they are cash,
    convert them to the same return base before calling this function.
    """
    values = tuple(float(value) for value in returns)
    if not values:
        raise ResearchValidationError("returns cannot be empty")
    if any(not isfinite(value) or value <= -1.0 for value in values):
        raise ResearchValidationError("returns must be finite and greater than -100%")
    if not isfinite(periods_per_year) or periods_per_year <= 0:
        raise ResearchValidationError("periods_per_year must be positive")
    if not 0.5 < confidence < 1.0:
        raise ResearchValidationError("confidence must be in (0.5, 1.0)")
    if benchmark_returns and len(benchmark_returns) != len(values):
        raise ResearchValidationError("benchmark returns must match returns length")
    if risk_free_returns and len(risk_free_returns) != len(values):
        raise ResearchValidationError("risk-free returns must match returns length")
    if costs and len(costs) != len(values):
        raise ResearchValidationError("costs must match returns length")
    if turnover and len(turnover) != len(values):
        raise ResearchValidationError("turnover must match returns length")

    cost_values = tuple(float(value) for value in costs) or tuple(0.0 for _ in values)
    if any(not isfinite(value) or value < 0 for value in cost_values):
        raise ResearchValidationError("costs must be finite and non-negative")
    net = tuple(value - cost for value, cost in zip(values, cost_values, strict=True))
    rf = tuple(float(value) for value in risk_free_returns) or tuple(0.0 for _ in net)
    excess = tuple(value - rate for value, rate in zip(net, rf, strict=True))
    wealth = [1.0]
    for value in net:
        wealth.append(wealth[-1] * (1.0 + value))
    total_return = wealth[-1] - 1.0
    annualized = (
        (1.0 + total_return) ** (periods_per_year / len(net)) - 1.0
        if total_return > -1.0
        else -1.0
    )
    annualized_vol = (
        _sample_stdev(net) * sqrt(periods_per_year) if len(net) >= 2 else 0.0
    )
    warnings: list[str] = []
    sharpe: float | None = None
    if len(excess) >= 2:
        stdev = _sample_stdev(excess)
        if stdev > 0:
            sharpe = mean(excess) / stdev * sqrt(periods_per_year)
        else:
            warnings.append("Sharpe ratio is undefined because excess-return volatility is zero")
    else:
        warnings.append("Sharpe ratio requires at least two observations")

    downside = tuple(min(value - target_return, 0.0) for value in excess)
    downside_deviation = sqrt(sum(value * value for value in downside) / len(downside))
    sortino = None
    if downside_deviation > 0:
        sortino = (mean(excess) - target_return) / downside_deviation
        warnings.append(
            "Sortino ratio uses a period excess-mean and a zero-target downside-deviation "
            "convention; do not compare it with a differently annualized implementation"
        )
    else:
        warnings.append("Sortino ratio is undefined because downside deviation is zero")

    mdd = _drawdown(wealth)
    calmar = annualized / mdd if mdd > 0 else None
    if calmar is None:
        warnings.append("Calmar ratio is undefined because maximum drawdown is zero")

    tracking: float | None = None
    if benchmark_returns:
        active = tuple(
            value - benchmark
            for value, benchmark in zip(net, benchmark_returns, strict=True)
        )
        if len(active) >= 2:
            tracking = _sample_stdev(active) * sqrt(periods_per_year)
        else:
            warnings.append("Tracking error requires at least two observations")
    if len(net) < 30:
        warnings.append("Fewer than 30 observations: inferential statistics are unreliable")
    warnings.append(
        "Annualization assumes independent, identically distributed periods; serial "
        "correlation and volatility clustering can invalidate it"
    )
    return PerformanceMetrics(
        periods=len(net),
        total_return=total_return,
        annualized_return=annualized,
        annualized_volatility=annualized_vol,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown=mdd,
        tracking_error=tracking,
        turnover=sum(float(value) for value in turnover),
        total_cost=sum(cost_values),
        net_return=total_return,
        warnings=tuple(warnings),
    )


def _sample_stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = mean(values)
    return sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


@dataclass(frozen=True)
class BootstrapResult:
    metric_name: str
    estimate: float
    lower: float
    upper: float
    confidence: float
    samples: int
    block_size: int
    seed: int
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.metric_name:
            raise ResearchValidationError("metric_name must be non-empty")
        if self.samples < 1:
            raise ResearchValidationError("samples must be positive")
        if self.block_size < 1:
            raise ResearchValidationError("block_size must be positive")


def block_bootstrap_mean_ci(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    samples: int = 1_000,
    block_size: int | None = None,
    seed: int = 0,
) -> BootstrapResult:
    """Deterministic circular block bootstrap for the sample mean.

    This is a research diagnostic, not a claim that the underlying returns are
    stationary. Callers must inspect serial dependence and regime changes.
    """
    data = tuple(float(value) for value in values)
    if not data:
        raise ResearchValidationError("values cannot be empty")
    if any(not isfinite(value) for value in data):
        raise ResearchValidationError("values must be finite")
    if not 0.5 < confidence < 1.0:
        raise ResearchValidationError("confidence must be in (0.5, 1.0)")
    if samples < 1:
        raise ResearchValidationError("samples must be positive")
    if block_size is None:
        block_size = max(1, ceil(len(data) ** (1.0 / 3.0)))
    if block_size < 1:
        raise ResearchValidationError("block_size must be positive")
    rng = Random(seed)
    n = len(data)
    means: list[float] = []
    for _ in range(samples):
        draw: list[float] = []
        while len(draw) < n:
            start = rng.randrange(n)
            for offset in range(block_size):
                draw.append(data[(start + offset) % n])
                if len(draw) == n:
                    break
        means.append(mean(draw))
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    lower = _quantile_sorted(means, alpha)
    upper = _quantile_sorted(means, 1.0 - alpha)
    return BootstrapResult(
        metric_name="mean",
        estimate=mean(data),
        lower=lower,
        upper=upper,
        confidence=confidence,
        samples=samples,
        block_size=block_size,
        seed=seed,
        warnings=(
            (
                "Block bootstrap preserves short-range dependence only; inspect regime "
                "changes and overlapping observations before drawing inferences."
            ),
        ),
    )


def _quantile_sorted(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ResearchValidationError("cannot compute a quantile of an empty sample")
    if probability <= 0:
        return sorted_values[0]
    if probability >= 1:
        return sorted_values[-1]
    position = probability * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def probabilistic_sharpe_ratio(
    sharpe: float,
    *,
    observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Approximate PSR under the stated i.i.d. higher-moment assumptions."""
    if observations < 2:
        raise ResearchValidationError("observations must be at least two")
    if not isfinite(sharpe) or not isfinite(skewness) or not isfinite(kurtosis):
        raise ResearchValidationError("sharpe, skewness, and kurtosis must be finite")
    if kurtosis < 1.0:
        raise ResearchValidationError("kurtosis must be at least one")
    variance = (
        1.0
        - skewness * sharpe
        + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    ) / (observations - 1)
    if variance <= 0:
        raise ResearchValidationError("PSR variance is non-positive; assumptions are invalid")
    return NormalDist().cdf(sharpe / sqrt(variance))


def deflated_sharpe_ratio(
    sharpe: float,
    *,
    observations: int,
    trials: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Approximate Deflated Sharpe Ratio for multiple tried configurations.

    Uses the expected maximum Sharpe under a null of independent trials. This
    is a screening diagnostic, not proof that a strategy is genuine.
    """
    if trials < 1:
        raise ResearchValidationError("trials must be positive")
    if trials == 1:
        return probabilistic_sharpe_ratio(
            sharpe,
            observations=observations,
            skewness=skewness,
            kurtosis=kurtosis,
        )
    log_trials = log(float(trials))
    if log_trials <= 0:
        return probabilistic_sharpe_ratio(
            sharpe,
            observations=observations,
            skewness=skewness,
            kurtosis=kurtosis,
        )
    expected_max = sqrt(2.0 * log_trials) - (
        log(log_trials) + log(4.0 * pi)
    ) / (2.0 * sqrt(2.0 * log_trials))
    return probabilistic_sharpe_ratio(
        sharpe - expected_max,
        observations=observations,
        skewness=skewness,
        kurtosis=kurtosis,
    )


@dataclass(frozen=True)
class ExperimentRecord:
    """One candidate method/configuration evaluated out of sample."""

    experiment_id: str
    method: str
    parameters: Mapping[str, Any]
    train_window: WalkForwardWindow
    test_window: WalkForwardWindow
    metrics: PerformanceMetrics
    holdout_used: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.method:
            raise ResearchValidationError("experiment_id and method must be non-empty")
        if self.test_window.test_start < self.train_window.train_end:
            raise ResearchValidationError("test window overlaps training data")


@dataclass(frozen=True)
class CandidateSelection:
    selected: ExperimentRecord | None
    baseline: ExperimentRecord | None
    candidates: tuple[ExperimentRecord, ...]
    selection_rule: str
    warnings: tuple[str, ...] = ()
    holdout_untouched: bool = True
    multiple_testing_count: int = 0
    deflated_probability: float | None = None


class ExperimentRegistry:
    """Append-only in-memory registry of candidate experiments."""

    def __init__(self) -> None:
        self._records: dict[str, ExperimentRecord] = {}

    @property
    def records(self) -> tuple[ExperimentRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    @property
    def multiple_testing_count(self) -> int:
        return len(self._records)

    def register(self, record: ExperimentRecord) -> ExperimentRecord:
        existing = self._records.get(record.experiment_id)
        if existing is not None:
            if existing != record:
                raise ResearchValidationError(
                    f"experiment {record.experiment_id} already registered with different data"
                )
            return existing
        self._records[record.experiment_id] = record
        return record

    def select_candidate(
        self,
        *,
        baseline_method: str,
        minimum_sharpe_improvement: float = 0.0,
        require_holdout_untouched: bool = True,
    ) -> CandidateSelection:
        """Select a candidate only after an explicit baseline comparison."""
        if not baseline_method:
            raise ResearchValidationError("baseline_method must be non-empty")
        records = self.records
        baselines = [record for record in records if record.method == baseline_method]
        if not baselines:
            raise ResearchValidationError("no baseline experiment has been registered")
        baseline = max(
            baselines,
            key=lambda record: (record.metrics.sharpe or float("-inf"), record.experiment_id),
        )
        candidates = [
            record
            for record in records
            if record.method != baseline_method
            and (not require_holdout_untouched or not record.holdout_used)
        ]
        warnings: list[str] = []
        if any(record.holdout_used for record in records):
            warnings.append("holdout data has already been used; final holdout is not untouched")
        if self.multiple_testing_count > 1:
            warnings.append(
                f"{self.multiple_testing_count} configurations were registered; report all "
                "candidates and apply a multiple-testing adjustment before promotion"
            )
        if not candidates:
            return CandidateSelection(
                selected=None,
                baseline=baseline,
                candidates=(),
                selection_rule="no eligible candidate",
                warnings=tuple(warnings),
                holdout_untouched=not any(record.holdout_used for record in records),
                multiple_testing_count=self.multiple_testing_count,
            )
        best = max(
            candidates,
            key=lambda record: (record.metrics.sharpe or float("-inf"), record.experiment_id),
        )
        baseline_sharpe = baseline.metrics.sharpe
        best_sharpe = best.metrics.sharpe
        selected = None
        if (
            baseline_sharpe is not None
            and best_sharpe is not None
            and best_sharpe >= baseline_sharpe + minimum_sharpe_improvement
        ):
            selected = best
        else:
            warnings.append(
                "no candidate demonstrated the pre-registered Sharpe improvement over baseline"
            )
        deflated = None
        if best_sharpe is not None:
            deflated = deflated_sharpe_ratio(
                best_sharpe / sqrt(252.0),
                observations=best.metrics.periods,
                trials=self.multiple_testing_count,
            )
        return CandidateSelection(
            selected=selected,
            baseline=baseline,
            candidates=tuple(candidates),
            selection_rule=(
                "highest out-of-sample Sharpe among candidates, subject to the "
                "pre-registered improvement threshold and untouched holdout"
            ),
            warnings=tuple(warnings),
            holdout_untouched=not any(record.holdout_used for record in records),
            multiple_testing_count=self.multiple_testing_count,
            deflated_probability=deflated,
        )


__all__ = [
    "BootstrapResult",
    "CandidateSelection",
    "ExperimentRecord",
    "ExperimentRegistry",
    "LookaheadError",
    "PerformanceMetrics",
    "ResearchValidationError",
    "WalkForwardWindow",
    "block_bootstrap_mean_ci",
    "deflated_sharpe_ratio",
    "performance_metrics",
    "point_in_time_available",
    "probabilistic_sharpe_ratio",
    "require_point_in_time",
    "walk_forward_windows",
]
