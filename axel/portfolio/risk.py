"""
P11-RISK: portfolio risk measures and risk contributions.

No single metric is sufficient. Volatility, marginal/component risk
contributions, historical VaR, expected shortfall, drawdown, concentration, and
liquidity are reported together with their assumptions and warnings.

The component-contribution identity ``sum(CRC) == portfolio volatility`` is
tested explicitly. Signed contributions may be negative for hedges; they are
never forced positive.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from statistics import NormalDist

from axel.portfolio.linalg import mat_vec
from axel.portfolio.returns import (
    InvalidCovarianceError,
    covariance_diagnostics,
    portfolio_volatility,
)
from axel.portfolio.types import RiskSummary, require_finite, require_positive
from axel.util.stats import quantile


class RiskCalculationError(ValueError):
    """Raised when a risk measure cannot be computed from valid inputs."""


@dataclass(frozen=True)
class RiskContribution:
    instrument_id: str
    weight: float
    marginal_contribution: float
    component_contribution: float
    percent_contribution: float


@dataclass(frozen=True)
class RiskReport:
    volatility: float
    variance: float
    var_95: float
    var_99: float
    expected_shortfall_95: float
    expected_shortfall_99: float
    max_drawdown: float
    hhi_instrument: float
    hhi_section: float
    gross_exposure: float
    net_exposure: float
    max_loss: float | None
    margin_requirement: float | None
    liquidity_horizon_days: float | None
    contributions: tuple[RiskContribution, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_summary(self) -> RiskSummary:
        return RiskSummary(
            volatility=self.volatility,
            var_95=self.var_95,
            var_99=self.var_99,
            expected_shortfall_95=self.expected_shortfall_95,
            expected_shortfall_99=self.expected_shortfall_99,
            max_drawdown=self.max_drawdown,
            hhi_instrument=self.hhi_instrument,
            hhi_section=self.hhi_section,
            gross_exposure=self.gross_exposure,
            net_exposure=self.net_exposure,
            max_loss=self.max_loss,
            margin_requirement=self.margin_requirement,
            liquidity_horizon_days=self.liquidity_horizon_days,
        )


def marginal_risk_contributions(
    weights: Sequence[float], covariance: Sequence[Sequence[float]]
) -> tuple[float, ...]:
    """``MRC_i = (Sigma w)_i / sigma_p``. Returns zeros when sigma_p is zero."""
    if not weights:
        raise RiskCalculationError("weights must be non-empty")
    if len(weights) != len(covariance):
        raise RiskCalculationError("weights and covariance dimensions differ")
    volatility = portfolio_volatility(weights, covariance)
    if volatility <= 0:
        return tuple(0.0 for _ in weights)
    return tuple(value / volatility for value in mat_vec(covariance, weights))


def component_risk_contributions(
    weights: Sequence[float], covariance: Sequence[Sequence[float]]
) -> tuple[float, ...]:
    """``CRC_i = w_i * MRC_i``. The sum equals portfolio volatility."""
    marginal = marginal_risk_contributions(weights, covariance)
    return tuple(weight * value for weight, value in zip(weights, marginal, strict=True))


def risk_contribution_percentages(
    weights: Sequence[float], covariance: Sequence[Sequence[float]]
) -> tuple[float, ...]:
    volatility = portfolio_volatility(weights, covariance)
    if volatility <= 0:
        return tuple(0.0 for _ in weights)
    return tuple(value / volatility for value in component_risk_contributions(weights, covariance))


def verify_risk_contribution_invariant(
    weights: Sequence[float],
    covariance: Sequence[Sequence[float]],
    *,
    tolerance: float = 1e-8,
) -> bool:
    volatility = portfolio_volatility(weights, covariance)
    total = sum(component_risk_contributions(weights, covariance))
    return abs(total - volatility) <= max(tolerance, tolerance * abs(volatility))


def historical_var(losses: Sequence[float], confidence: float = 0.95) -> float:
    """Historical loss quantile. ``losses`` are positive for a loss."""
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1.0)")
    if not losses:
        return 0.0
    for value in losses:
        require_finite(value, "loss")
    return max(0.0, quantile(losses, confidence))


def expected_shortfall(losses: Sequence[float], confidence: float = 0.95) -> float:
    """Empirical expected shortfall using the worst-tail average.

    Uses ``ceil((1 - confidence) * n)`` worst observations. This is a documented
    discrete-tail convention, not a claim that the loss distribution is
    continuous.
    """
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1.0)")
    if not losses:
        return 0.0
    for value in losses:
        require_finite(value, "loss")
    ordered = sorted(losses, reverse=True)
    tail_count = max(1, ceil((1.0 - confidence) * len(ordered)))
    tail = ordered[:tail_count]
    return max(0.0, sum(tail) / len(tail))


def scenario_cvar(
    losses: Sequence[float],
    probabilities: Sequence[float],
    confidence: float = 0.95,
) -> float:
    """Quantile-consistent CVaR for a discrete scenario distribution."""
    if len(losses) != len(probabilities) or not losses:
        raise ValueError("losses and probabilities must be non-empty and equal length")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1.0)")
    total_probability = sum(probabilities)
    if abs(total_probability - 1.0) > 1e-6:
        raise ValueError("probabilities must sum to 1")
    if any(value < 0 for value in probabilities):
        raise ValueError("probabilities must be non-negative")
    pairs = sorted(zip(losses, probabilities, strict=True), key=lambda pair: pair[0])
    alpha = 1.0 - confidence
    cumulative = 0.0
    for index, (loss, probability) in enumerate(pairs):
        cumulative += probability
        if cumulative >= confidence:
            tail = sum(
                tail_probability * tail_loss
                for tail_loss, tail_probability in pairs[index + 1 :]
            )
            mass_at_var = cumulative - confidence
            return max(0.0, (tail + mass_at_var * loss) / alpha)
    return max(0.0, pairs[-1][0])


def max_drawdown(values: Sequence[float]) -> float:
    """Maximum drawdown as a positive magnitude; zero for a flat series."""
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        require_finite(value, "portfolio value")
        peak = max(peak, value)
        if peak > 0:
            drawdown = value / peak - 1.0
            worst = min(worst, drawdown)
    return max(0.0, -worst)


def drawdown_series(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        return ()
    peak = values[0]
    result: list[float] = []
    for value in values:
        peak = max(peak, value)
        result.append(value / peak - 1.0 if peak > 0 else 0.0)
    return tuple(result)


def hhi(shares: Sequence[float]) -> float:
    """Herfindahl-Hirschman index over non-negative exposure shares."""
    if not shares:
        return 0.0
    for value in shares:
        require_finite(value, "share")
        if value < 0:
            raise ValueError("HHI shares must be non-negative")
    total = sum(shares)
    if total <= 0:
        return 0.0
    return sum((value / total) ** 2 for value in shares)


def liquidity_horizon_days(quantity: float, safe_execution_rate: float) -> float:
    """Planning estimate ``|Q| / q_safe`` in days; not an execution guarantee."""
    require_positive(safe_execution_rate, "safe_execution_rate")
    return abs(quantity) / safe_execution_rate


def normal_var(
    volatility: float,
    portfolio_value: float,
    confidence: float = 0.95,
) -> float:
    """Zero-mean normal VaR approximation, as a positive loss amount."""
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1.0)")
    require_finite(volatility, "volatility")
    require_finite(portfolio_value, "portfolio_value")
    if volatility < 0 or portfolio_value < 0:
        raise ValueError("volatility and portfolio value must be non-negative")
    z_score = NormalDist().inv_cdf(confidence)
    return z_score * volatility * portfolio_value


def _hhi_sections(section_gross: Mapping[str, float]) -> float:
    return hhi(tuple(max(0.0, value) for value in section_gross.values()))


def aggregate_risk_report(
    *,
    instrument_ids: Sequence[str],
    weights: Sequence[float],
    covariance: Sequence[Sequence[float]],
    portfolio_returns: Sequence[float] = (),
    portfolio_values: Sequence[float] = (),
    section_gross_exposure: Mapping[str, float] | None = None,
    gross_exposure: float = 0.0,
    net_exposure: float = 0.0,
    max_loss: float | None = None,
    margin_requirement: float | None = None,
    liquidity_horizon_days: float | None = None,
) -> RiskReport:
    """Assemble a complete risk report with explicit warnings."""
    if len(instrument_ids) != len(weights):
        raise RiskCalculationError("instrument ids and weights must have equal length")
    diagnostics = covariance_diagnostics(covariance)
    warnings: list[str] = list(diagnostics.warnings)
    if not diagnostics.is_valid:
        raise InvalidCovarianceError("; ".join(diagnostics.warnings))
    volatility = portfolio_volatility(weights, covariance)
    variance = volatility * volatility
    losses = [-value for value in portfolio_returns]
    var_95 = historical_var(losses, 0.95)
    var_99 = historical_var(losses, 0.99)
    es_95 = expected_shortfall(losses, 0.95)
    es_99 = expected_shortfall(losses, 0.99)
    if len(portfolio_returns) < 30:
        warnings.append("fewer than 30 return observations; VaR/ES estimates are unstable")
    marginal = marginal_risk_contributions(weights, covariance)
    component = component_risk_contributions(weights, covariance)
    percentages = risk_contribution_percentages(weights, covariance)
    contributions = tuple(
        RiskContribution(
            instrument_id=instrument_ids[index],
            weight=weights[index],
            marginal_contribution=marginal[index],
            component_contribution=component[index],
            percent_contribution=percentages[index],
        )
        for index in range(len(instrument_ids))
    )
    if abs(sum(component) - volatility) > max(1e-8, abs(volatility) * 1e-8):
        warnings.append("component risk contributions do not reconcile to volatility")
    if max_loss is None:
        warnings.append("portfolio maximum loss is undefined; do not treat as zero")
    if margin_requirement is None:
        warnings.append("portfolio margin requirement is unknown")
    if liquidity_horizon_days is None:
        warnings.append("portfolio liquidity horizon is unknown")
    instrument_hhi = hhi(tuple(abs(value) for value in weights))
    section_hhi = _hhi_sections(section_gross_exposure or {})
    return RiskReport(
        volatility=volatility,
        variance=variance,
        var_95=var_95,
        var_99=var_99,
        expected_shortfall_95=es_95,
        expected_shortfall_99=es_99,
        max_drawdown=max_drawdown(portfolio_values),
        hhi_instrument=instrument_hhi,
        hhi_section=section_hhi,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
        max_loss=max_loss,
        margin_requirement=margin_requirement,
        liquidity_horizon_days=liquidity_horizon_days,
        contributions=contributions,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def risk_summary_from_exposure(
    *,
    gross_exposure: float,
    net_exposure: float,
    max_loss: float | None,
    margin_requirement: float | None,
    liquidity_horizon_days: float | None,
    hhi_instrument: float = 0.0,
    hhi_section: float = 0.0,
) -> RiskSummary:
    """Risk summary when no return history is available.

    Volatility and tail measures remain zero only because they were not
    estimated; the caller must attach the warning that they are unavailable.
    """
    return RiskSummary(
        volatility=0.0,
        var_95=0.0,
        var_99=0.0,
        expected_shortfall_95=0.0,
        expected_shortfall_99=0.0,
        max_drawdown=0.0,
        hhi_instrument=hhi_instrument,
        hhi_section=hhi_section,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
        max_loss=max_loss,
        margin_requirement=margin_requirement,
        liquidity_horizon_days=liquidity_horizon_days,
    )


__all__ = [
    "RiskCalculationError",
    "RiskContribution",
    "RiskReport",
    "aggregate_risk_report",
    "component_risk_contributions",
    "drawdown_series",
    "expected_shortfall",
    "hhi",
    "historical_var",
    "liquidity_horizon_days",
    "marginal_risk_contributions",
    "max_drawdown",
    "normal_var",
    "risk_contribution_percentages",
    "risk_summary_from_exposure",
    "scenario_cvar",
    "verify_risk_contribution_invariant",
]
