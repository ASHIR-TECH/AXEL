"""
P11-OBSERVABILITY: deterministic metrics, audit events, and alerts.

This module performs no network or database I/O. It turns portfolio domain
objects into stable metric/event records that an existing monitoring pipeline
can export. Event IDs are deterministic so replaying the same logical state
does not duplicate audit records.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from axel.core.types import Section
from axel.portfolio.proposal import AllocationProposal, RebalanceProposal
from axel.portfolio.serialization import fingerprint, to_jsonable
from axel.portfolio.stress import StressReport
from axel.portfolio.types import (
    ExposureSnapshot,
    IssueSeverity,
    PortfolioAlert,
    PortfolioSnapshot,
    ReconciliationIssue,
    require_aware,
    utc_now,
)
from axel.portfolio.validator import PortfolioValidationResult


class MetricUnit(str, Enum):
    RATIO = "ratio"
    CURRENCY = "currency"
    SECONDS = "seconds"
    COUNT = "count"
    BOOLEAN = "boolean"
    BPS = "basis_points"


@dataclass(frozen=True)
class PortfolioMetric:
    name: str
    value: float
    unit: MetricUnit
    labels: Mapping[str, str] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("metric name must be non-empty")
        object.__setattr__(self, "observed_at", require_aware(self.observed_at, "observed_at"))
        if not isinstance(self.unit, MetricUnit):
            object.__setattr__(self, "unit", MetricUnit(self.unit))


class EventType(str, Enum):
    SNAPSHOT_BUILT = "portfolio.snapshot.built"
    EXPOSURE_BUILT = "portfolio.exposure.built"
    RECONCILIATION_BLOCKED = "portfolio.reconciliation.blocked"
    ALLOCATION_PROPOSED = "portfolio.allocation.proposed"
    REBALANCE_PROPOSED = "portfolio.rebalance.proposed"
    PROPOSAL_DEFERRED = "portfolio.proposal.deferred"
    PROPOSAL_VALIDATED = "portfolio.proposal.validated"
    STRESS_COMPLETED = "portfolio.stress.completed"
    ALERT_RAISED = "portfolio.alert.raised"


@dataclass(frozen=True)
class PortfolioEvent:
    event_id: str
    event_type: EventType
    occurred_at: datetime
    aggregate_id: str
    payload: Mapping[str, Any]
    schema_version: str = "p11-event-1"

    def __post_init__(self) -> None:
        if not self.event_id or not self.aggregate_id:
            raise ValueError("event_id and aggregate_id must be non-empty")
        object.__setattr__(self, "occurred_at", require_aware(self.occurred_at, "occurred_at"))
        if not isinstance(self.event_type, EventType):
            object.__setattr__(self, "event_type", EventType(self.event_type))

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "aggregate_id": self.aggregate_id,
            "schema_version": self.schema_version,
            "payload": to_jsonable(self.payload),
        }


def _event(
    event_type: EventType,
    aggregate_id: str,
    occurred_at: datetime,
    payload: Mapping[str, Any],
) -> PortfolioEvent:
    return PortfolioEvent(
        event_id=fingerprint("evt", event_type.value, aggregate_id, occurred_at, payload),
        event_type=event_type,
        occurred_at=occurred_at,
        aggregate_id=aggregate_id,
        payload=payload,
    )


def snapshot_metrics(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot | None = None,
    *,
    now: datetime | None = None,
) -> tuple[PortfolioMetric, ...]:
    """Metrics operators need to judge whether a snapshot is safe to consume."""
    current = require_aware(now or utc_now(), "now")
    labels = {"snapshot_id": snapshot.snapshot_id, "base_currency": snapshot.base_currency}
    metrics: list[PortfolioMetric] = [
        PortfolioMetric(
            "axel_portfolio_snapshot_age_seconds",
            max(0.0, snapshot.age_seconds(current)),
            MetricUnit.SECONDS,
            labels,
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_positions",
            float(len(snapshot.positions)),
            MetricUnit.COUNT,
            labels,
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_open_orders",
            float(len(snapshot.open_orders)),
            MetricUnit.COUNT,
            labels,
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_risk_increase_usable",
            1.0 if snapshot.is_usable_for_risk_increase else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_data_quality_valid",
            1.0 if snapshot.data_quality_status.value == "valid" else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_reconciled",
            1.0 if snapshot.reconciliation_status.value == "reconciled" else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            current,
        ),
    ]
    if snapshot.capital_summary.equity_value is not None:
        metrics.append(
            PortfolioMetric(
                "axel_portfolio_equity",
                snapshot.capital_summary.equity_value,
                MetricUnit.CURRENCY,
                labels,
                current,
            )
        )
    if snapshot.capital_summary.free_equity is not None:
        metrics.append(
            PortfolioMetric(
                "axel_portfolio_free_equity",
                snapshot.capital_summary.free_equity or 0.0,
                MetricUnit.CURRENCY,
                labels,
                current,
            )
        )
    if exposure is not None:
        exposure_labels = {
            **labels,
            "exposure_snapshot_id": exposure.exposure_snapshot_id,
        }
        metrics.extend(
            (
                PortfolioMetric(
                    "axel_portfolio_exposure_coverage_ratio",
                    exposure.coverage_ratio,
                    MetricUnit.RATIO,
                    exposure_labels,
                    current,
                ),
                PortfolioMetric(
                    "axel_portfolio_exposure_failures",
                    float(len(exposure.failures)),
                    MetricUnit.COUNT,
                    exposure_labels,
                    current,
                ),
                PortfolioMetric(
                    "axel_portfolio_exposure_complete",
                    1.0 if exposure.is_complete else 0.0,
                    MetricUnit.BOOLEAN,
                    exposure_labels,
                    current,
                ),
            )
        )
        for section_value, section_exposure in sorted(exposure.section_exposures.items()):
            section_labels = {**exposure_labels, "section": section_value}
            metrics.append(
                PortfolioMetric(
                    "axel_portfolio_section_gross_exposure",
                    section_exposure.gross_market_value,
                    MetricUnit.CURRENCY,
                    section_labels,
                    current,
                )
            )
            metrics.append(
                PortfolioMetric(
                    "axel_portfolio_section_net_exposure",
                    section_exposure.net_market_value,
                    MetricUnit.CURRENCY,
                    section_labels,
                    current,
                )
            )
    return tuple(metrics)


def proposal_metrics(
    proposal: AllocationProposal | RebalanceProposal,
    *,
    now: datetime | None = None,
) -> tuple[PortfolioMetric, ...]:
    current = require_aware(now or utc_now(), "now")
    proposal_id = (
        proposal.proposal_id
        if isinstance(proposal, AllocationProposal)
        else proposal.rebalance_id
    )
    labels = {"proposal_id": proposal_id}
    age = max(0.0, (current - proposal.created_at).total_seconds())
    remaining = max(0.0, (proposal.expires_at - current).total_seconds())
    action = proposal.action.value
    metrics = [
        PortfolioMetric(
            "axel_portfolio_proposal_age_seconds", age, MetricUnit.SECONDS, labels, current
        ),
        PortfolioMetric(
            "axel_portfolio_proposal_ttl_seconds",
            remaining,
            MetricUnit.SECONDS,
            labels,
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_proposal_action_increase",
            1.0 if action == "increase" else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_proposal_expired",
            1.0 if remaining <= 0.0 else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            current,
        ),
    ]
    if isinstance(proposal, AllocationProposal):
        metrics.extend(
            (
                PortfolioMetric(
                    "axel_portfolio_proposal_turnover",
                    proposal.estimated_turnover,
                    MetricUnit.RATIO,
                    labels,
                    current,
                ),
                PortfolioMetric(
                    "axel_portfolio_proposal_estimated_cost",
                    proposal.estimated_costs.total_cost,
                    MetricUnit.CURRENCY,
                    labels,
                    current,
                ),
                PortfolioMetric(
                    "axel_portfolio_proposal_stress_passed",
                    1.0 if proposal.stress_passed else 0.0,
                    MetricUnit.BOOLEAN,
                    labels,
                    current,
                ),
            )
        )
    return tuple(metrics)


def validation_metrics(
    result: PortfolioValidationResult,
) -> tuple[PortfolioMetric, ...]:
    labels = {
        "proposal_id": result.proposal_id,
        "decision": result.decision.value,
    }
    return (
        PortfolioMetric(
            "axel_portfolio_validation_accept",
            1.0 if result.decision.value == "accept" else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            result.validated_at,
        ),
        PortfolioMetric(
            "axel_portfolio_validation_reject",
            1.0 if result.decision.value == "reject" else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            result.validated_at,
        ),
        PortfolioMetric(
            "axel_portfolio_validation_checks_failed",
            float(sum(1 for check in result.checks if not check.passed)),
            MetricUnit.COUNT,
            labels,
            result.validated_at,
        ),
    )


def stress_metrics(report: StressReport) -> tuple[PortfolioMetric, ...]:
    labels = {"stress_report_id": report.report_id}
    metrics = [
        PortfolioMetric(
            "axel_portfolio_stress_passed",
            1.0 if report.passed else 0.0,
            MetricUnit.BOOLEAN,
            labels,
            report.created_at,
        ),
        PortfolioMetric(
            "axel_portfolio_stress_worst_loss",
            report.worst_case_loss,
            MetricUnit.CURRENCY,
            labels,
            report.created_at,
        ),
        PortfolioMetric(
            "axel_portfolio_stress_scenarios",
            float(len(report.results)),
            MetricUnit.COUNT,
            labels,
            report.created_at,
        ),
    ]
    for result in report.results:
        scenario_labels = {
            **labels,
            "scenario_id": result.scenario_id,
            "family": result.family.value,
        }
        metrics.append(
            PortfolioMetric(
                "axel_portfolio_stress_scenario_loss",
                result.total_loss,
                MetricUnit.CURRENCY,
                scenario_labels,
                report.created_at,
            )
        )
        metrics.append(
            PortfolioMetric(
                "axel_portfolio_stress_scenario_passed",
                1.0 if result.passed else 0.0,
                MetricUnit.BOOLEAN,
                scenario_labels,
                report.created_at,
            )
        )
    return tuple(metrics)


def reconciliation_metrics(
    issues: Sequence[ReconciliationIssue],
    *,
    now: datetime | None = None,
) -> tuple[PortfolioMetric, ...]:
    current = require_aware(now or utc_now(), "now")
    open_issues = [issue for issue in issues if issue.is_open]
    blocking = [issue for issue in open_issues if issue.blocks_new_risk]
    metrics = [
        PortfolioMetric(
            "axel_portfolio_reconciliation_open_issues",
            float(len(open_issues)),
            MetricUnit.COUNT,
            {},
            current,
        ),
        PortfolioMetric(
            "axel_portfolio_reconciliation_blocking_issues",
            float(len(blocking)),
            MetricUnit.COUNT,
            {},
            current,
        ),
    ]
    for severity in IssueSeverity:
        metrics.append(
            PortfolioMetric(
                "axel_portfolio_reconciliation_issues",
                float(sum(1 for issue in open_issues if issue.severity == severity)),
                MetricUnit.COUNT,
                {"severity": severity.value},
                current,
            )
        )
    return tuple(metrics)


def snapshot_built_event(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot | None = None,
) -> PortfolioEvent:
    payload: dict[str, Any] = {
        "snapshot_version": snapshot.snapshot_version,
        "source_cutoff_time": snapshot.source_cutoff_time,
        "data_quality_status": snapshot.data_quality_status.value,
        "completeness_status": snapshot.completeness_status.value,
        "reconciliation_status": snapshot.reconciliation_status.value,
        "position_count": len(snapshot.positions),
        "open_order_count": len(snapshot.open_orders),
        "is_usable_for_risk_increase": snapshot.is_usable_for_risk_increase,
    }
    if exposure is not None:
        payload["exposure_snapshot_id"] = exposure.exposure_snapshot_id
        payload["coverage_ratio"] = exposure.coverage_ratio
        payload["failure_count"] = len(exposure.failures)
    return _event(
        EventType.SNAPSHOT_BUILT,
        snapshot.snapshot_id,
        snapshot.snapshot_time,
        payload,
    )


def proposal_event(
    proposal: AllocationProposal | RebalanceProposal,
) -> PortfolioEvent:
    if isinstance(proposal, AllocationProposal):
        aggregate_id = proposal.proposal_id
        event_type = (
            EventType.PROPOSAL_DEFERRED
            if proposal.action.value == "defer"
            else EventType.ALLOCATION_PROPOSED
        )
        payload: dict[str, Any] = {
            "portfolio_snapshot_id": proposal.portfolio_snapshot_id,
            "exposure_snapshot_id": proposal.exposure_snapshot_id,
            "method": proposal.method.value,
            "solver_status": proposal.solver_status.value,
            "action": proposal.action.value,
            "approval_state": proposal.approval_state.value,
            "stress_passed": proposal.stress_passed,
            "estimated_turnover": proposal.estimated_turnover,
            "estimated_cost": proposal.estimated_costs.total_cost,
            "target_count": len(proposal.proposed_targets),
        }
    else:
        aggregate_id = proposal.rebalance_id
        event_type = EventType.REBALANCE_PROPOSED
        payload = {
            "allocation_proposal_id": proposal.allocation_proposal_id,
            "portfolio_snapshot_id": proposal.portfolio_snapshot_id,
            "action": proposal.action.value,
            "validation_state": proposal.validation_state.value,
            "change_count": len(proposal.changes),
            "required_capital": proposal.required_capital,
        }
    return _event(event_type, aggregate_id, proposal.created_at, payload)


def validation_event(result: PortfolioValidationResult) -> PortfolioEvent:
    return _event(
        EventType.PROPOSAL_VALIDATED,
        result.proposal_id,
        result.validated_at,
        {
            "decision": result.decision.value,
            "reason_codes": result.reason_codes,
            "authorized": result.authorized,
            "authorization_id": result.authorization_id,
            "policy_version": result.policy_version,
            "requires_human_approval": result.requires_human_approval,
        },
    )


def stress_event(report: StressReport) -> PortfolioEvent:
    return _event(
        EventType.STRESS_COMPLETED,
        report.report_id,
        report.created_at,
        {
            "portfolio_snapshot_id": report.portfolio_snapshot_id,
            "exposure_snapshot_id": report.exposure_snapshot_id,
            "model_version": report.model_version,
            "passed": report.passed,
            "worst_case_loss": report.worst_case_loss,
            "worst_scenario_id": report.worst_scenario_id,
            "scenario_count": len(report.results),
        },
    )


def alert_for_blocking_issue(
    issue: ReconciliationIssue,
    *,
    now: datetime | None = None,
) -> PortfolioAlert:
    current = require_aware(now or utc_now(), "now")
    return PortfolioAlert(
        alert_id=fingerprint("alert", issue.issue_id, issue.status.value, current),
        severity=issue.severity,
        code="RECONCILIATION_BLOCKING_ISSUE",
        message=(
            f"{issue.entity_type} {issue.entity_ref} differs between "
            f"{', '.join(issue.source_systems)}; new risk is blocked."
        ),
        detected_at=current,
        context={
            "issue_id": issue.issue_id,
            "entity_type": issue.entity_type,
            "entity_ref": issue.entity_ref,
            "difference": issue.difference,
            "unit": issue.unit,
        },
    )


def alert_for_stress_failure(
    report: StressReport,
    *,
    now: datetime | None = None,
) -> PortfolioAlert:
    current = require_aware(now or utc_now(), "now")
    return PortfolioAlert(
        alert_id=fingerprint("alert", report.report_id, "stress_failed", current),
        severity=IssueSeverity.CRITICAL,
        code="STRESS_SUITE_FAILED",
        message=(
            f"Stress suite {report.report_id} failed; worst scenario "
            f"{report.worst_scenario_id} lost {report.worst_case_loss:.2f}."
        ),
        detected_at=current,
        context={
            "stress_report_id": report.report_id,
            "worst_case_loss": report.worst_case_loss,
            "worst_scenario_id": report.worst_scenario_id,
        },
    )


def alert_for_validation_rejection(
    result: PortfolioValidationResult,
    *,
    now: datetime | None = None,
) -> PortfolioAlert | None:
    if result.decision.value != "reject":
        return None
    current = require_aware(now or utc_now(), "now")
    return PortfolioAlert(
        alert_id=fingerprint(
            "alert", result.proposal_id, result.decision.value, result.reason_codes, current
        ),
        severity=IssueSeverity.CRITICAL,
        code="PROPOSAL_REJECTED",
        message=(
            f"Proposal {result.proposal_id} rejected: "
            f"{', '.join(result.reason_codes) or 'no reason codes'}."
        ),
        detected_at=current,
        context={
            "proposal_id": result.proposal_id,
            "decision": result.decision.value,
            "reason_codes": result.reason_codes,
            "policy_version": result.policy_version,
        },
    )


def section_metrics(
    exposure: ExposureSnapshot,
    *,
    now: datetime | None = None,
) -> tuple[PortfolioMetric, ...]:
    current = require_aware(now or utc_now(), "now")
    metrics: list[PortfolioMetric] = []
    for section in Section:
        section_exposure = exposure.section(section)
        if section_exposure is None:
            continue
        labels = {"section": section.value}
        metrics.extend(
            (
                PortfolioMetric(
                    "axel_portfolio_section_instruments",
                    float(section_exposure.instrument_count),
                    MetricUnit.COUNT,
                    labels,
                    current,
                ),
                PortfolioMetric(
                    "axel_portfolio_section_margin",
                    section_exposure.margin_requirement or 0.0,
                    MetricUnit.CURRENCY,
                    labels,
                    current,
                ),
                PortfolioMetric(
                    "axel_portfolio_section_max_loss",
                    section_exposure.max_loss or 0.0,
                    MetricUnit.CURRENCY,
                    labels,
                    current,
                ),
            )
        )
    return tuple(metrics)


def render_prometheus(metrics: Sequence[PortfolioMetric]) -> str:
    """Render metrics in Prometheus text exposition format.

    Labels are sorted and escaped deterministically. This function does not
    emit HELP/TYPE metadata because metric ownership belongs to the deployment.
    """
    lines: list[str] = []
    for metric in metrics:
        labels = ",".join(
            f'{key}="{_escape_label(value)}"'
            for key, value in sorted(metric.labels.items())
        )
        suffix = f"{{{labels}}}" if labels else ""
        lines.append(f"{metric.name}{suffix} {metric.value:.10g}")
    return "\n".join(lines) + ("\n" if lines else "")


def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


__all__ = [
    "EventType",
    "MetricUnit",
    "PortfolioEvent",
    "PortfolioMetric",
    "alert_for_blocking_issue",
    "alert_for_stress_failure",
    "alert_for_validation_rejection",
    "proposal_event",
    "proposal_metrics",
    "reconciliation_metrics",
    "render_prometheus",
    "section_metrics",
    "snapshot_built_event",
    "snapshot_metrics",
    "stress_event",
    "stress_metrics",
    "validation_event",
    "validation_metrics",
]
