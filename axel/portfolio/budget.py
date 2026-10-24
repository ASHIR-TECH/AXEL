"""
P11-BUDGET: global and section risk-budget proposals.

A budget is approved risk capacity, not cash and not an order. Section budgets
always reconcile to the global policy; an optimizer cannot increase a hard
limit. Incomplete data applies a conservative haircut and is never allowed to
increase capacity.

The base-currency budget is a planning amount. It is deliberately not equated
with available cash: leveraged FX, options, and prediction contracts have
different capital and loss profiles.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite

from axel.core.types import Section
from axel.portfolio.serialization import fingerprint
from axel.portfolio.types import (
    ConstraintResult,
    ConstraintSeverity,
    DataQuality,
    ExposureSnapshot,
    PortfolioSnapshot,
    RiskBudgetPolicy,
    RiskBudgetProposal,
    SectionBudget,
    SectionReadiness,
    SnapshotCompleteness,
    require_aware,
    require_non_negative,
    utc_now,
)


class BudgetMethod(str, Enum):
    FIXED = "fixed"
    EQUAL_RISK = "equal_risk"
    VOLATILITY = "volatility"
    RISK_PARITY = "risk_parity"
    CURRENT = "current"


@dataclass(frozen=True)
class BudgetValidation:
    passed: bool
    checks: tuple[ConstraintResult, ...]
    messages: tuple[str, ...] = ()


def _hard_check(
    name: str,
    passed: bool,
    actual: float | None,
    limit: float | None,
    unit: str,
    message: str = "",
) -> ConstraintResult:
    return ConstraintResult(
        name=name,
        severity=ConstraintSeverity.HARD,
        passed=passed,
        actual=actual,
        limit=limit,
        unit=unit,
        message=message,
    )


def available_risk_capital(
    snapshot: PortfolioSnapshot,
    policy: RiskBudgetPolicy,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    """Return ``(cap, binding_constraints, warnings)`` for the global budget.

    The cap is the minimum of the approved gross cap, a leverage cap, and a
    liquidity/cash-reserve cap. Unknown capital produces zero capacity.
    """
    capital = snapshot.capital_summary
    constraints: list[str] = []
    warnings: list[str] = []
    if capital.equity_value is None or capital.total_commitments is None:
        return 0.0, ("capital_unknown",), ("equity or commitments are unknown",)
    if not capital.conversion_complete:
        return 0.0, ("currency_conversion_incomplete",), (
            "currency conversion is incomplete",
        )
    equity = capital.equity_value
    if equity <= 0:
        return 0.0, ("non_positive_equity",), ("portfolio equity is non-positive",)

    leverage_cap = policy.max_leverage * equity
    min_cash = policy.min_cash_reserve_pct * equity
    liquidity_cap = max(0.0, equity - capital.total_commitments - min_cash)
    cap = min(policy.global_max_gross_exposure, leverage_cap, liquidity_cap)
    if cap == policy.global_max_gross_exposure:
        constraints.append("global_max_gross_exposure")
    if cap == leverage_cap:
        constraints.append("max_leverage")
    if cap == liquidity_cap:
        constraints.append("min_cash_reserve")
    if cap <= 0:
        warnings.append("available risk capacity is zero")
        return 0.0, tuple(constraints), tuple(warnings)

    if (
        snapshot.completeness_status != SnapshotCompleteness.COMPLETE
        or snapshot.data_quality_status != DataQuality.VALID
        or not snapshot.is_usable_for_risk_increase
    ):
        haircut = policy.incomplete_data_haircut
        cap *= max(0.0, 1.0 - haircut)
        constraints.append("incomplete_data_haircut")
        warnings.append(
            f"incomplete or degraded snapshot applied a {haircut:.0%} capacity haircut"
        )
    return cap, tuple(constraints), tuple(warnings)


def _eligible_sections(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
) -> tuple[Section, ...]:
    eligible: list[Section] = []
    for section in Section:
        status = snapshot.section(section)
        if status is None:
            continue
        if status.readiness not in {
            SectionReadiness.LIVE_ELIGIBLE,
            SectionReadiness.PAPER_ONLY,
        }:
            continue
        section_exposure = exposure.section(section)
        if section_exposure is not None and "exposure_failure" in section_exposure.uncertainty_flags:
            continue
        eligible.append(section)
    return tuple(eligible)


def _inverse_weights(
    sections: Sequence[Section],
    section_volatilities: Mapping[str, float] | None,
) -> dict[Section, float]:
    if not section_volatilities:
        return {section: 1.0 for section in sections}
    raw: dict[Section, float] = {}
    for section in sections:
        volatility = section_volatilities.get(section.value)
        if volatility is None or not isfinite(volatility) or volatility <= 0:
            raw[section] = 1.0
        else:
            raw[section] = 1.0 / volatility
    total = sum(raw.values())
    return {section: value / total for section, value in raw.items()}


def propose_risk_budgets(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    policy: RiskBudgetPolicy,
    *,
    method: BudgetMethod = BudgetMethod.EQUAL_RISK,
    section_volatilities: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> RiskBudgetProposal:
    """Propose section budgets that reconcile to the global approved cap."""
    current = require_aware(now or utc_now(), "now")
    if not isinstance(method, BudgetMethod):
        method = BudgetMethod(method)
    global_cap, binding, warnings = available_risk_capital(snapshot, policy)
    eligible = _eligible_sections(snapshot, exposure)
    warnings_list = list(warnings)
    binding_list = list(binding)

    for section in Section:
        if section in eligible:
            continue
        status = snapshot.section(section)
        if status is not None and status.readiness not in {
            SectionReadiness.DISABLED,
            SectionReadiness.RESEARCH_ONLY,
        }:
            warnings_list.append(
                f"section {section.value} is {status.readiness.value} and receives no new budget"
            )

    if not eligible or global_cap <= 0:
        section_budgets = tuple(
            SectionBudget(
                section=section,
                capital_budget=0.0,
                risk_budget=0.0,
                max_gross_exposure=0.0,
                current_gross_exposure=(
                    exposure.section(section).gross_market_value
                    if exposure.section(section) is not None
                    else 0.0
                ),
                current_net_exposure=(
                    exposure.section(section).net_market_value
                    if exposure.section(section) is not None
                    else 0.0
                ),
                readiness=(
                    snapshot.section(section).readiness
                    if snapshot.section(section) is not None
                    else SectionReadiness.UNKNOWN
                ),
                eligible_for_increase=False,
                binding_constraints=("no_eligible_sections",),
                warnings=("no eligible section or no capacity",),
            )
            for section in Section
        )
        return RiskBudgetProposal(
            budget_id=fingerprint(
                "budget", snapshot.snapshot_id, exposure.exposure_snapshot_id, policy.policy_version, method.value
            ),
            portfolio_snapshot_id=snapshot.snapshot_id,
            exposure_snapshot_id=exposure.exposure_snapshot_id,
            policy_version=policy.policy_version,
            method=method.value,
            section_budgets=section_budgets,
            total_capital_budget=0.0,
            total_risk_budget=0.0,
            binding_constraints=tuple(dict.fromkeys(binding_list + ["no_eligible_sections"])),
            warnings=tuple(dict.fromkeys(warnings_list)),
            is_valid=False,
            created_at=current,
        )

    if method in {BudgetMethod.EQUAL_RISK}:
        weights = {section: 1.0 / len(eligible) for section in eligible}
    elif method in {BudgetMethod.VOLATILITY, BudgetMethod.RISK_PARITY}:
        weights = _inverse_weights(eligible, section_volatilities)
        if method == BudgetMethod.RISK_PARITY:
            warnings_list.append(
                "section-level risk parity is an inverse-volatility approximation, "
                "not a full instrument-level risk-budget solution"
            )
    elif method == BudgetMethod.CURRENT:
        current_values = {
            section: (
                exposure.section(section).gross_market_value
                if exposure.section(section) is not None
                else 0.0
            )
            for section in eligible
        }
        total_current = sum(current_values.values())
        if total_current <= 0:
            weights = {section: 1.0 / len(eligible) for section in eligible}
        else:
            weights = {
                section: current_values[section] / total_current for section in eligible
            }
    elif method == BudgetMethod.FIXED:
        configured = {
            section: policy.cap_for(section) for section in eligible
        }
        configured_total = sum(
            value for value in configured.values() if value is not None
        )
        if configured_total <= 0:
            warnings_list.append("no fixed section caps configured; equal allocation used")
            weights = {section: 1.0 / len(eligible) for section in eligible}
        else:
            missing = [section for section, cap in configured.items() if cap is None]
            if missing:
                warnings_list.append(
                    "sections without a fixed cap receive no budget: "
                    + ", ".join(section.value for section in missing)
                )
            weights = {
                section: (configured[section] or 0.0) / configured_total
                for section in eligible
            }
    else:
        raise ValueError(f"unsupported budget method: {method}")

    raw_budgets = {
        section: global_cap * weight for section, weight in weights.items()
    }
    for section in eligible:
        cap = policy.cap_for(section)
        if cap is not None and raw_budgets[section] > cap:
            raw_budgets[section] = cap
            binding_list.append(f"section_cap:{section.value}")
    total = sum(raw_budgets.values())
    if total > global_cap and total > 0:
        scale = global_cap / total
        raw_budgets = {section: value * scale for section, value in raw_budgets.items()}
        binding_list.append("global_budget_scaled")
        total = global_cap
    if total < global_cap:
        # The difference is deliberately unallocated, not silently assigned.
        warnings_list.append(
            f"{global_cap - total:.2f} of approved capacity remains unallocated"
        )

    section_budgets_list: list[SectionBudget] = []
    for section in Section:
        section_exposure = exposure.section(section)
        status = snapshot.section(section)
        readiness = status.readiness if status is not None else SectionReadiness.UNKNOWN
        budget = raw_budgets.get(section, 0.0)
        section_budgets_list.append(
            SectionBudget(
                section=section,
                capital_budget=budget,
                risk_budget=budget,
                max_gross_exposure=budget,
                current_gross_exposure=(
                    section_exposure.gross_market_value if section_exposure is not None else 0.0
                ),
                current_net_exposure=(
                    section_exposure.net_market_value if section_exposure is not None else 0.0
                ),
                readiness=readiness,
                eligible_for_increase=section in eligible,
                binding_constraints=(
                    (f"section_cap:{section.value}",)
                    if policy.cap_for(section) is not None
                    else ()
                ),
                warnings=(
                    ("exposure data is incomplete",)
                    if section_exposure is not None
                    and "exposure_failure" in section_exposure.uncertainty_flags
                    else ()
                ),
            )
        )

    total_budget = sum(item.risk_budget for item in section_budgets_list)
    is_valid = total_budget <= global_cap + 1e-6 and total_budget >= 0
    return RiskBudgetProposal(
        budget_id=fingerprint(
            "budget",
            snapshot.snapshot_id,
            exposure.exposure_snapshot_id,
            policy.policy_version,
            method.value,
            tuple(section_budgets_list),
        ),
        portfolio_snapshot_id=snapshot.snapshot_id,
        exposure_snapshot_id=exposure.exposure_snapshot_id,
        policy_version=policy.policy_version,
        method=method.value,
        section_budgets=tuple(section_budgets_list),
        total_capital_budget=total_budget,
        total_risk_budget=total_budget,
        binding_constraints=tuple(dict.fromkeys(binding_list)),
        warnings=tuple(dict.fromkeys(warnings_list)),
        is_valid=is_valid,
        created_at=current,
    )


def validate_budget_reconciliation(
    proposal: RiskBudgetProposal,
    policy: RiskBudgetPolicy,
) -> BudgetValidation:
    """Independently verify that section budgets reconcile to the policy."""
    checks: list[ConstraintResult] = []
    section_total = sum(item.risk_budget for item in proposal.section_budgets)
    checks.append(
        _hard_check(
            "section_budgets_reconcile",
            abs(section_total - proposal.total_risk_budget) <= 1e-6,
            section_total,
            proposal.total_risk_budget,
            "currency",
            "section budgets must sum to the proposal total",
        )
    )
    checks.append(
        _hard_check(
            "global_budget_cap",
            proposal.total_risk_budget <= policy.global_max_gross_exposure + 1e-6,
            proposal.total_risk_budget,
            policy.global_max_gross_exposure,
            "currency",
            "total budget cannot exceed the approved global cap",
        )
    )
    for item in proposal.section_budgets:
        require_non_negative(item.risk_budget, "section risk budget")
        cap = policy.cap_for(item.section)
        if cap is not None:
            checks.append(
                _hard_check(
                    f"section_cap:{item.section.value}",
                    item.risk_budget <= cap + 1e-6,
                    item.risk_budget,
                    cap,
                    "currency",
                    f"{item.section.value} budget exceeds its policy cap",
                )
            )
    passed = all(check.passed for check in checks)
    return BudgetValidation(
        passed=passed,
        checks=tuple(checks),
        messages=tuple(check.message for check in checks if not check.passed),
    )


__all__ = [
    "BudgetMethod",
    "BudgetValidation",
    "available_risk_capital",
    "propose_risk_budgets",
    "validate_budget_reconciliation",
]
