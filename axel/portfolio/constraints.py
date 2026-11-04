"""
P11-GOVERNANCE: independent hard-constraint evaluation.

Hard constraints are not penalties inside the optimizer objective. They are
re-evaluated here, independently, from the final weights and approved policy.
A solver's success status is not authorization; this module only reports
constraint results and never approves an order.

Units:

* weights are fractions of portfolio equity and sum to one when fully invested.
* exposure, VaR, ES, margin, and stress limits are base-currency amounts.
* HHI is a dimensionless index.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from axel.core.types import Section
from axel.portfolio.returns import covariance_diagnostics
from axel.portfolio.risk import RiskReport, hhi
from axel.portfolio.types import (
    ConstraintResult,
    ConstraintSeverity,
    RiskBudgetPolicy,
    as_section,
    require_finite,
)


def _check(
    name: str,
    passed: bool,
    actual: float | None,
    limit: float | None,
    unit: str,
    message: str = "",
    *,
    hard: bool = True,
) -> ConstraintResult:
    return ConstraintResult(
        name=name,
        severity=ConstraintSeverity.HARD if hard else ConstraintSeverity.SOFT,
        passed=passed,
        actual=actual,
        limit=limit,
        unit=unit,
        message=message,
    )


def _validate_weights(weights: Sequence[float]) -> None:
    if not weights:
        raise ValueError("weights must be non-empty")
    for value in weights:
        require_finite(value, "weight")


def evaluate_allocation_constraints(
    *,
    instrument_ids: Sequence[str],
    weights: Sequence[float],
    current_weights: Sequence[float],
    covariance: Sequence[Sequence[float]],
    policy: RiskBudgetPolicy,
    equity: float,
    section_by_instrument: Mapping[str, Section],
    expected_returns: Sequence[float] | None = None,
    risk_report: RiskReport | None = None,
    factor_exposures: Mapping[str, float] | None = None,
    factor_caps: Mapping[str, float] | None = None,
    eligible_instruments: Sequence[str] | None = None,
    liquidity_horizon_by_instrument: Mapping[str, float] | None = None,
    available_cash: float | None = None,
    fully_invested: bool = True,
    allow_short: bool = False,
    stress_passed: bool | None = None,
) -> tuple[ConstraintResult, ...]:
    """Evaluate every applicable hard constraint on final weights."""
    _validate_weights(weights)
    if len(weights) != len(instrument_ids) or len(weights) != len(current_weights):
        raise ValueError("instrument, weight, and current-weight dimensions must match")
    if len(weights) != len(covariance):
        raise ValueError("weight and covariance dimensions must match")
    if equity <= 0:
        return (
            _check(
                "positive_equity",
                False,
                equity,
                0.0,
                "currency",
                "portfolio equity must be positive for weight constraints",
            ),
        )
    results: list[ConstraintResult] = []
    total_weight = sum(weights)
    results.append(
        _check(
            "fully_invested",
            abs(total_weight - 1.0) <= 1e-6 if fully_invested else True,
            total_weight,
            1.0,
            "weight",
            "weights must sum to one when the portfolio is fully invested",
        )
    )
    minimum_weight = min(weights)
    results.append(
        _check(
            "long_only",
            minimum_weight >= -1e-9 if not allow_short else True,
            minimum_weight,
            0.0,
            "weight",
            "negative weights are not permitted under the long-only policy",
        )
    )
    max_weight = max(weights)
    results.append(
        _check(
            "max_single_instrument_weight",
            max_weight <= policy.max_single_instrument_weight + 1e-9,
            max_weight,
            policy.max_single_instrument_weight,
            "weight",
            "a single instrument exceeds the policy weight cap",
        )
    )
    gross = sum(abs(value) for value in weights) * equity
    results.append(
        _check(
            "global_max_gross_exposure",
            gross <= policy.global_max_gross_exposure + 1e-6,
            gross,
            policy.global_max_gross_exposure,
            "currency",
            "gross exposure exceeds the approved global cap",
        )
    )
    net = abs(sum(weights)) * equity
    results.append(
        _check(
            "global_max_net_exposure",
            net <= policy.global_max_net_exposure + 1e-6,
            net,
            policy.global_max_net_exposure,
            "currency",
            "net exposure exceeds the approved global cap",
        )
    )
    turnover = sum(
        abs(proposed - current)
        for proposed, current in zip(weights, current_weights, strict=True)
    )
    results.append(
        _check(
            "max_turnover",
            turnover <= policy.global_max_turnover + 1e-9,
            turnover,
            policy.global_max_turnover,
            "weight",
            "one-way turnover exceeds the approved limit",
        )
    )
    concentration = hhi(tuple(abs(value) for value in weights))
    results.append(
        _check(
            "max_concentration_hhi",
            concentration <= policy.global_max_concentration_hhi + 1e-9,
            concentration,
            policy.global_max_concentration_hhi,
            "index",
            "instrument concentration exceeds the approved HHI cap",
        )
    )

    section_gross: dict[Section, float] = {}
    for instrument_id, weight in zip(instrument_ids, weights, strict=True):
        section = as_section(section_by_instrument[instrument_id])
        section_gross[section] = section_gross.get(section, 0.0) + abs(weight) * equity
    for section, exposure in sorted(section_gross.items(), key=lambda item: item[0].value):
        cap = policy.cap_for(section)
        if cap is not None:
            results.append(
                _check(
                    f"section_cap:{section.value}",
                    exposure <= cap + 1e-6,
                    exposure,
                    cap,
                    "currency",
                    f"{section.value} exposure exceeds its approved cap",
                )
            )

    if liquidity_horizon_by_instrument:
        illiquid_weight = sum(
            abs(weight)
            for instrument_id, weight in zip(instrument_ids, weights, strict=True)
            if liquidity_horizon_by_instrument.get(instrument_id, 0.0) > 5.0
        )
        results.append(
            _check(
                "max_illiquid_weight",
                illiquid_weight <= policy.max_illiquid_weight + 1e-9,
                illiquid_weight,
                policy.max_illiquid_weight,
                "weight",
                "illiquid allocation exceeds the approved weight cap",
            )
        )

    if available_cash is not None:
        required_cash = policy.min_cash_reserve_pct * equity
        results.append(
            _check(
                "min_cash_reserve",
                available_cash + 1e-6 >= required_cash,
                available_cash,
                required_cash,
                "currency",
                "available cash is below the approved reserve floor",
            )
        )

    if risk_report is not None:
        var_amount = risk_report.var_95 * equity
        es_amount = risk_report.expected_shortfall_95 * equity
        if policy.global_max_var is not None:
            results.append(
                _check(
                    "global_max_var",
                    var_amount <= policy.global_max_var + 1e-6,
                    var_amount,
                    policy.global_max_var,
                    "currency",
                    "95% VaR exceeds the approved global limit",
                )
            )
        if policy.global_max_expected_shortfall is not None:
            results.append(
                _check(
                    "global_max_expected_shortfall",
                    es_amount <= policy.global_max_expected_shortfall + 1e-6,
                    es_amount,
                    policy.global_max_expected_shortfall,
                    "currency",
                    "95% expected shortfall exceeds the approved global limit",
                )
            )
        if policy.global_max_margin is not None:
            margin = risk_report.margin_requirement
            results.append(
                _check(
                    "global_max_margin",
                    margin is not None and margin <= policy.global_max_margin + 1e-6,
                    margin,
                    policy.global_max_margin,
                    "currency",
                    "margin requirement is unknown or exceeds the approved limit",
                )
            )

    if policy.global_max_stress_loss is not None:
        results.append(
            _check(
                "stress_suite_passed",
                stress_passed is True,
                None if stress_passed is None else float(stress_passed),
                1.0,
                "boolean",
                "the approved stress suite has not passed",
            )
        )

    if factor_exposures and factor_caps:
        for factor_id, cap in sorted(factor_caps.items()):
            exposure = abs(factor_exposures.get(factor_id, 0.0))
            results.append(
                _check(
                    f"factor_cap:{factor_id}",
                    exposure <= cap + 1e-6,
                    exposure,
                    cap,
                    "currency",
                    f"factor {factor_id} exposure exceeds its approved cap",
                )
            )

    if eligible_instruments is not None:
        eligible = set(eligible_instruments)
        ineligible = [
            instrument_id
            for instrument_id, weight in zip(instrument_ids, weights, strict=True)
            if abs(weight) > 1e-9 and instrument_id not in eligible
        ]
        results.append(
            _check(
                "instrument_eligibility",
                not ineligible,
                float(len(ineligible)),
                0.0,
                "count",
                "ineligible instruments have non-zero target weights: "
                + ", ".join(sorted(ineligible)),
            )
        )

    if expected_returns is not None:
        if len(expected_returns) != len(weights):
            raise ValueError("expected returns must match the weight dimension")
        for value in expected_returns:
            require_finite(value, "expected return")

    # The covariance argument is part of the interface so callers cannot
    # accidentally evaluate weights against a different risk model than the one
    # used to size them. Validity is checked here, independently of the solver.
    diagnostics = covariance_diagnostics(covariance)
    results.append(
        _check(
            "covariance_validity",
            diagnostics.is_valid,
            None,
            None,
            "boolean",
            "; ".join(diagnostics.warnings) or "covariance must be symmetric PSD",
        )
    )
    return tuple(results)


def hard_constraints_pass(results: Sequence[ConstraintResult]) -> bool:
    return all(check.passed for check in results if check.hard)


def failed_hard_constraints(
    results: Sequence[ConstraintResult],
) -> tuple[ConstraintResult, ...]:
    return tuple(check for check in results if check.hard and not check.passed)


def risk_budget_utilization(
    section_budgets: Mapping[str, float],
    section_exposure: Mapping[str, float],
) -> Mapping[str, float]:
    """Utilization helper; unknown or zero budgets return 0 rather than infinity."""
    result: dict[str, float] = {}
    for section, budget in section_budgets.items():
        exposure = section_exposure.get(section, 0.0)
        result[section] = exposure / budget if budget > 0 else 0.0
    return result


__all__ = [
    "evaluate_allocation_constraints",
    "failed_hard_constraints",
    "hard_constraints_pass",
    "risk_budget_utilization",
]
