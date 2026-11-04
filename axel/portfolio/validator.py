"""
P11-GOVERNANCE: deterministic authorization for portfolio proposals.

The validator is deliberately independent of the optimizer. It re-checks
references, freshness, data quality, reconciliation, section readiness, solver
status, budget reconciliation, stress results, and hard constraints before
authorizing anything.

Authorization is not order submission. A successful validation means the
proposal may be handed to AXEL's existing deterministic risk/execution path;
this module never imports or calls that path.

Risk-reducing proposals may remain possible during a kill switch or degraded
state when policy permits it. Risk-increasing proposals fail closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from axel.core.types import Section
from axel.portfolio.budget import validate_budget_reconciliation
from axel.portfolio.constraints import (
    evaluate_allocation_constraints,
)
from axel.portfolio.proposal import is_proposal_fresh
from axel.portfolio.returns import covariance_diagnostics
from axel.portfolio.risk import RiskReport, hhi
from axel.portfolio.serialization import fingerprint
from axel.portfolio.stress import StressReport
from axel.portfolio.types import (
    AllocationProposal,
    DataQuality,
    ExposureSnapshot,
    PortfolioSnapshot,
    ProposalAction,
    ProposalState,
    RebalanceProposal,
    ReconciliationIssue,
    ReconciliationStatus,
    RiskBudgetPolicy,
    RiskBudgetProposal,
    SectionReadiness,
    SolverStatus,
    ValidationCheck,
    ValidationDecision,
    as_section,
    require_aware,
)

VALIDATOR_VERSION = "p11-validator-1"


class ValidationCode(str, Enum):
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    SNAPSHOT_MISSING = "SNAPSHOT_MISSING"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    SNAPSHOT_INCOMPLETE = "SNAPSHOT_INCOMPLETE"
    SNAPSHOT_INVALID = "SNAPSHOT_INVALID"
    DATA_QUALITY_DEGRADED = "DATA_QUALITY_DEGRADED"
    RECONCILIATION_BLOCK = "RECONCILIATION_BLOCK"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    WATCHDOG_HALTED = "WATCHDOG_HALTED"
    PROPOSAL_EXPIRED = "PROPOSAL_EXPIRED"
    PROPOSAL_STATE_INVALID = "PROPOSAL_STATE_INVALID"
    SOLVER_FAILED = "SOLVER_FAILED"
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"
    STRESS_FAILED = "STRESS_FAILED"
    MISSING_STRESS_REPORT = "MISSING_STRESS_REPORT"
    SECTION_NOT_ELIGIBLE = "SECTION_NOT_ELIGIBLE"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    BUDGET_RECONCILIATION_FAILED = "BUDGET_RECONCILIATION_FAILED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DUPLICATE_IDEMPOTENCY_KEY = "DUPLICATE_IDEMPOTENCY_KEY"
    RISK_INCREASE_NOT_PERMITTED = "RISK_INCREASE_NOT_PERMITTED"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"
    REBALANCE_MISMATCH = "REBALANCE_MISMATCH"
    AUTHORIZED = "AUTHORIZED"


class ExecutionMode(str, Enum):
    LIVE = "live"
    PAPER = "paper"
    SHADOW = "shadow"
    RESEARCH = "research"


@dataclass(frozen=True)
class PortfolioValidationPolicy:
    policy_version: str = "p11-validation-1"
    max_proposal_age_seconds: int = 900
    max_snapshot_age_seconds: int = 300
    require_complete_snapshot: bool = True
    require_reconciled: bool = True
    allow_risk_reducing_when_degraded: bool = True
    require_stress_pass: bool = True
    require_human_approval_for_increase: bool = True
    require_watchdog_clear: bool = True
    require_kill_switch_clear: bool = True
    allow_paper_only_in_paper: bool = True

    def __post_init__(self) -> None:
        if not self.policy_version:
            raise ValueError("policy_version must be non-empty")
        for name in (
            "max_proposal_age_seconds",
            "max_snapshot_age_seconds",
        ):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class ValidationContext:
    now: datetime
    execution_mode: ExecutionMode = ExecutionMode.PAPER
    kill_switch_active: bool = False
    watchdog_halted: bool = False
    human_approval_ref: str | None = None
    seen_idempotency_keys: frozenset[str] = frozenset()
    reconciliation_issues: tuple[ReconciliationIssue, ...] = ()
    allowed_sections: tuple[Section, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "now", require_aware(self.now, "now"))
        if not isinstance(self.execution_mode, ExecutionMode):
            object.__setattr__(
                self, "execution_mode", ExecutionMode(self.execution_mode)
            )
        if self.allowed_sections is not None:
            object.__setattr__(
                self,
                "allowed_sections",
                tuple(as_section(section) for section in self.allowed_sections),
            )


@dataclass(frozen=True)
class PortfolioValidationResult:
    decision: ValidationDecision
    proposal_id: str
    reason_codes: tuple[str, ...]
    checks: tuple[ValidationCheck, ...]
    validated_at: datetime
    policy_version: str
    authorized: bool
    requires_human_approval: bool
    authorization_id: str | None = None
    execution_permitted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "validated_at", require_aware(self.validated_at, "validated_at")
        )

    @property
    def passed(self) -> bool:
        return self.decision == ValidationDecision.ACCEPT


def _check(
    code: ValidationCode | str,
    passed: bool,
    detail: str,
    *,
    hard: bool = True,
) -> ValidationCheck:
    return ValidationCheck(
        code=code.value if isinstance(code, ValidationCode) else code,
        passed=passed,
        detail=detail,
        hard=hard,
    )


def _result(
    *,
    proposal_id: str,
    checks: Sequence[ValidationCheck],
    context: ValidationContext,
    policy: PortfolioValidationPolicy,
    requires_human_approval: bool,
) -> PortfolioValidationResult:
    hard_failures = [check for check in checks if check.hard and not check.passed]
    non_hard_failures = [
        check for check in checks if not check.hard and not check.passed
    ]
    if hard_failures:
        decision = ValidationDecision.REJECT
    elif non_hard_failures:
        decision = ValidationDecision.REQUIRE_REVIEW
    else:
        decision = ValidationDecision.ACCEPT
    authorized = decision == ValidationDecision.ACCEPT
    reason_codes = tuple(
        dict.fromkeys(
            check.code
            for check in checks
            if not check.passed
        )
    )
    if authorized:
        reason_codes = reason_codes + (ValidationCode.AUTHORIZED.value,)
    authorization_id = (
        fingerprint(
            "auth",
            VALIDATOR_VERSION,
            proposal_id,
            policy.policy_version,
            tuple(checks),
            context.now,
        )
        if authorized
        else None
    )
    return PortfolioValidationResult(
        decision=decision,
        proposal_id=proposal_id,
        reason_codes=reason_codes,
        checks=tuple(checks),
        validated_at=context.now,
        policy_version=policy.policy_version,
        authorized=authorized,
        requires_human_approval=requires_human_approval,
        authorization_id=authorization_id,
        execution_permitted=authorized,
    )


def _target_vectors(
    proposal: AllocationProposal,
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...]]:
    ids = tuple(target.instrument_id for target in proposal.proposed_targets)
    proposed = tuple(target.proposed_weight for target in proposal.proposed_targets)
    current = tuple(target.current_weight for target in proposal.proposed_targets)
    return ids, proposed, current


def _section_map(
    proposal: AllocationProposal,
    provided: Mapping[str, Section] | None,
) -> Mapping[str, Section]:
    if provided is not None:
        return {key: as_section(value) for key, value in provided.items()}
    return {target.instrument_id: target.section for target in proposal.proposed_targets}


def _structural_checks(
    proposal: AllocationProposal,
    policy: RiskBudgetPolicy,
) -> tuple[ValidationCheck, ...]:
    if not proposal.proposed_targets:
        return (
            _check(
                ValidationCode.SCHEMA_INVALID,
                False,
                "proposal has no targets",
            ),
        )
    weights = tuple(target.proposed_weight for target in proposal.proposed_targets)
    checks: list[ValidationCheck] = [
        _check(
            "weights_sum_to_one",
            abs(sum(weights) - 1.0) <= 1e-6,
            f"sum of proposed weights is {sum(weights):.8f}",
        ),
        _check(
            "long_only",
            min(weights) >= -1e-9,
            f"minimum proposed weight is {min(weights):.8f}",
        ),
        _check(
            "max_single_instrument_weight",
            max(weights) <= policy.max_single_instrument_weight + 1e-9,
            f"maximum proposed weight is {max(weights):.8f}",
        ),
        _check(
            "max_concentration_hhi",
            hhi(tuple(abs(value) for value in weights))
            <= policy.global_max_concentration_hhi + 1e-9,
            "instrument HHI check",
        ),
    ]
    turnover = sum(
        abs(proposed - current)
        for proposed, current in zip(
            weights,
            (target.current_weight for target in proposal.proposed_targets),
            strict=True,
        )
    )
    checks.append(
        _check(
            "max_turnover",
            turnover <= policy.global_max_turnover + 1e-9,
            f"one-way turnover is {turnover:.8f}",
        )
    )
    return tuple(checks)


def _independent_constraint_checks(
    *,
    proposal: AllocationProposal,
    snapshot: PortfolioSnapshot,
    policy: RiskBudgetPolicy,
    risk_report: RiskReport | None,
    covariance: Sequence[Sequence[float]] | None,
    section_by_instrument: Mapping[str, Section] | None,
    expected_returns: Sequence[float] | None,
    eligible_instruments: Sequence[str] | None,
    factor_exposures: Mapping[str, float] | None,
    factor_caps: Mapping[str, float] | None,
    liquidity_horizon_by_instrument: Mapping[str, float] | None,
    available_cash: float | None,
    fully_invested: bool,
    allow_short: bool,
    stress_passed: bool,
    risk_increasing: bool,
) -> tuple[ValidationCheck, ...]:
    checks: list[ValidationCheck] = []
    ids, weights, current = _target_vectors(proposal)
    if not ids:
        return (_check(ValidationCode.SCHEMA_INVALID, False, "proposal has no targets"),)
    if covariance is None:
        if risk_increasing:
            checks.append(
                _check(
                    ValidationCode.SCHEMA_INVALID,
                    False,
                    "covariance is required to independently verify a risk-increasing proposal",
                )
            )
            return tuple(checks)
        return _structural_checks(proposal, policy)
    if len(covariance) != len(ids) or any(len(row) != len(ids) for row in covariance):
        checks.append(
            _check(
                ValidationCode.PROVENANCE_MISMATCH,
                False,
                "covariance dimension does not match proposal targets",
            )
        )
        return tuple(checks)
    diagnostics = covariance_diagnostics(covariance)
    checks.append(
        _check(
            "covariance_validity",
            diagnostics.is_valid,
            "; ".join(diagnostics.warnings) or "covariance is valid",
        )
    )
    if not diagnostics.is_valid:
        return tuple(checks)
    equity = snapshot.capital_summary.equity_value
    if equity is None or equity <= 0:
        checks.append(
            _check(
                ValidationCode.SNAPSHOT_INVALID,
                False,
                "portfolio equity is unknown or non-positive",
            )
        )
        return tuple(checks)
    section_map = _section_map(proposal, section_by_instrument)
    if any(instrument_id not in section_map for instrument_id in ids):
        checks.append(
            _check(
                ValidationCode.PROVENANCE_MISMATCH,
                False,
                "section mapping is incomplete",
            )
        )
        return tuple(checks)
    if eligible_instruments is None:
        checks.append(
            _check(
                ValidationCode.UNSUPPORTED_INSTRUMENT,
                False,
                "eligible instrument universe was not supplied; cannot verify eligibility",
            )
        )
        return tuple(checks)
    if tuple(eligible_instruments) != ids and any(
        instrument_id not in set(eligible_instruments) for instrument_id in ids
    ):
        checks.append(
            _check(
                ValidationCode.UNSUPPORTED_INSTRUMENT,
                False,
                "proposal contains an instrument outside the approved universe",
            )
        )
    if (
        policy.global_max_var is not None
        or policy.global_max_expected_shortfall is not None
    ) and risk_report is None:
        checks.append(
            _check(
                ValidationCode.SCHEMA_INVALID,
                False,
                "risk report is required to verify VaR/ES limits",
            )
        )
    if policy.global_max_margin is not None and risk_report is None:
        checks.append(
            _check(
                ValidationCode.SCHEMA_INVALID,
                False,
                "risk report is required to verify margin limits",
            )
        )
    results = evaluate_allocation_constraints(
        instrument_ids=ids,
        weights=weights,
        current_weights=current,
        covariance=covariance,
        policy=policy,
        equity=equity,
        section_by_instrument=section_map,
        expected_returns=expected_returns,
        risk_report=risk_report,
        factor_exposures=factor_exposures,
        factor_caps=factor_caps,
        eligible_instruments=eligible_instruments,
        liquidity_horizon_by_instrument=liquidity_horizon_by_instrument,
        available_cash=available_cash,
        fully_invested=fully_invested,
        allow_short=allow_short,
        stress_passed=stress_passed,
    )
    for result in results:
        checks.append(
            _check(
                result.name,
                result.passed,
                result.message or f"{result.name}: actual={result.actual} limit={result.limit}",
                hard=result.hard,
            )
        )
    return tuple(checks)


def _section_checks(
    proposal: AllocationProposal,
    snapshot: PortfolioSnapshot,
    context: ValidationContext,
    policy: PortfolioValidationPolicy,
) -> tuple[ValidationCheck, ...]:
    checks: list[ValidationCheck] = []
    increasing_sections = {
        target.section for target in proposal.proposed_targets if target.action == ProposalAction.INCREASE
    }
    if context.allowed_sections is not None:
        for section in sorted(increasing_sections, key=lambda value: value.value):
            checks.append(
                _check(
                    ValidationCode.SECTION_NOT_ELIGIBLE,
                    section in context.allowed_sections,
                    f"section {section.value} is outside the allowed sections",
                )
            )
    for section in sorted(increasing_sections, key=lambda value: value.value):
        status = snapshot.section(section)
        if status is None:
            checks.append(
                _check(
                    ValidationCode.SECTION_NOT_ELIGIBLE,
                    False,
                    f"section {section.value} has no status",
                )
            )
            continue
        if status.readiness == SectionReadiness.LIVE_ELIGIBLE or (
            status.readiness == SectionReadiness.PAPER_ONLY
            and context.execution_mode == ExecutionMode.PAPER
            and policy.allow_paper_only_in_paper
        ):
            allowed = True
        else:
            allowed = False
        checks.append(
            _check(
                ValidationCode.SECTION_NOT_ELIGIBLE,
                allowed,
                f"section {section.value} readiness is {status.readiness.value} "
                f"in {context.execution_mode.value} mode",
            )
        )
    return tuple(checks)


def validate_allocation_proposal(
    proposal: AllocationProposal,
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    budget: RiskBudgetProposal,
    policy: RiskBudgetPolicy,
    context: ValidationContext,
    *,
    validation_policy: PortfolioValidationPolicy | None = None,
    stress_report: StressReport | None = None,
    risk_report: RiskReport | None = None,
    covariance: Sequence[Sequence[float]] | None = None,
    section_by_instrument: Mapping[str, Section] | None = None,
    expected_returns: Sequence[float] | None = None,
    eligible_instruments: Sequence[str] | None = None,
    factor_exposures: Mapping[str, float] | None = None,
    factor_caps: Mapping[str, float] | None = None,
    liquidity_horizon_by_instrument: Mapping[str, float] | None = None,
    available_cash: float | None = None,
    fully_invested: bool = True,
    allow_short: bool = False,
) -> PortfolioValidationResult:
    """Validate one allocation proposal and return a deterministic decision."""
    rules = validation_policy or PortfolioValidationPolicy()
    checks: list[ValidationCheck] = []
    risk_increasing = proposal.is_risk_increasing
    requires_human_approval = risk_increasing and rules.require_human_approval_for_increase

    checks.append(
        _check(
            ValidationCode.PROPOSAL_STATE_INVALID,
            proposal.approval_state
            in {ProposalState.PENDING_VALIDATION, ProposalState.DRAFT},
            f"proposal state is {proposal.approval_state.value}",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROPOSAL_EXPIRED,
            is_proposal_fresh(proposal, context.now),
            f"proposal is valid until {proposal.expires_at.isoformat()}",
        )
    )
    checks.append(
        _check(
            ValidationCode.SNAPSHOT_STALE,
            snapshot.age_seconds(context.now) <= rules.max_snapshot_age_seconds,
            f"snapshot age is {snapshot.age_seconds(context.now):.1f}s",
        )
    )
    checks.append(
        _check(
            ValidationCode.SNAPSHOT_STALE,
            (context.now - proposal.created_at).total_seconds()
            <= rules.max_proposal_age_seconds,
            "proposal age check",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROVENANCE_MISMATCH,
            proposal.portfolio_snapshot_id == snapshot.snapshot_id,
            "proposal portfolio snapshot reference",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROVENANCE_MISMATCH,
            proposal.exposure_snapshot_id == exposure.exposure_snapshot_id,
            "proposal exposure snapshot reference",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROVENANCE_MISMATCH,
            proposal.risk_budget_proposal_id == budget.budget_id,
            "proposal risk budget reference",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROVENANCE_MISMATCH,
            exposure.portfolio_snapshot_id == snapshot.snapshot_id,
            "exposure and portfolio snapshot reference",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROVENANCE_MISMATCH,
            budget.portfolio_snapshot_id == snapshot.snapshot_id,
            "budget and portfolio snapshot reference",
        )
    )
    checks.append(
        _check(
            ValidationCode.DUPLICATE_IDEMPOTENCY_KEY,
            proposal.idempotency_key not in context.seen_idempotency_keys,
            "proposal idempotency key has already been consumed",
        )
    )

    if rules.require_complete_snapshot and risk_increasing:
        checks.append(
            _check(
                ValidationCode.SNAPSHOT_INCOMPLETE,
                snapshot.completeness_status.value == "complete",
                f"snapshot completeness is {snapshot.completeness_status.value}",
            )
        )
    if rules.require_reconciled and risk_increasing:
        checks.append(
            _check(
                ValidationCode.RECONCILIATION_BLOCK,
                snapshot.reconciliation_status == ReconciliationStatus.RECONCILED,
                f"snapshot reconciliation is {snapshot.reconciliation_status.value}",
            )
        )
        checks.append(
            _check(
                ValidationCode.RECONCILIATION_BLOCK,
                not any(
                    issue.blocks_new_risk for issue in context.reconciliation_issues
                ),
                "open reconciliation issue blocks new risk",
            )
        )
    if risk_increasing:
        checks.append(
            _check(
                ValidationCode.DATA_QUALITY_DEGRADED,
                snapshot.data_quality_status == DataQuality.VALID,
                f"snapshot data quality is {snapshot.data_quality_status.value}",
            )
        )
        checks.append(
            _check(
                ValidationCode.SNAPSHOT_INVALID,
                snapshot.is_usable_for_risk_increase,
                "snapshot is not usable for risk increase",
            )
        )
        checks.append(
            _check(
                ValidationCode.SNAPSHOT_INCOMPLETE,
                exposure.is_complete,
                "exposure snapshot is incomplete",
            )
        )
    elif not rules.allow_risk_reducing_when_degraded:
        checks.append(
            _check(
                ValidationCode.SNAPSHOT_INCOMPLETE,
                snapshot.is_usable_for_risk_increase,
                "risk-reducing proposals are not permitted in the current degraded state",
            )
        )

    if context.kill_switch_active and rules.require_kill_switch_clear and risk_increasing:
        checks.append(
            _check(
                ValidationCode.KILL_SWITCH_ACTIVE,
                False,
                "global kill switch is active; only risk-reducing actions are permitted",
            )
        )
    if context.watchdog_halted and rules.require_watchdog_clear and risk_increasing:
        checks.append(
            _check(
                ValidationCode.WATCHDOG_HALTED,
                False,
                "watchdog is halted; risk-increasing actions are blocked",
            )
        )

    checks.extend(_section_checks(proposal, snapshot, context, rules))

    checks.append(
        _check(
            ValidationCode.SOLVER_FAILED,
            proposal.solver_status
            in {SolverStatus.OPTIMAL, SolverStatus.APPROXIMATE},
            f"proposal solver status is {proposal.solver_status.value}",
        )
    )
    checks.append(
        _check(
            ValidationCode.SCHEMA_INVALID,
            proposal.proposed_targets
            and all(
                target.proposed_weight >= -1e-9
                and target.proposed_weight <= 1.0 + 1e-9
                for target in proposal.proposed_targets
            ),
            "proposal targets must have valid weights",
        )
    )
    budget_validation = validate_budget_reconciliation(budget, policy)
    checks.append(
        _check(
            ValidationCode.BUDGET_RECONCILIATION_FAILED,
            budget_validation.passed,
            "; ".join(budget_validation.messages) or "budget reconciles",
        )
    )

    if rules.require_stress_pass:
        if stress_report is None:
            checks.append(
                _check(
                    ValidationCode.MISSING_STRESS_REPORT,
                    False,
                    "stress report is required but was not supplied",
                )
            )
        else:
            checks.append(
                _check(
                    ValidationCode.PROVENANCE_MISMATCH,
                    stress_report.portfolio_snapshot_id == snapshot.snapshot_id
                    and stress_report.exposure_snapshot_id == exposure.exposure_snapshot_id,
                    "stress report references match the proposal inputs",
                )
            )
            checks.append(
                _check(
                    ValidationCode.PROVENANCE_MISMATCH,
                    proposal.stress_report_id is None
                    or proposal.stress_report_id == stress_report.report_id,
                    "proposal stress reference matches the supplied report",
                )
            )
            checks.append(
                _check(
                    ValidationCode.STRESS_FAILED,
                    stress_report.passed and proposal.stress_passed,
                    "stress suite did not pass",
                )
            )

    if proposal.constraint_results:
        hard_failures = [
            check
            for check in proposal.constraint_results
            if check.hard and not check.passed
        ]
        checks.append(
            _check(
                ValidationCode.HARD_CONSTRAINT_VIOLATION,
                not hard_failures,
                "proposal-reported hard constraints failed: "
                + ", ".join(check.name for check in hard_failures),
            )
        )
    checks.extend(
        _independent_constraint_checks(
            proposal=proposal,
            snapshot=snapshot,
            policy=policy,
            risk_report=risk_report,
            covariance=covariance,
            section_by_instrument=section_by_instrument,
            expected_returns=expected_returns,
            eligible_instruments=eligible_instruments,
            factor_exposures=factor_exposures,
            factor_caps=factor_caps,
            liquidity_horizon_by_instrument=liquidity_horizon_by_instrument,
            available_cash=available_cash,
            fully_invested=fully_invested,
            allow_short=allow_short,
            stress_passed=stress_report.passed if stress_report is not None else False,
            risk_increasing=risk_increasing,
        )
    )

    if requires_human_approval and not context.human_approval_ref:
        checks.append(
            _check(
                ValidationCode.APPROVAL_REQUIRED,
                False,
                "risk-increasing proposal requires a human approval reference",
                hard=False,
            )
        )
    if context.human_approval_ref:
        checks.append(
            _check(
                "HUMAN_APPROVAL_PRESENT",
                True,
                f"human approval reference {context.human_approval_ref}",
                hard=False,
            )
        )
    return _result(
        proposal_id=proposal.proposal_id,
        checks=checks,
        context=context,
        policy=rules,
        requires_human_approval=requires_human_approval,
    )


def validate_rebalance_proposal(
    rebalance: RebalanceProposal,
    allocation: AllocationProposal,
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    budget: RiskBudgetProposal,
    policy: RiskBudgetPolicy,
    context: ValidationContext,
    *,
    validation_policy: PortfolioValidationPolicy | None = None,
    stress_report: StressReport | None = None,
    risk_report: RiskReport | None = None,
    covariance: Sequence[Sequence[float]] | None = None,
    section_by_instrument: Mapping[str, Section] | None = None,
    expected_returns: Sequence[float] | None = None,
    eligible_instruments: Sequence[str] | None = None,
    factor_exposures: Mapping[str, float] | None = None,
    factor_caps: Mapping[str, float] | None = None,
    liquidity_horizon_by_instrument: Mapping[str, float] | None = None,
    available_cash: float | None = None,
    fully_invested: bool = True,
    allow_short: bool = False,
) -> PortfolioValidationResult:
    """Validate a rebalance proposal against its parent allocation proposal."""
    rules = validation_policy or PortfolioValidationPolicy()
    base = validate_allocation_proposal(
        allocation,
        snapshot,
        exposure,
        budget,
        policy,
        context,
        validation_policy=rules,
        stress_report=stress_report,
        risk_report=risk_report,
        covariance=covariance,
        section_by_instrument=section_by_instrument,
        expected_returns=expected_returns,
        eligible_instruments=eligible_instruments,
        factor_exposures=factor_exposures,
        factor_caps=factor_caps,
        liquidity_horizon_by_instrument=liquidity_horizon_by_instrument,
        available_cash=available_cash,
        fully_invested=fully_invested,
        allow_short=allow_short,
    )
    checks = list(base.checks)
    checks.append(
        _check(
            ValidationCode.PROPOSAL_STATE_INVALID,
            rebalance.allocation_proposal_id == allocation.proposal_id,
            "rebalance does not reference the supplied allocation proposal",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROPOSAL_STATE_INVALID,
            rebalance.validation_state
            in {ProposalState.PENDING_VALIDATION, ProposalState.DRAFT},
            f"rebalance state is {rebalance.validation_state.value}",
        )
    )
    checks.append(
        _check(
            ValidationCode.PROPOSAL_EXPIRED,
            is_proposal_fresh(rebalance, context.now),
            "rebalance freshness",
        )
    )
    expected_changes = {
        target.instrument_id: target
        for target in allocation.proposed_targets
        if target.action != ProposalAction.HOLD
    }
    actual_changes = {change.instrument_id: change for change in rebalance.changes}
    mismatch = set(expected_changes) != set(actual_changes)
    if not mismatch:
        for instrument_id, target in expected_changes.items():
            change = actual_changes[instrument_id]
            if abs(change.delta_exposure - target.delta_exposure) > max(
                0.01, abs(target.delta_exposure) * 1e-6
            ):
                mismatch = True
                break
    checks.append(
        _check(
            ValidationCode.REBALANCE_MISMATCH,
            not mismatch,
            "rebalance changes do not match the allocation targets",
        )
    )
    if rebalance.is_risk_increasing:
        cash = available_cash
        if cash is None:
            cash = snapshot.capital_summary.available_to_trade
        required = rebalance.required_capital.get("total", 0.0)
        checks.append(
            _check(
                ValidationCode.INSUFFICIENT_CAPITAL,
                cash is not None and required <= cash + 1e-6,
                f"required capital {required:.2f} versus available {cash}",
            )
        )
    hard_failures = [
        check for check in checks if check.hard and not check.passed
    ]
    approval_failures = [
        check for check in checks if not check.hard and not check.passed
    ]
    if hard_failures:
        decision = ValidationDecision.REJECT
    elif approval_failures:
        decision = ValidationDecision.REQUIRE_REVIEW
    else:
        decision = ValidationDecision.ACCEPT
    authorized = decision == ValidationDecision.ACCEPT
    reason_codes = tuple(
        dict.fromkeys(check.code for check in checks if not check.passed)
    )
    if authorized:
        reason_codes += (ValidationCode.AUTHORIZED.value,)
    authorization_id = (
        fingerprint(
            "auth",
            VALIDATOR_VERSION,
            rebalance.rebalance_id,
            allocation.proposal_id,
            rules.policy_version,
            tuple(checks),
            context.now,
        )
        if authorized
        else None
    )
    return PortfolioValidationResult(
        decision=decision,
        proposal_id=rebalance.rebalance_id,
        reason_codes=reason_codes,
        checks=tuple(checks),
        validated_at=context.now,
        policy_version=rules.policy_version,
        authorized=authorized,
        requires_human_approval=allocation.requires_human_approval,
        authorization_id=authorization_id,
        execution_permitted=authorized,
    )


def validation_result_to_dict(result: PortfolioValidationResult) -> Mapping[str, object]:
    return {
        "decision": result.decision.value,
        "proposal_id": result.proposal_id,
        "reason_codes": result.reason_codes,
        "authorized": result.authorized,
        "requires_human_approval": result.requires_human_approval,
        "authorization_id": result.authorization_id,
        "policy_version": result.policy_version,
        "validated_at": result.validated_at.isoformat(),
    }


__all__ = [
    "VALIDATOR_VERSION",
    "ExecutionMode",
    "PortfolioValidationPolicy",
    "PortfolioValidationResult",
    "ValidationCode",
    "ValidationContext",
    "validate_allocation_proposal",
    "validate_rebalance_proposal",
    "validation_result_to_dict",
]
