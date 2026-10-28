"""
P11-RISK: returns, covariance estimation, shrinkage, and numerical validity.

Missing observations are preserved as ``None`` and never filled with zero.
Covariance estimation uses complete cases across the selected instruments and
reports the observation count and coverage. Shrinkage is explicit and versioned;
the default intensity is a transparent small-sample heuristic, not a claim of a
calibrated Ledoit-Wolf estimate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import log, sqrt

from axel.portfolio.linalg import (
    condition_number,
    is_positive_semidefinite,
    is_symmetric,
    mat_vec,
    quadratic_form,
)
from axel.portfolio.types import require_aware, require_finite, require_positive


class ReturnDataError(ValueError):
    """Base class for return/covariance data errors."""


class InsufficientDataError(ReturnDataError):
    """Raised when there are too few aligned observations for an estimate."""


class InvalidCovarianceError(ReturnDataError):
    """Raised when a covariance matrix is malformed or numerically invalid."""


@dataclass(frozen=True)
class PriceSeries:
    instrument_id: str
    timestamps: tuple[datetime, ...]
    prices: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id must be non-empty")
        if len(self.timestamps) != len(self.prices):
            raise ValueError("timestamps and prices must have equal length")
        if not self.timestamps:
            raise ValueError("price series must be non-empty")
        previous: datetime | None = None
        for timestamp, price in zip(self.timestamps, self.prices, strict=True):
            require_aware(timestamp, "timestamp")
            require_positive(price, "price")
            if previous is not None and timestamp <= previous:
                raise ValueError("timestamps must be strictly increasing")
            previous = timestamp


@dataclass(frozen=True)
class ReturnPanel:
    """Asynchronous returns aligned on the union of observation timestamps."""

    instrument_ids: tuple[str, ...]
    timestamps: tuple[datetime, ...]
    returns: Mapping[str, tuple[float | None, ...]]
    coverage: Mapping[str, float]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("instrument ids must be unique")
        for instrument_id in self.instrument_ids:
            values = self.returns.get(instrument_id)
            if values is None or len(values) != len(self.timestamps):
                raise ValueError(f"returns for {instrument_id} are not aligned")
            for value in values:
                if value is not None:
                    require_finite(value, "return")

    @property
    def observation_count(self) -> int:
        return len(self.timestamps)


def simple_returns(series: PriceSeries) -> tuple[tuple[datetime, ...], tuple[float, ...]]:
    timestamps: list[datetime] = []
    values: list[float] = []
    for index in range(1, len(series.prices)):
        timestamps.append(series.timestamps[index])
        values.append(series.prices[index] / series.prices[index - 1] - 1.0)
    return tuple(timestamps), tuple(values)


def log_returns(series: PriceSeries) -> tuple[tuple[datetime, ...], tuple[float, ...]]:
    timestamps: list[datetime] = []
    values: list[float] = []
    for index in range(1, len(series.prices)):
        timestamps.append(series.timestamps[index])
        values.append(log(series.prices[index] / series.prices[index - 1]))
    return tuple(timestamps), tuple(values)


def build_return_panel(
    series: Sequence[PriceSeries],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    min_coverage: float = 0.8,
    kind: str = "simple",
) -> ReturnPanel:
    """Build an asynchronous return panel without filling missing values."""
    if not series:
        raise ReturnDataError("at least one price series is required")
    if kind not in {"simple", "log"}:
        raise ValueError("kind must be 'simple' or 'log'")
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage must be in [0, 1]")

    if start is not None:
        start = require_aware(start, "start")
    if end is not None:
        end = require_aware(end, "end")
    if start is not None and end is not None and end <= start:
        raise ValueError("end must be after start")

    computed: dict[str, dict[datetime, float]] = {}
    instrument_ids: list[str] = []
    all_timestamps: set[datetime] = set()
    for item in series:
        if item.instrument_id in computed:
            raise ReturnDataError(f"duplicate price series for {item.instrument_id}")
        instrument_ids.append(item.instrument_id)
        filtered_timestamps = tuple(
            timestamp
            for timestamp in item.timestamps
            if (start is None or timestamp >= start) and (end is None or timestamp <= end)
        )
        if len(filtered_timestamps) < 2:
            computed[item.instrument_id] = {}
            continue
        filtered = PriceSeries(
            instrument_id=item.instrument_id,
            timestamps=filtered_timestamps,
            prices=tuple(
                price
                for timestamp, price in zip(item.timestamps, item.prices, strict=True)
                if (start is None or timestamp >= start) and (end is None or timestamp <= end)
            ),
        )
        timestamps, values = (
            simple_returns(filtered) if kind == "simple" else log_returns(filtered)
        )
        mapping = dict(zip(timestamps, values, strict=True))
        computed[item.instrument_id] = mapping
        all_timestamps.update(mapping)

    ordered = tuple(sorted(all_timestamps))
    returns: dict[str, tuple[float | None, ...]] = {}
    coverage: dict[str, float] = {}
    warnings: list[str] = []
    for instrument_id in instrument_ids:
        mapping = computed[instrument_id]
        values = tuple(mapping.get(timestamp) for timestamp in ordered)
        present = sum(1 for value in values if value is not None)
        ratio = present / len(ordered) if ordered else 0.0
        returns[instrument_id] = values
        coverage[instrument_id] = ratio
        if ratio < min_coverage:
            warnings.append(
                f"{instrument_id} coverage {ratio:.3f} is below required {min_coverage:.3f}"
            )
    return ReturnPanel(
        instrument_ids=tuple(instrument_ids),
        timestamps=ordered,
        returns=returns,
        coverage=coverage,
        warnings=tuple(warnings),
    )


def complete_case_matrix(
    panel: ReturnPanel,
    instrument_ids: Sequence[str] | None = None,
    *,
    min_observations: int = 2,
) -> tuple[tuple[str, ...], tuple[datetime, ...], list[list[float]]]:
    """Return timestamps where every selected instrument has an observation."""
    selected = tuple(instrument_ids) if instrument_ids is not None else panel.instrument_ids
    if not selected:
        raise InsufficientDataError("no instruments selected")
    for instrument_id in selected:
        if instrument_id not in panel.returns:
            raise ReturnDataError(f"instrument {instrument_id} is not in the panel")
    rows: list[list[float]] = []
    timestamps: list[datetime] = []
    for index, timestamp in enumerate(panel.timestamps):
        row: list[float] = []
        complete = True
        for instrument_id in selected:
            value = panel.returns[instrument_id][index]
            if value is None:
                complete = False
                break
            row.append(value)
        if complete:
            rows.append(row)
            timestamps.append(timestamp)
    if len(rows) < min_observations:
        raise InsufficientDataError(
            f"only {len(rows)} complete observations for {len(selected)} instruments; "
            f"need at least {min_observations}"
        )
    return selected, tuple(timestamps), rows


@dataclass(frozen=True)
class CovarianceResult:
    instrument_ids: tuple[str, ...]
    covariance: tuple[tuple[float, ...], ...]
    observations: int
    method: str
    missing_policy: str
    coverage: Mapping[str, float]
    warnings: tuple[str, ...] = ()

    def as_list(self) -> list[list[float]]:
        return [list(row) for row in self.covariance]


def _sample_covariance(
    rows: Sequence[Sequence[float]], ddof: int
) -> list[list[float]]:
    observations = len(rows)
    width = len(rows[0])
    if observations <= ddof:
        raise InsufficientDataError("not enough observations for the requested ddof")
    means = [
        sum(row[column] for row in rows) / observations for column in range(width)
    ]
    covariance = [[0.0] * width for _ in range(width)]
    for i in range(width):
        for j in range(i, width):
            value = sum(
                (row[i] - means[i]) * (row[j] - means[j]) for row in rows
            ) / (observations - ddof)
            covariance[i][j] = value
            covariance[j][i] = value
    return covariance


def sample_covariance(
    panel: ReturnPanel,
    *,
    instrument_ids: Sequence[str] | None = None,
    ddof: int = 1,
    min_observations: int = 2,
) -> CovarianceResult:
    selected, _, rows = complete_case_matrix(
        panel, instrument_ids, min_observations=max(min_observations, ddof + 1)
    )
    covariance = _sample_covariance(rows, ddof)
    warnings = list(panel.warnings)
    if len(rows) < 2 * len(selected):
        warnings.append("sample size is small relative to the number of instruments")
    return CovarianceResult(
        instrument_ids=selected,
        covariance=tuple(tuple(row) for row in covariance),
        observations=len(rows),
        method="sample_covariance",
        missing_policy="complete_cases",
        coverage={instrument_id: panel.coverage[instrument_id] for instrument_id in selected},
        warnings=tuple(warnings),
    )


def ewma_covariance(
    panel: ReturnPanel,
    *,
    decay: float = 0.94,
    instrument_ids: Sequence[str] | None = None,
    min_observations: int = 2,
) -> CovarianceResult:
    """Finite-sample exponentially weighted covariance.

    Weights are normalized so they sum to one. The most recent observation has
    weight proportional to ``1 - decay`` before normalization.
    """
    if not 0.0 < decay < 1.0:
        raise ValueError("decay must be in (0, 1)")
    selected, _, rows = complete_case_matrix(
        panel, instrument_ids, min_observations=min_observations
    )
    observations = len(rows)
    raw_weights = [decay**index for index in range(observations)]
    total_weight = sum(raw_weights)
    weights = [value / total_weight for value in raw_weights]
    width = len(selected)
    means = [
        sum(weights[index] * rows[index][column] for index in range(observations))
        for column in range(width)
    ]
    covariance = [[0.0] * width for _ in range(width)]
    for i in range(width):
        for j in range(i, width):
            value = sum(
                weights[index]
                * (rows[index][i] - means[i])
                * (rows[index][j] - means[j])
                for index in range(observations)
            )
            covariance[i][j] = value
            covariance[j][i] = value
    warnings: list[str] = list(panel.warnings)
    if observations < 30:
        warnings.append("EWMA estimate uses fewer than 30 observations")
    return CovarianceResult(
        instrument_ids=selected,
        covariance=tuple(tuple(row) for row in covariance),
        observations=observations,
        method=f"ewma_decay_{decay}",
        missing_policy="complete_cases",
        coverage={instrument_id: panel.coverage[instrument_id] for instrument_id in selected},
        warnings=tuple(warnings),
    )


def plugin_shrinkage_intensity(n_assets: int, n_observations: int) -> float:
    """Transparent small-sample shrinkage heuristic.

    This is intentionally not presented as a calibrated Ledoit-Wolf estimate.
    It increases shrinkage as the observation-to-asset ratio falls and is
    bounded away from both zero and one.
    """
    if n_assets < 1 or n_observations < 1:
        return 1.0
    raw = (n_assets + 2.0) / (n_assets + n_observations + 2.0)
    return min(0.9, max(0.05, raw))


@dataclass(frozen=True)
class ShrinkageResult:
    covariance: tuple[tuple[float, ...], ...]
    target: str
    shrinkage: float
    method: str
    observations: int
    warnings: tuple[str, ...] = ()

    def as_list(self) -> list[list[float]]:
        return [list(row) for row in self.covariance]


def _validate_square(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    rows = [list(row) for row in matrix]
    if not rows or not rows[0]:
        raise InvalidCovarianceError("covariance must be non-empty")
    size = len(rows)
    if any(len(row) != size for row in rows):
        raise InvalidCovarianceError("covariance must be square")
    for row in rows:
        for value in row:
            require_finite(value, "covariance")
    return rows


def _constant_correlation_target(sample: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(sample)
    variances = [sample[i][i] for i in range(size)]
    if any(value <= 0 for value in variances):
        raise InvalidCovarianceError("constant-correlation target requires positive variances")
    correlations: list[float] = []
    for i in range(size):
        for j in range(i + 1, size):
            correlations.append(sample[i][j] / sqrt(variances[i] * variances[j]))
    average = sum(correlations) / len(correlations) if correlations else 0.0
    average = min(1.0, max(-1.0, average))
    target = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            target[i][j] = (
                variances[i]
                if i == j
                else average * sqrt(variances[i] * variances[j])
            )
    return target


def _diagonal_target(sample: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(sample)
    return [
        [sample[i][i] if i == j else 0.0 for j in range(size)] for i in range(size)
    ]


def _single_factor_target(sample: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(sample)
    market_variance = sum(
        sample[i][j] for i in range(size) for j in range(size)
    ) / (size * size)
    if market_variance <= 0:
        raise InvalidCovarianceError("single-factor target requires positive market variance")
    betas = [sum(sample[i]) / size / market_variance for i in range(size)]
    target = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            target[i][j] = betas[i] * betas[j] * market_variance
        residual = sample[i][i] - betas[i] * betas[i] * market_variance
        target[i][i] += max(residual, 0.0)
    return target


def shrink_covariance(
    sample_covariance: Sequence[Sequence[float]],
    *,
    target: str = "constant_correlation",
    shrinkage: float | None = None,
    observations: int | None = None,
) -> ShrinkageResult:
    """Shrink a sample covariance toward a structured, documented target."""
    sample = _validate_square(sample_covariance)
    if target not in {"diagonal", "constant_correlation", "single_factor"}:
        raise ValueError("unsupported shrinkage target")
    if not is_symmetric(sample):
        raise InvalidCovarianceError("sample covariance must be symmetric")
    size = len(sample)
    if target == "diagonal":
        structured = _diagonal_target(sample)
    elif target == "constant_correlation":
        structured = _constant_correlation_target(sample)
    else:
        structured = _single_factor_target(sample)
    if shrinkage is None:
        if observations is None:
            raise ValueError("observations are required when shrinkage is not supplied")
        intensity = plugin_shrinkage_intensity(size, observations)
    else:
        require_finite(shrinkage, "shrinkage")
        if not 0.0 <= shrinkage <= 1.0:
            raise ValueError("shrinkage must be in [0, 1]")
        intensity = shrinkage
    combined = [
        [
            intensity * structured[i][j] + (1.0 - intensity) * sample[i][j]
            for j in range(size)
        ]
        for i in range(size)
    ]
    warnings: list[str] = []
    if intensity >= 0.9:
        warnings.append("shrinkage intensity is high; estimates may be dominated by the target")
    return ShrinkageResult(
        covariance=tuple(tuple(row) for row in combined),
        target=target,
        shrinkage=intensity,
        method=f"shrink_{target}",
        observations=observations or 0,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class CovarianceDiagnostics:
    symmetric: bool
    positive_semidefinite: bool
    min_eigenvalue: float
    max_eigenvalue: float
    condition_number: float
    has_non_finite: bool
    warnings: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return self.symmetric and self.positive_semidefinite and not self.has_non_finite


def covariance_diagnostics(
    covariance: Sequence[Sequence[float]],
    *,
    eigenvalue_tolerance: float = 1e-8,
) -> CovarianceDiagnostics:
    try:
        matrix = _validate_square(covariance)
    except InvalidCovarianceError:
        return CovarianceDiagnostics(
            symmetric=False,
            positive_semidefinite=False,
            min_eigenvalue=float("nan"),
            max_eigenvalue=float("nan"),
            condition_number=float("nan"),
            has_non_finite=True,
            warnings=("covariance is malformed",),
        )
    symmetric = is_symmetric(matrix)
    psd = symmetric and is_positive_semidefinite(matrix, tolerance=eigenvalue_tolerance)
    from axel.portfolio.linalg import jacobi_eigenvalues

    eigenvalues = jacobi_eigenvalues(matrix) if symmetric else []
    smallest = eigenvalues[0] if eigenvalues else float("nan")
    largest = eigenvalues[-1] if eigenvalues else float("nan")
    warnings: list[str] = []
    if not symmetric:
        warnings.append("covariance is not symmetric")
    if not psd:
        warnings.append("covariance is not positive semidefinite within tolerance")
    if eigenvalues and smallest <= 0:
        warnings.append("covariance is singular or near-singular")
    return CovarianceDiagnostics(
        symmetric=symmetric,
        positive_semidefinite=psd,
        min_eigenvalue=smallest,
        max_eigenvalue=largest,
        condition_number=condition_number(matrix) if symmetric else float("nan"),
        has_non_finite=False,
        warnings=tuple(warnings),
    )


def require_valid_covariance(
    covariance: Sequence[Sequence[float]],
    *,
    eigenvalue_tolerance: float = 1e-8,
) -> CovarianceDiagnostics:
    diagnostics = covariance_diagnostics(
        covariance, eigenvalue_tolerance=eigenvalue_tolerance
    )
    if not diagnostics.is_valid:
        raise InvalidCovarianceError("; ".join(diagnostics.warnings) or "invalid covariance")
    return diagnostics


def correlation_matrix(covariance: Sequence[Sequence[float]]) -> list[list[float]]:
    matrix = _validate_square(covariance)
    size = len(matrix)
    variances = [matrix[i][i] for i in range(size)]
    if any(value <= 0 for value in variances):
        raise InvalidCovarianceError("correlation is undefined with zero variance")
    result = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            result[i][j] = matrix[i][j] / sqrt(variances[i] * variances[j])
    return result


def annualize_covariance(
    covariance: Sequence[Sequence[float]], periods_per_year: float
) -> list[list[float]]:
    require_positive(periods_per_year, "periods_per_year")
    matrix = _validate_square(covariance)
    return [[value * periods_per_year for value in row] for row in matrix]


def portfolio_variance(
    weights: Sequence[float], covariance: Sequence[Sequence[float]]
) -> float:
    return quadratic_form(weights, covariance)


def portfolio_volatility(
    weights: Sequence[float], covariance: Sequence[Sequence[float]]
) -> float:
    variance = portfolio_variance(weights, covariance)
    if variance < 0 and variance > -1e-12:
        variance = 0.0
    if variance < 0:
        raise InvalidCovarianceError("portfolio variance is negative")
    return sqrt(variance)


def marginal_volatility(
    weights: Sequence[float], covariance: Sequence[Sequence[float]]
) -> list[float]:
    product = mat_vec(covariance, weights)
    volatility = portfolio_volatility(weights, covariance)
    if volatility <= 0:
        return [0.0 for _ in weights]
    return [value / volatility for value in product]


def tracking_error(
    active_returns: Sequence[float], *, periods_per_year: float = 252.0
) -> float:
    if len(active_returns) < 2:
        return 0.0
    average = sum(active_returns) / len(active_returns)
    variance = sum((value - average) ** 2 for value in active_returns) / (
        len(active_returns) - 1
    )
    return sqrt(variance) * sqrt(periods_per_year)


__all__ = [
    "CovarianceDiagnostics",
    "CovarianceResult",
    "InsufficientDataError",
    "InvalidCovarianceError",
    "PriceSeries",
    "ReturnDataError",
    "ReturnPanel",
    "ShrinkageResult",
    "annualize_covariance",
    "build_return_panel",
    "complete_case_matrix",
    "correlation_matrix",
    "covariance_diagnostics",
    "ewma_covariance",
    "log_returns",
    "marginal_volatility",
    "plugin_shrinkage_intensity",
    "portfolio_variance",
    "portfolio_volatility",
    "require_valid_covariance",
    "sample_covariance",
    "shrink_covariance",
    "simple_returns",
    "tracking_error",
]
