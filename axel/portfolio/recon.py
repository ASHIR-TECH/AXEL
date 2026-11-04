"""
P11-RECON: reconciliation between AXEL's internal state and authoritative sources.

Reconciliation is a safety control, not a reporting nicety. A critical
unresolved discrepancy blocks risk-increasing proposals that depend on the
affected state. Unknown order states remain UNKNOWN; they are never assumed
cancelled, filled, or zero.

Issue IDs are deterministic for the same logical discrepancy, which makes
duplicate detection idempotent. Database uniqueness constraints remain the
authoritative deduplication mechanism.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from axel.portfolio.serialization import fingerprint
from axel.portfolio.types import (
    AccountCapital,
    CommitmentRecord,
    IssueSeverity,
    IssueStatus,
    OpenOrderRecord,
    PositionRecord,
    ReconciliationIssue,
    ReconciliationStatus,
    require_aware,
    require_finite,
)


@dataclass(frozen=True)
class ReconciliationTolerance:
    """Approved tolerance bands. Zero tolerance is not realistic for FX values."""

    absolute: float = 0.01
    relative: float = 1e-3
    quantity_absolute: float = 1e-8
    quantity_relative: float = 1e-8

    def __post_init__(self) -> None:
        for name in ("absolute", "relative", "quantity_absolute", "quantity_relative"):
            value = getattr(self, name)
            require_finite(value, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

    def amount_within(self, expected: float, observed: float) -> bool:
        threshold = max(
            self.absolute,
            self.relative * max(abs(expected), abs(observed)),
        )
        return abs(expected - observed) <= threshold

    def quantity_within(self, expected: float, observed: float) -> bool:
        threshold = max(
            self.quantity_absolute,
            self.quantity_relative * max(abs(expected), abs(observed)),
        )
        return abs(expected - observed) <= threshold


def _issue(
    *,
    source_systems: Sequence[str],
    entity_type: str,
    entity_ref: str,
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
    unit: str,
    detected_at: datetime,
    severity: IssueSeverity,
    difference: float | None = None,
    reason: str = "",
    blocks_risk_increase: bool = True,
) -> ReconciliationIssue:
    issue_id = fingerprint(
        "rec",
        tuple(sorted(source_systems)),
        entity_type,
        entity_ref,
        expected,
        observed,
        unit,
        reason,
    )
    return ReconciliationIssue(
        issue_id=issue_id,
        source_systems=tuple(source_systems),
        entity_type=entity_type,
        entity_ref=entity_ref,
        expected_value=dict(expected),
        observed_value=dict(observed),
        unit=unit,
        detected_at=require_aware(detected_at, "detected_at"),
        severity=severity,
        difference=difference,
        blocks_risk_increase=blocks_risk_increase,
    )


def reconcile_accounts(
    internal: Sequence[AccountCapital],
    authoritative: Sequence[AccountCapital],
    now: datetime,
    *,
    tolerance: ReconciliationTolerance | None = None,
    severity: IssueSeverity = IssueSeverity.CRITICAL,
) -> tuple[ReconciliationIssue, ...]:
    """Compare internal account balances with authoritative venue balances."""
    bands = tolerance or ReconciliationTolerance()
    internal_by_id = {account.account_id: account for account in internal}
    authoritative_by_id = {account.account_id: account for account in authoritative}
    issues: list[ReconciliationIssue] = []

    for account_id in sorted(internal_by_id.keys() | authoritative_by_id.keys()):
        expected = internal_by_id.get(account_id)
        observed = authoritative_by_id.get(account_id)
        if expected is None:
            assert observed is not None
            issues.append(
                _issue(
                    source_systems=("internal", observed.source),
                    entity_type="account",
                    entity_ref=account_id,
                    expected={},
                    observed={"account_id": observed.account_id, "currency": observed.currency},
                    unit="account",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative account is missing from internal state",
                )
            )
            continue
        if observed is None:
            issues.append(
                _issue(
                    source_systems=(expected.source, "authoritative"),
                    entity_type="account",
                    entity_ref=account_id,
                    expected={
                        "cash_balance": expected.cash_balance,
                        "currency": expected.currency,
                    },
                    observed={},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative account state is unavailable",
                )
            )
            continue

        if expected.currency != observed.currency:
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="account",
                    entity_ref=account_id,
                    expected={"currency": expected.currency},
                    observed={"currency": observed.currency},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    reason="account currency mismatch",
                )
            )
            continue

        for field_name in (
            "cash_balance",
            "equity_value",
            "account_equity",
            "reserved_order_capacity",
            "margin_requirement",
        ):
            expected_value = getattr(expected, field_name)
            observed_value = getattr(observed, field_name)
            if not bands.amount_within(expected_value, observed_value):
                issues.append(
                    _issue(
                        source_systems=(expected.source, observed.source),
                        entity_type="account",
                        entity_ref=account_id,
                        expected={field_name: expected_value, "currency": expected.currency},
                        observed={field_name: observed_value, "currency": observed.currency},
                        unit="currency",
                        detected_at=now,
                        severity=severity,
                        difference=expected_value - observed_value,
                        reason=f"{field_name} differs beyond tolerance",
                    )
                )
    return tuple(issues)


def reconcile_positions(
    internal: Sequence[PositionRecord],
    authoritative: Sequence[PositionRecord],
    now: datetime,
    *,
    tolerance: ReconciliationTolerance | None = None,
    severity: IssueSeverity = IssueSeverity.CRITICAL,
) -> tuple[ReconciliationIssue, ...]:
    """Compare positions by (instrument_id, venue)."""
    bands = tolerance or ReconciliationTolerance()
    internal_by_key = {(p.instrument_id, p.venue): p for p in internal}
    authoritative_by_key = {(p.instrument_id, p.venue): p for p in authoritative}
    issues: list[ReconciliationIssue] = []

    for key in sorted(internal_by_key.keys() | authoritative_by_key.keys()):
        expected = internal_by_key.get(key)
        observed = authoritative_by_key.get(key)
        entity_ref = f"{key[0]}@{key[1]}"
        if expected is None:
            assert observed is not None
            issues.append(
                _issue(
                    source_systems=("internal", observed.source),
                    entity_type="position",
                    entity_ref=entity_ref,
                    expected={},
                    observed={
                        "quantity": observed.quantity,
                        "market_value": observed.market_value,
                        "currency": observed.currency,
                    },
                    unit="quantity",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative position is missing from internal state",
                )
            )
            continue
        if observed is None:
            issues.append(
                _issue(
                    source_systems=(expected.source, "authoritative"),
                    entity_type="position",
                    entity_ref=entity_ref,
                    expected={
                        "quantity": expected.quantity,
                        "market_value": expected.market_value,
                        "currency": expected.currency,
                    },
                    observed={},
                    unit="quantity",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative position state is unavailable",
                )
            )
            continue

        if expected.reconciliation_status in {
            ReconciliationStatus.UNKNOWN,
            ReconciliationStatus.UNAVAILABLE,
            ReconciliationStatus.MISMATCH,
        }:
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="position",
                    entity_ref=entity_ref,
                    expected={"reconciliation_status": expected.reconciliation_status.value},
                    observed={"reconciliation_status": observed.reconciliation_status.value},
                    unit="state",
                    detected_at=now,
                    severity=severity,
                    reason="internal position is not reconciled",
                )
            )

        if expected.currency != observed.currency:
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="position",
                    entity_ref=entity_ref,
                    expected={"currency": expected.currency},
                    observed={"currency": observed.currency},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    reason="position currency mismatch",
                )
            )
            continue

        if not bands.quantity_within(expected.quantity, observed.quantity):
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="position",
                    entity_ref=entity_ref,
                    expected={"quantity": expected.quantity},
                    observed={"quantity": observed.quantity},
                    unit="quantity",
                    detected_at=now,
                    severity=severity,
                    difference=expected.quantity - observed.quantity,
                    reason="position quantity differs beyond tolerance",
                )
            )
        if not bands.amount_within(expected.market_value, observed.market_value):
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="position",
                    entity_ref=entity_ref,
                    expected={
                        "market_value": expected.market_value,
                        "currency": expected.currency,
                    },
                    observed={
                        "market_value": observed.market_value,
                        "currency": observed.currency,
                    },
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    difference=expected.market_value - observed.market_value,
                    reason="position market value differs beyond tolerance",
                )
            )
    return tuple(issues)


def reconcile_orders(
    internal: Sequence[OpenOrderRecord],
    authoritative: Sequence[OpenOrderRecord],
    now: datetime,
    *,
    tolerance: ReconciliationTolerance | None = None,
    severity: IssueSeverity = IssueSeverity.CRITICAL,
) -> tuple[ReconciliationIssue, ...]:
    """Compare open orders by order_id. Unknown states are never inferred."""
    bands = tolerance or ReconciliationTolerance()
    internal_by_id = {order.order_id: order for order in internal}
    authoritative_by_id = {order.order_id: order for order in authoritative}
    issues: list[ReconciliationIssue] = []

    for order_id in sorted(internal_by_id.keys() | authoritative_by_id.keys()):
        expected = internal_by_id.get(order_id)
        observed = authoritative_by_id.get(order_id)
        if expected is None:
            assert observed is not None
            issues.append(
                _issue(
                    source_systems=("internal", observed.source),
                    entity_type="order",
                    entity_ref=order_id,
                    expected={},
                    observed={"state": observed.state, "remaining_quantity": observed.remaining_quantity},
                    unit="quantity",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative open order is missing from internal state",
                )
            )
            continue
        if observed is None:
            issues.append(
                _issue(
                    source_systems=(expected.source, "authoritative"),
                    entity_type="order",
                    entity_ref=order_id,
                    expected={"state": expected.state, "remaining_quantity": expected.remaining_quantity},
                    observed={},
                    unit="quantity",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative order state is unavailable",
                )
            )
            continue

        if expected.state.lower() in {"unknown", "pending", "cancel_pending"}:
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="order",
                    entity_ref=order_id,
                    expected={"state": expected.state},
                    observed={"state": observed.state},
                    unit="state",
                    detected_at=now,
                    severity=severity,
                    reason="internal order state is not final",
                )
            )

        if expected.state.lower() != observed.state.lower():
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="order",
                    entity_ref=order_id,
                    expected={"state": expected.state},
                    observed={"state": observed.state},
                    unit="state",
                    detected_at=now,
                    severity=severity,
                    reason="order state mismatch",
                )
            )
        if not bands.quantity_within(expected.remaining_quantity, observed.remaining_quantity):
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="order",
                    entity_ref=order_id,
                    expected={"remaining_quantity": expected.remaining_quantity},
                    observed={"remaining_quantity": observed.remaining_quantity},
                    unit="quantity",
                    detected_at=now,
                    severity=severity,
                    difference=expected.remaining_quantity - observed.remaining_quantity,
                    reason="remaining order quantity differs beyond tolerance",
                )
            )
        if not bands.amount_within(expected.reserved_capacity, observed.reserved_capacity):
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="order",
                    entity_ref=order_id,
                    expected={"reserved_capacity": expected.reserved_capacity},
                    observed={"reserved_capacity": observed.reserved_capacity},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    difference=expected.reserved_capacity - observed.reserved_capacity,
                    reason="reserved order capacity differs beyond tolerance",
                )
            )
    return tuple(issues)


def reconcile_commitments(
    internal: Sequence[CommitmentRecord],
    authoritative: Sequence[CommitmentRecord],
    now: datetime,
    *,
    tolerance: ReconciliationTolerance | None = None,
    severity: IssueSeverity = IssueSeverity.WARNING,
) -> tuple[ReconciliationIssue, ...]:
    """Compare pending commitments by commitment_id."""
    bands = tolerance or ReconciliationTolerance()
    internal_by_id = {item.commitment_id: item for item in internal}
    authoritative_by_id = {item.commitment_id: item for item in authoritative}
    issues: list[ReconciliationIssue] = []

    for commitment_id in sorted(internal_by_id.keys() | authoritative_by_id.keys()):
        expected = internal_by_id.get(commitment_id)
        observed = authoritative_by_id.get(commitment_id)
        if expected is None:
            assert observed is not None
            issues.append(
                _issue(
                    source_systems=("internal", observed.source),
                    entity_type="commitment",
                    entity_ref=commitment_id,
                    expected={},
                    observed={"amount": observed.amount, "currency": observed.currency},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative commitment is missing from internal state",
                )
            )
            continue
        if observed is None:
            issues.append(
                _issue(
                    source_systems=(expected.source, "authoritative"),
                    entity_type="commitment",
                    entity_ref=commitment_id,
                    expected={"amount": expected.amount, "currency": expected.currency},
                    observed={},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    reason="authoritative commitment state is unavailable",
                )
            )
            continue
        if expected.currency != observed.currency:
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="commitment",
                    entity_ref=commitment_id,
                    expected={"currency": expected.currency},
                    observed={"currency": observed.currency},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    reason="commitment currency mismatch",
                )
            )
            continue
        if not bands.amount_within(expected.amount, observed.amount):
            issues.append(
                _issue(
                    source_systems=(expected.source, observed.source),
                    entity_type="commitment",
                    entity_ref=commitment_id,
                    expected={"amount": expected.amount, "currency": expected.currency},
                    observed={"amount": observed.amount, "currency": observed.currency},
                    unit="currency",
                    detected_at=now,
                    severity=severity,
                    difference=expected.amount - observed.amount,
                    reason="commitment amount differs beyond tolerance",
                )
            )
    return tuple(issues)


def reconcile_all(
    *,
    internal_accounts: Sequence[AccountCapital],
    authoritative_accounts: Sequence[AccountCapital],
    internal_positions: Sequence[PositionRecord],
    authoritative_positions: Sequence[PositionRecord],
    internal_orders: Sequence[OpenOrderRecord] = (),
    authoritative_orders: Sequence[OpenOrderRecord] = (),
    internal_commitments: Sequence[CommitmentRecord] = (),
    authoritative_commitments: Sequence[CommitmentRecord] = (),
    now: datetime,
    tolerance: ReconciliationTolerance | None = None,
) -> tuple[ReconciliationIssue, ...]:
    """Run all supported reconciliations and return deterministic issues."""
    issues: list[ReconciliationIssue] = []
    issues.extend(
        reconcile_accounts(
            internal_accounts, authoritative_accounts, now, tolerance=tolerance
        )
    )
    issues.extend(
        reconcile_positions(
            internal_positions, authoritative_positions, now, tolerance=tolerance
        )
    )
    issues.extend(
        reconcile_orders(internal_orders, authoritative_orders, now, tolerance=tolerance)
    )
    issues.extend(
        reconcile_commitments(
            internal_commitments, authoritative_commitments, now, tolerance=tolerance
        )
    )
    return tuple(sorted(issues, key=lambda issue: issue.issue_id))


def critical_open_issues(
    issues: Sequence[ReconciliationIssue],
) -> tuple[ReconciliationIssue, ...]:
    return tuple(
        issue
        for issue in issues
        if issue.is_open and issue.severity == IssueSeverity.CRITICAL
    )


def blocks_new_risk(
    issues: Sequence[ReconciliationIssue],
    sections: Sequence[str] | None = None,
) -> bool:
    """True when an open issue blocks risk-increasing actions.

    ``sections`` narrows the check to affected sections when known. If omitted,
    any blocking issue blocks portfolio-wide risk increases (fail closed).
    """
    if sections is None:
        return any(issue.blocks_new_risk for issue in issues)
    allowed = {str(section) for section in sections}
    return any(
        issue.blocks_new_risk
        and (
            issue.entity_type == "account"
            or issue.entity_type in {"portfolio", "global"}
            or any(token in allowed for token in issue.source_systems)
        )
        for issue in issues
    )


def reconciliation_status_from_issues(
    issues: Sequence[ReconciliationIssue],
    *,
    fallback: ReconciliationStatus = ReconciliationStatus.RECONCILED,
) -> ReconciliationStatus:
    if any(
        issue.is_open and issue.severity == IssueSeverity.CRITICAL for issue in issues
    ):
        return ReconciliationStatus.MISMATCH
    if any(issue.is_open and issue.severity == IssueSeverity.WARNING for issue in issues):
        return ReconciliationStatus.PENDING
    return fallback


def resolve_issue(
    issue: ReconciliationIssue,
    *,
    resolution_ref: str,
    responsible: str,
) -> ReconciliationIssue:
    """Mark an issue resolved with auditable evidence."""
    if not resolution_ref or not responsible:
        raise ValueError("resolution_ref and responsible are required")
    return replace(
        issue,
        status=IssueStatus.RESOLVED,
        resolution_ref=resolution_ref,
        responsible=responsible,
        blocks_risk_increase=False,
    )


def merge_issues(
    existing: Sequence[ReconciliationIssue],
    detected: Sequence[ReconciliationIssue],
) -> tuple[ReconciliationIssue, ...]:
    """Idempotent merge: resolved evidence survives a repeated detection."""
    by_id = {issue.issue_id: issue for issue in existing}
    for issue in detected:
        if issue.issue_id not in by_id:
            by_id[issue.issue_id] = issue
    return tuple(sorted(by_id.values(), key=lambda issue: issue.issue_id))


def summarize_issues(issues: Sequence[ReconciliationIssue]) -> Mapping[str, int]:
    summary = {
        "total": len(issues),
        "open": 0,
        "resolved": 0,
        "critical": 0,
        "warning": 0,
        "info": 0,
        "blocking": 0,
    }
    for issue in issues:
        if issue.is_open:
            summary["open"] += 1
        else:
            summary["resolved"] += 1
        summary[issue.severity.value] += 1
        if issue.blocks_new_risk:
            summary["blocking"] += 1
    return summary


__all__ = [
    "ReconciliationTolerance",
    "blocks_new_risk",
    "critical_open_issues",
    "merge_issues",
    "reconcile_accounts",
    "reconcile_all",
    "reconcile_commitments",
    "reconcile_orders",
    "reconcile_positions",
    "reconciliation_status_from_issues",
    "resolve_issue",
    "summarize_issues",
]
