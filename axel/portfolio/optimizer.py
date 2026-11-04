"""
P11-OPTIMIZER: constrained candidate allocation generation.

The optimizer returns a typed ``AllocationResult``. Failure is never encoded as
an empty allocation that could be mistaken for a valid zero-risk target. Hard
constraints are checked independently after optimization and a solver success
status is not authorization.

Baselines are deliberately transparent: no rebalance, equal weight (only for
comparable linear assets), inverse volatility, minimum variance, risk parity,
constrained mean variance, and an approximate scenario-CVaR method. A more
complex method must beat these baselines before becoming a default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import sqrt

from axel.core.types import Section
from axel.portfolio.constraints import (
    evaluate_allocation_constraints,
    failed_hard_constraints,
    hard_constraints_pass,
)
from axel.portfolio.cost import turnover
from axel.portfolio.linalg import (
    QPStatus,
    dot,
    mat_vec,
    minimize_qp,
    project_simplex,
    quadratic_form,
)
from axel.portfolio.returns import covariance_diagnostics
from axel.portfolio.risk import (
    RiskReport,
    expected_shortfall,
    scenario_cvar,
)
from axel.portfolio.serialization import fingerprint
from axel.portfolio.types import (
    AllocationResult,
    AssetClass,
    BaselineComparison,
    ConstraintResult,
    CostSummary,
    ExposureSnapshot,
    OptimizationConfig,
    OptimizationMethod,
    PortfolioSnapshot,
    RiskBudgetPolicy,
    RiskBudgetProposal,
    SolverStatus,
    StabilityResult,
    require_aware,
    require_finite,
    utc_now,
)

OPTIMIZER_VERSION = "p11-optimizer-1"


def _weight_exposure(exposure) -> float:  # type: ignore[no-untyped-def]
    """Exposure basis used for weights.

    Options use delta-equivalent exposure; predictions use maximum loss;
    FX uses the base-currency leg; linear instruments use signed market value.
    """
    if exposure.asset_class == AssetClass.OPTION and exposure.delta_equivalent is not None:
        return exposure.delta_equivalent
    if exposure.asset_class == AssetClass.PREDICTION and exposure.max_loss is not None:
        return exposure.max_loss
    if exposure.asset_class == AssetClass.FOREX and exposure.delta_equivalent is not None:
        return exposure.delta_equivalent
    return exposure.market_value


def exposure_by_instrument(exposure: ExposureSnapshot) -> Mapping[str, float]:
    totals: dict[str, float] = {}
    for item in exposure.instrument_exposures:
        totals[item.instrument_id] = totals.get(item.instrument_id, 0.0) + _weight_exposure(item)
    return totals


def _current_weights(
    exposure: ExposureSnapshot,
    instrument_ids: Sequence[str],
    equity: float,
) -> tuple[float, ...]:
    totals = exposure_by_instrument(exposure)
    return tuple(totals.get(instrument_id, 0.0) / equity for instrument_id in instrument_ids)


def _risk_budget_weights(
    instrument_ids: Sequence[str],
    section_by_instrument: Mapping[str, Section],
    budget: RiskBudgetProposal,
) -> tuple[float, ...]:
    raw: list[float] = []
    for instrument_id in instrument_ids:
        section = section_by_instrument[instrument_id]
        section_budget = budget.budget_for(section)
        raw.append(section_budget.risk_budget if section_budget is not None else 0.0)
    total = sum(raw)
    if total <= 0:
        return tuple(1.0 / len(instrument_ids) for _ in instrument_ids)
    return tuple(value / total for value in raw)


def _solve_min_variance(
    covariance: Sequence[Sequence[float]],
    config: OptimizationConfig,
    target_sum: float,
    initial: Sequence[float],
) -> tuple[SolverStatus, tuple[float, ...], str, int]:
    result = minimize_qp(
        covariance,
        [0.0] * len(covariance),
        lower=config.lower_bound if config.long_only else -1.0,
        upper=config.upper_bound,
        target_sum=target_sum,
        max_iterations=config.max_iterations,
        tolerance=config.tolerance,
        initial=initial,
    )
    status = _map_qp_status(result.status)
    return status, result.x, result.message, result.iterations


def _solve_mean_variance(
    covariance: Sequence[Sequence[float]],
    expected_returns: Sequence[float],
    config: OptimizationConfig,
    target_sum: float,
    initial: Sequence[float],
    current_weights: Sequence[float],
) -> tuple[SolverStatus, tuple[float, ...], str, int]:
    if config.risk_aversion <= 0:
        return SolverStatus.INVALID_INPUT, tuple(initial), "risk_aversion must be positive", 0
    size = len(expected_returns)
    p_matrix = [
        [config.risk_aversion * covariance[i][j] for j in range(size)]
        for i in range(size)
    ]
    q_vector = [-expected_returns[i] for i in range(size)]
    if config.cost_penalty > 0:
        # Quadratic surrogate for the absolute turnover penalty. The proposal
        # layer still reports the true one-way turnover and costs.
        for i in range(size):
            p_matrix[i][i] += 2.0 * config.cost_penalty
            q_vector[i] -= 2.0 * config.cost_penalty * current_weights[i]
    result = minimize_qp(
        p_matrix,
        q_vector,
        lower=config.lower_bound if config.long_only else -1.0,
        upper=config.upper_bound,
        target_sum=target_sum,
        max_iterations=config.max_iterations,
        tolerance=config.tolerance,
        initial=initial,
    )
    status = _map_qp_status(result.status)
    return status, result.x, result.message, result.iterations


def _map_qp_status(status: QPStatus) -> SolverStatus:
    if status == QPStatus.OPTIMAL:
        return SolverStatus.OPTIMAL
    if status in {QPStatus.APPROXIMATE, QPStatus.MAX_ITERATIONS}:
        return SolverStatus.APPROXIMATE
    if status == QPStatus.INFEASIBLE:
        return SolverStatus.INFEASIBLE
    return SolverStatus.INVALID_INPUT


def _risk_parity_weights(
    covariance: Sequence[Sequence[float]],
    risk_budget_weights: Sequence[float],
    config: OptimizationConfig,
) -> tuple[SolverStatus, tuple[float, ...], str, int]:
    size = len(covariance)
    variances = [covariance[i][i] for i in range(size)]
    if any(value <= 0 for value in variances):
        return SolverStatus.INVALID_INPUT, (), "risk parity requires positive variances", 0
    budgets = list(risk_budget_weights)
    if len(budgets) != size or sum(budgets) <= 0:
        budgets = [1.0 / size] * size
    else:
        total = sum(budgets)
        budgets = [value / total for value in budgets]
    weights = [1.0 / sqrt(value) for value in variances]
    total_weight = sum(weights)
    weights = [value / total_weight for value in weights]
    damping = 0.5
    for iteration in range(1, config.max_iterations + 1):
        sigma_w = mat_vec(covariance, weights)
        if any(value <= 0 for value in sigma_w):
            return SolverStatus.INVALID_INPUT, tuple(weights), "non-positive marginal risk", iteration
        target = [
            budgets[index] / sigma_w[index] if budgets[index] > 0 else 0.0
            for index in range(size)
        ]
        target_total = sum(target)
        if target_total <= 0:
            return SolverStatus.INVALID_INPUT, tuple(weights), "empty risk-budget target", iteration
        target = [value / target_total for value in target]
        candidate = [
            (1.0 - damping) * weights[index] + damping * target[index]
            for index in range(size)
        ]
        candidate = project_simplex(candidate)
        distance = max(
            abs(candidate[index] - weights[index]) for index in range(size)
        )
        weights = candidate
        if distance <= config.tolerance:
            return SolverStatus.OPTIMAL, tuple(weights), "converged", iteration
    return (
        SolverStatus.APPROXIMATE,
        tuple(weights),
        "risk-parity iteration limit reached",
        config.max_iterations,
    )


def _cvar_weights(
    scenario_returns: Sequence[Sequence[float]] | None,
    scenario_probabilities: Sequence[float] | None,
    config: OptimizationConfig,
    initial: Sequence[float],
) -> tuple[SolverStatus, tuple[float, ...], str, int]:
    if scenario_returns is None or scenario_probabilities is None:
        return SolverStatus.INVALID_INPUT, (), "scenario returns are required for CVaR", 0
    if not scenario_returns or len(scenario_returns) != len(scenario_probabilities):
        return SolverStatus.INVALID_INPUT, (), "scenario dimensions are invalid", 0
    probabilities = list(scenario_probabilities)
    if abs(sum(probabilities) - 1.0) > 1e-6 or any(value < 0 for value in probabilities):
        return SolverStatus.INVALID_INPUT, (), "scenario probabilities must sum to 1", 0
    size = len(initial)
    if any(len(row) != size for row in scenario_returns):
        return SolverStatus.INVALID_INPUT, (), "scenario return width is invalid", 0

    weights = project_simplex(initial)
    step = 0.1
    best = scenario_cvar(
        [-dot(row, weights) for row in scenario_returns], probabilities, config.confidence
    )
    for iteration in range(1, config.max_iterations + 1):
        losses = [-dot(row, weights) for row in scenario_returns]
        var = expected_shortfall(losses, config.confidence)
        tail = [
            (row, probability)
            for row, probability, loss in zip(
                scenario_returns, probabilities, losses, strict=True
            )
            if loss >= var
        ]
        if not tail:
            return SolverStatus.APPROXIMATE, tuple(weights), "empty CVaR tail", iteration
        tail_weight = sum(probability for _, probability in tail)
        if tail_weight <= 0:
            return SolverStatus.APPROXIMATE, tuple(weights), "zero CVaR tail weight", iteration
        gradient = [
            -sum(row[index] * probability for row, probability in tail) / tail_weight
            for index in range(size)
        ]
        candidate = project_simplex(
            [weights[index] - step * gradient[index] for index in range(size)]
        )
        candidate_losses = [-dot(row, candidate) for row in scenario_returns]
        candidate_value = scenario_cvar(candidate_losses, probabilities, config.confidence)
        if candidate_value < best - 1e-12:
            weights = candidate
            best = candidate_value
            step = min(1.0, step * 1.2)
        else:
            step *= 0.5
        if step < 1e-10:
            return SolverStatus.APPROXIMATE, tuple(weights), "CVaR step size collapsed", iteration
    return SolverStatus.APPROXIMATE, tuple(weights), "CVaR iteration limit reached", config.max_iterations


def _raw_weights(
    config: OptimizationConfig,
    *,
    covariance: Sequence[Sequence[float]],
    current_weights: Sequence[float],
    expected_returns: Sequence[float] | None,
    risk_budget_weights: Sequence[float],
    scenario_returns: Sequence[Sequence[float]] | None,
    scenario_probabilities: Sequence[float] | None,
) -> tuple[SolverStatus, tuple[float, ...], str, int]:
    size = len(current_weights)
    target_sum = 1.0 if config.fully_invested else max(0.0, sum(current_weights))
    method = config.method
    if method == OptimizationMethod.NO_REBALANCE:
        return SolverStatus.OPTIMAL, tuple(current_weights), "no rebalance requested", 0
    if method == OptimizationMethod.EQUAL_WEIGHT:
        return SolverStatus.OPTIMAL, tuple(1.0 / size for _ in range(size)), "equal weight", 0
    if method == OptimizationMethod.INVERSE_VOLATILITY:
        variances = [covariance[i][i] for i in range(size)]
        if any(value <= 0 for value in variances):
            return SolverStatus.INVALID_INPUT, (), "inverse volatility requires positive variances", 0
        raw = [1.0 / sqrt(value) for value in variances]
        total = sum(raw)
        return SolverStatus.OPTIMAL, tuple(value / total for value in raw), "inverse volatility", 0
    if method == OptimizationMethod.MINIMUM_VARIANCE:
        return _solve_min_variance(covariance, config, target_sum, current_weights)
    if method == OptimizationMethod.MEAN_VARIANCE:
        if expected_returns is None:
            return SolverStatus.INVALID_INPUT, (), "expected returns are required", 0
        return _solve_mean_variance(
            covariance,
            expected_returns,
            config,
            target_sum,
            current_weights,
            current_weights,
        )
    if method == OptimizationMethod.RISK_PARITY:
        return _risk_parity_weights(covariance, risk_budget_weights, config)
    if method == OptimizationMethod.CVAR:
        return _cvar_weights(
            scenario_returns, scenario_probabilities, config, current_weights
        )
    return SolverStatus.INVALID_INPUT, (), f"unsupported method {method}", 0


def _minimal_constraints(
    instrument_ids: Sequence[str],
    weights: Sequence[float],
    covariance: Sequence[Sequence[float]],
    config: OptimizationConfig,
) -> tuple[ConstraintResult, ...]:
    from axel.portfolio.types import ConstraintSeverity

    total = sum(weights)
    minimum = min(weights)
    diagnostics = covariance_diagnostics(covariance)
    return (
        ConstraintResult(
            name="fully_invested",
            severity=ConstraintSeverity.HARD,
            passed=abs(total - 1.0) <= 1e-6 if config.fully_invested else True,
            actual=total,
            limit=1.0,
            unit="weight",
        ),
        ConstraintResult(
            name="long_only",
            severity=ConstraintSeverity.HARD,
            passed=minimum >= -1e-9 if config.long_only else True,
            actual=minimum,
            limit=0.0,
            unit="weight",
        ),
        ConstraintResult(
            name="covariance_validity",
            severity=ConstraintSeverity.HARD,
            passed=diagnostics.is_valid,
            actual=None,
            limit=None,
            unit="boolean",
            message="; ".join(diagnostics.warnings),
        ),
    )


def _expected_return(
    weights: Sequence[float], expected_returns: Sequence[float] | None
) -> float | None:
    if expected_returns is None:
        return None
    return dot(weights, expected_returns)


def _expected_volatility(
    weights: Sequence[float], covariance: Sequence[Sequence[float]]
) -> float | None:
    variance = quadratic_form(weights, covariance)
    if variance < 0 and variance > -1e-12:
        variance = 0.0
    if variance < 0:
        return None
    return sqrt(variance)


def generate_allocation(
    *,
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    budget: RiskBudgetProposal,
    config: OptimizationConfig,
    covariance: Sequence[Sequence[float]],
    instrument_ids: Sequence[str],
    section_by_instrument: Mapping[str, Section],
    expected_returns: Sequence[float] | None = None,
    current_weights: Sequence[float] | None = None,
    policy: RiskBudgetPolicy | None = None,
    risk_report: RiskReport | None = None,
    cost_summary: CostSummary | None = None,
    stress_passed: bool | None = None,
    eligible_instruments: Sequence[str] | None = None,
    factor_exposures: Mapping[str, float] | None = None,
    factor_caps: Mapping[str, float] | None = None,
    liquidity_horizon_by_instrument: Mapping[str, float] | None = None,
    available_cash: float | None = None,
    scenario_returns: Sequence[Sequence[float]] | None = None,
    scenario_probabilities: Sequence[float] | None = None,
    now: datetime | None = None,
) -> AllocationResult:
    """Generate one candidate allocation with explicit success/failure status."""
    current_time = require_aware(now or utc_now(), "now")
    ids = tuple(instrument_ids)
    warnings: list[str] = []
    if not ids or len(set(ids)) != len(ids):
        return _failure(
            SolverStatus.INVALID_INPUT,
            config,
            ids,
            (),
            "instrument ids must be non-empty and unique",
        )
    if len(covariance) != len(ids) or any(len(row) != len(ids) for row in covariance):
        return _failure(
            SolverStatus.INVALID_INPUT,
            config,
            ids,
            (),
            "covariance dimensions do not match the instrument universe",
        )
    if exposure.portfolio_snapshot_id != snapshot.snapshot_id:
        return _failure(
            SolverStatus.INVALID_INPUT, config, ids, (), "exposure does not match snapshot"
        )
    if budget.portfolio_snapshot_id != snapshot.snapshot_id:
        return _failure(
            SolverStatus.INVALID_INPUT, config, ids, (), "budget does not match snapshot"
        )
    if not snapshot.is_usable_for_risk_increase:
        return _failure(
            SolverStatus.STALE_DATA,
            config,
            ids,
            (),
            "snapshot is not usable for risk-increasing allocation",
        )
    if not exposure.is_complete:
        return _failure(
            SolverStatus.MISSING_EXPOSURE,
            config,
            ids,
            (),
            "exposure snapshot is incomplete",
        )
    if not budget.is_valid:
        return _failure(
            SolverStatus.INVALID_INPUT, config, ids, (), "risk budget is not valid"
        )
    if any(instrument_id not in section_by_instrument for instrument_id in ids):
        return _failure(
            SolverStatus.INVALID_INPUT,
            config,
            ids,
            (),
            "section mapping is missing an instrument",
        )
    if config.method == OptimizationMethod.EQUAL_WEIGHT and config.require_comparable_assets:
        non_comparable = [
            instrument_id
            for instrument_id in ids
            if section_by_instrument[instrument_id] in {Section.OPTIONS, Section.PREDICTIONS, Section.FOREX}
        ]
        if non_comparable:
            return _failure(
                SolverStatus.UNSUPPORTED_INSTRUMENT,
                config,
                ids,
                (),
                "equal weight is not appropriate for derivatives or event contracts",
            )
    diagnostics = covariance_diagnostics(covariance)
    if not diagnostics.is_valid:
        return _failure(
            SolverStatus.INVALID_INPUT,
            config,
            ids,
            (),
            "; ".join(diagnostics.warnings) or "invalid covariance",
        )
    warnings.extend(diagnostics.warnings)
    equity = snapshot.capital_summary.equity_value
    if equity is None or equity <= 0:
        return _failure(
            SolverStatus.INVALID_INPUT, config, ids, (), "portfolio equity is unknown or non-positive"
        )
    if current_weights is None:
        current = _current_weights(exposure, ids, equity)
    else:
        current = tuple(current_weights)
        if len(current) != len(ids):
            return _failure(
                SolverStatus.INVALID_INPUT,
                config,
                ids,
                (),
                "current weights do not match the instrument universe",
            )
        for value in current:
            require_finite(value, "current weight")
    if expected_returns is not None:
        if len(expected_returns) != len(ids):
            return _failure(
                SolverStatus.INVALID_INPUT,
                config,
                ids,
                (),
                "expected returns do not match the instrument universe",
            )
        for value in expected_returns:
            require_finite(value, "expected return")
    if config.method == OptimizationMethod.CVAR and (
        scenario_returns is None or scenario_probabilities is None
    ):
        return _failure(
            SolverStatus.INVALID_INPUT,
            config,
            ids,
            current,
            "scenario returns and probabilities are required for CVaR",
        )

    budget_weights = _risk_budget_weights(ids, section_by_instrument, budget)
    status, raw, message, iterations = _raw_weights(
        config,
        covariance=covariance,
        current_weights=current,
        expected_returns=expected_returns,
        risk_budget_weights=budget_weights,
        scenario_returns=scenario_returns,
        scenario_probabilities=scenario_probabilities,
    )
    if status in {SolverStatus.INVALID_INPUT, SolverStatus.INFEASIBLE} or not raw:
        return _failure(status, config, ids, current, message, iterations=iterations)

    weights = tuple(raw)
    if config.long_only and min(weights) < -1e-9:
        return _failure(
            SolverStatus.INFEASIBLE,
            config,
            ids,
            current,
            "solver returned a negative weight under a long-only policy",
            attempted=weights,
            iterations=iterations,
        )
    one_way_turnover = turnover(current, weights)
    if one_way_turnover > config.turnover_limit + 1e-9:
        return _failure(
            SolverStatus.INFEASIBLE,
            config,
            ids,
            current,
            "turnover limit exceeded",
            attempted=weights,
            iterations=iterations,
        )

    if policy is not None:
        constraints = evaluate_allocation_constraints(
            instrument_ids=ids,
            weights=weights,
            current_weights=current,
            covariance=covariance,
            policy=policy,
            equity=equity,
            section_by_instrument=section_by_instrument,
            expected_returns=expected_returns,
            risk_report=risk_report,
            factor_exposures=factor_exposures,
            factor_caps=factor_caps,
            eligible_instruments=eligible_instruments,
            liquidity_horizon_by_instrument=liquidity_horizon_by_instrument,
            available_cash=available_cash,
            fully_invested=config.fully_invested,
            allow_short=not config.long_only,
            stress_passed=stress_passed,
        )
    else:
        constraints = _minimal_constraints(ids, weights, covariance, config)
        warnings.append("no policy supplied; only structural constraints were checked")
    if not hard_constraints_pass(constraints):
        failures = ", ".join(check.name for check in failed_hard_constraints(constraints))
        return _failure(
            SolverStatus.INFEASIBLE,
            config,
            ids,
            current,
            f"hard constraints failed: {failures}",
            attempted=weights,
            iterations=iterations,
            constraints=constraints,
        )

    result_status = status
    if status == SolverStatus.APPROXIMATE:
        warnings.append(message)
    expected_volatility = _expected_volatility(weights, covariance)
    if expected_volatility is None:
        return _failure(
            SolverStatus.SOLVER_ERROR,
            config,
            ids,
            current,
            "computed portfolio variance is negative",
            attempted=weights,
            iterations=iterations,
        )
    input_fingerprint = fingerprint(
        "alloc",
        OPTIMIZER_VERSION,
        snapshot.snapshot_id,
        exposure.exposure_snapshot_id,
        budget.budget_id,
        config,
        ids,
        covariance,
        expected_returns,
        current,
        current_time,
    )
    return AllocationResult(
        status=result_status,
        method=config.method,
        instrument_ids=ids,
        weights=weights,
        current_weights=current,
        expected_return=_expected_return(weights, expected_returns),
        expected_volatility=expected_volatility,
        turnover=one_way_turnover,
        estimated_cost=cost_summary,
        constraint_results=constraints,
        solver_message=message,
        iterations=iterations,
        input_fingerprint=input_fingerprint,
        warnings=tuple(dict.fromkeys(warnings)),
        attempted_weights=weights,
    )


def _failure(
    status: SolverStatus,
    config: OptimizationConfig,
    instrument_ids: Sequence[str],
    current_weights: Sequence[float],
    message: str,
    *,
    attempted: Sequence[float] | None = None,
    iterations: int = 0,
    constraints: Sequence[ConstraintResult] = (),
) -> AllocationResult:
    return AllocationResult(
        status=status,
        method=config.method,
        instrument_ids=tuple(instrument_ids),
        weights=None,
        current_weights=tuple(current_weights),
        expected_return=None,
        expected_volatility=None,
        turnover=None,
        estimated_cost=None,
        constraint_results=tuple(constraints),
        solver_message=message,
        iterations=iterations,
        input_fingerprint=fingerprint(
            "alloc_failure", config, tuple(instrument_ids), message, attempted
        ),
        warnings=(message,),
        attempted_weights=tuple(attempted) if attempted is not None else None,
    )


def compare_with_baseline(
    candidate: AllocationResult,
    baseline: AllocationResult,
) -> BaselineComparison:
    """Compare a candidate against a simple baseline on identical inputs."""
    if candidate.weights is None or baseline.weights is None:
        raise ValueError("both candidate and baseline must have valid weights")
    if candidate.instrument_ids != baseline.instrument_ids:
        raise ValueError("candidate and baseline universes differ")
    distance = sqrt(
        sum(
            (candidate.weights[index] - baseline.weights[index]) ** 2
            for index in range(len(candidate.weights))
        )
    )
    volatility_difference = (
        (candidate.expected_volatility or 0.0) - (baseline.expected_volatility or 0.0)
    )
    candidate_cost = candidate.estimated_cost.total_cost if candidate.estimated_cost else 0.0
    baseline_cost = baseline.estimated_cost.total_cost if baseline.estimated_cost else 0.0
    notes: list[str] = []
    if candidate.method == baseline.method:
        notes.append("candidate and baseline use the same method")
    if candidate.status == SolverStatus.APPROXIMATE:
        notes.append("candidate is an approximate solution")
    return BaselineComparison(
        baseline_method=baseline.method,
        weight_distance=distance,
        volatility_difference=volatility_difference,
        cost_difference=candidate_cost - baseline_cost,
        turnover_difference=(candidate.turnover or 0.0) - (baseline.turnover or 0.0),
        notes=tuple(notes),
    )


def stability_analysis(
    *,
    base_weights: Sequence[float],
    covariance: Sequence[Sequence[float]],
    expected_returns: Sequence[float] | None,
    config: OptimizationConfig,
    risk_budget_weights: Sequence[float] | None = None,
    perturbations: Sequence[float] = (0.05, 0.10),
    scenario_returns: Sequence[Sequence[float]] | None = None,
    scenario_probabilities: Sequence[float] | None = None,
) -> StabilityResult:
    """Re-solve under covariance/return perturbations and measure weight drift."""
    if not perturbations:
        return StabilityResult(0.0, 0.0, 0, ())
    budgets = tuple(risk_budget_weights or (1.0 / len(base_weights),) * len(base_weights))
    distances: list[float] = []
    warnings: list[str] = []
    for perturbation in perturbations:
        if not -1.0 < perturbation < 1.0:
            raise ValueError("perturbations must be in (-1, 1)")
        scaled_covariance = [
            [value * (1.0 + perturbation) for value in row] for row in covariance
        ]
        scaled_returns = (
            [value * (1.0 + perturbation) for value in expected_returns]
            if expected_returns is not None
            else None
        )
        status, weights, message, _ = _raw_weights(
            config,
            covariance=scaled_covariance,
            current_weights=base_weights,
            expected_returns=scaled_returns,
            risk_budget_weights=budgets,
            scenario_returns=scenario_returns,
            scenario_probabilities=scenario_probabilities,
        )
        if status in {SolverStatus.INVALID_INPUT, SolverStatus.INFEASIBLE} or not weights:
            warnings.append(f"perturbation {perturbation:.2%} failed: {message}")
            continue
        distances.append(
            sqrt(
                sum(
                    (weights[index] - base_weights[index]) ** 2
                    for index in range(len(base_weights))
                )
            )
        )
    if not distances:
        return StabilityResult(0.0, 0.0, 0, tuple(warnings))
    return StabilityResult(
        max_weight_distance=max(distances),
        mean_weight_distance=sum(distances) / len(distances),
        perturbed_cases=len(distances),
        warnings=tuple(warnings),
    )


__all__ = [
    "OPTIMIZER_VERSION",
    "compare_with_baseline",
    "exposure_by_instrument",
    "generate_allocation",
    "stability_analysis",
]
