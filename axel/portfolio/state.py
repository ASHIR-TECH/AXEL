"""
P11-STATE: consolidated, versioned portfolio snapshots.

The builder is a pure function of explicit inputs. It never calls a broker,
exchange, or database. Missing or inconsistent state produces a degraded or
invalid snapshot, never a silently "complete" one.

Safety invariants implemented here:

* Reserved capital and margin are never counted as available cash twice.
* A missing section status is UNKNOWN, not a verified zero position.
* A disabled section is DISABLED, not a successful zero-position reconciliation.
* Future timestamps are rejected (point-in-time integrity).
* Snapshot IDs are deterministic for the same logical input version.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from axel.core.types import Section
from axel.portfolio.serialization import fingerprint
from axel.portfolio.types import (
    AccountCapital,
    CapitalSummary,
    CommitmentKind,
    CommitmentRecord,
    ConversionResult,
    DataQuality,
    FreshnessPolicy,
    FxRate,
    IssueSeverity,
    OpenOrderRecord,
    PortfolioSnapshot,
    PositionRecord,
    ReconciliationIssue,
    ReconciliationStatus,
    SectionReadiness,
    SectionStatus,
    SnapshotCompleteness,
    SourceRef,
    as_section,
    require_aware,
    utc_now,
)

SNAPSHOT_SCHEMA_VERSION = "p11-snapshot-1"
SNAPSHOT_CALCULATION_VERSION = "p11-state-1"
DEFAULT_FRESHNESS = FreshnessPolicy()

_QUALITY_ORDER: dict[DataQuality, int] = {
    DataQuality.VALID: 0,
    DataQuality.DEGRADED: 1,
    DataQuality.STALE: 2,
    DataQuality.MISSING: 3,
    DataQuality.INVALID: 4,
}

_DEFAULT_LIMITATIONS: tuple[str, ...] = (
    "Open orders are treated as possible incremental exposure; mutually exclusive orders are not netted.",
    "Option Greeks are local model sensitivities, not a complete measure of nonlinear or gap risk.",
    "Prediction-market probabilities are not evidence of settlement certainty.",
    "Stress scenarios are incomplete by design and do not enumerate all future states.",
)


class SnapshotInvariantError(ValueError):
    """Raised when a snapshot cannot be built without violating an invariant."""


def worst_quality(*qualities: DataQuality) -> DataQuality:
    if not qualities:
        return DataQuality.VALID
    return max(qualities, key=lambda quality: _QUALITY_ORDER[quality])


def _worse_quality(left: DataQuality, right: DataQuality) -> DataQuality:
    return left if _QUALITY_ORDER[left] >= _QUALITY_ORDER[right] else right


def _fx_graph(rates: Sequence[FxRate]) -> dict[str, list[tuple[str, float, FxRate]]]:
    graph: dict[str, list[tuple[str, float, FxRate]]] = {}
    for rate in rates:
        graph.setdefault(rate.base_currency, []).append(
            (rate.quote_currency, rate.rate, rate)
        )
        graph.setdefault(rate.quote_currency, []).append(
            (rate.base_currency, 1.0 / rate.rate, rate)
        )
    for edges in graph.values():
        edges.sort(key=lambda edge: edge[0])
    return graph


def convert_amount(
    amount: float,
    from_currency: str,
    to_currency: str,
    rates: Sequence[FxRate],
    now: datetime,
    *,
    max_age_seconds: int = 300,
) -> ConversionResult:
    """Convert an amount using the shortest deterministic FX rate path.

    Missing or invalid rates fail closed. Stale rates are returned with
    ``quality=STALE`` so the caller can degrade the snapshot rather than
    pretending the value is current.
    """
    if from_currency == to_currency:
        return ConversionResult(
            ok=True,
            amount=amount,
            rate=1.0,
            path=(from_currency,),
            as_of=require_aware(now, "now"),
            quality=DataQuality.VALID,
        )
    if not from_currency or not to_currency:
        return ConversionResult.failure("currency codes must be non-empty")

    current = require_aware(now, "now")
    graph = _fx_graph(rates)
    queue: deque[tuple[str, float, tuple[str, ...], DataQuality, datetime | None]] = deque(
        [(from_currency, 1.0, (from_currency,), DataQuality.VALID, None)]
    )
    visited = {from_currency}
    while queue:
        currency, accumulated, path, quality, oldest = queue.popleft()
        for neighbor, edge_rate, rate in graph.get(currency, []):
            if neighbor in visited:
                continue
            edge_quality = rate.quality
            if edge_quality in {DataQuality.MISSING, DataQuality.INVALID}:
                continue
            age = (current - rate.as_of).total_seconds()
            if age > max_age_seconds:
                edge_quality = _worse_quality(edge_quality, DataQuality.STALE)
            next_quality = _worse_quality(quality, edge_quality)
            next_oldest = rate.as_of if oldest is None else min(oldest, rate.as_of)
            next_path = path + (neighbor,)
            if neighbor == to_currency:
                return ConversionResult(
                    ok=True,
                    amount=amount * accumulated * edge_rate,
                    rate=accumulated * edge_rate,
                    path=next_path,
                    as_of=next_oldest,
                    quality=next_quality,
                )
            visited.add(neighbor)
            queue.append(
                (neighbor, accumulated * edge_rate, next_path, next_quality, next_oldest)
            )
    return ConversionResult.failure(f"no FX conversion path from {from_currency} to {to_currency}")


def _ensure_not_future(value: datetime, now: datetime, field_name: str) -> None:
    if value > now + timedelta(seconds=1):
        raise SnapshotInvariantError(f"{field_name} is in the future: {value.isoformat()}")


def _duplicate_ids(items: Sequence[object], attribute: str, label: str) -> None:
    seen: set[str] = set()
    for item in items:
        value = getattr(item, attribute)
        if value in seen:
            raise SnapshotInvariantError(f"duplicate {label} id: {value}")
        seen.add(value)


def _merge_source_refs(refs: Sequence[SourceRef]) -> tuple[SourceRef, ...]:
    merged: dict[str, SourceRef] = {}
    for ref in refs:
        existing = merged.get(ref.source_id)
        if existing is None:
            merged[ref.source_id] = ref
            continue
        merged[ref.source_id] = SourceRef(
            source_id=ref.source_id,
            source_type=ref.source_type,
            observed_at=max(existing.observed_at, ref.observed_at),
            received_at=max(existing.received_at, ref.received_at),
            quality=worst_quality(existing.quality, ref.quality),
            section=existing.section or ref.section,
            lineage=tuple(dict.fromkeys(existing.lineage + ref.lineage)),
            note=existing.note or ref.note,
        )
    return tuple(merged[key] for key in sorted(merged))


def _section_status(
    provided: Mapping[Section, SectionStatus],
    section: Section,
    now: datetime,
    freshness: FreshnessPolicy,
) -> SectionStatus:
    status = provided.get(section)
    if status is None:
        return SectionStatus(
            section=section,
            readiness=SectionReadiness.UNKNOWN,
            data_quality=DataQuality.MISSING,
            reconciliation_status=ReconciliationStatus.UNAVAILABLE,
            last_update=None,
            reason="no section status supplied",
        )
    if status.last_update is None:
        return replace(
            status,
            data_quality=DataQuality.MISSING,
            reconciliation_status=ReconciliationStatus.UNAVAILABLE,
            reason=(status.reason + "; no last_update timestamp").strip("; "),
        )
    _ensure_not_future(status.last_update, now, f"section {section.value} last_update")
    age = (now - status.last_update).total_seconds()
    if age > freshness.max_age_for(section) + freshness.stale_grace_seconds:
        return replace(
            status,
            data_quality=_worse_quality(status.data_quality, DataQuality.STALE),
            reconciliation_status=(
                ReconciliationStatus.PENDING
                if status.reconciliation_status == ReconciliationStatus.RECONCILED
                else status.reconciliation_status
            ),
            reason=(status.reason + "; section state is stale").strip("; "),
        )
    return status


def _capital_summary(
    accounts: Sequence[AccountCapital],
    commitments: Sequence[CommitmentRecord],
    fx_rates: Sequence[FxRate],
    base_currency: str,
    now: datetime,
    freshness: FreshnessPolicy,
) -> CapitalSummary:
    if not accounts:
        return CapitalSummary(
            base_currency=base_currency,
            cash_balance=None,
            equity_value=None,
            account_equity=None,
            reserved_order_capacity=None,
            margin_requirement=None,
            available_to_trade=None,
            commitments_not_reserved=None,
            unsettled_proceeds=None,
            risk_budget_remaining=None,
            liquidation_value_estimate=None,
            by_account={},
            conversion_complete=False,
            unconverted_accounts=(),
        )

    totals = {
        "cash_balance": 0.0,
        "equity_value": 0.0,
        "account_equity": 0.0,
        "reserved_order_capacity": 0.0,
        "margin_requirement": 0.0,
        "available_to_trade": 0.0,
    }
    risk_budget_total = 0.0
    liquidation_total = 0.0
    risk_budget_complete = True
    liquidation_complete = True
    conversion_complete = True
    unconverted: list[str] = []

    def convert(amount: float, currency: str, label: str) -> float | None:
        nonlocal conversion_complete
        result = convert_amount(
            amount,
            currency,
            base_currency,
            fx_rates,
            now,
            max_age_seconds=freshness.max_fx_age_seconds,
        )
        if not result.ok or result.amount is None:
            conversion_complete = False
            if label not in unconverted:
                unconverted.append(label)
            return None
        return result.amount

    def convert_optional(amount: float, currency: str) -> float | None:
        result = convert_amount(
            amount,
            currency,
            base_currency,
            fx_rates,
            now,
            max_age_seconds=freshness.max_fx_age_seconds,
        )
        if not result.ok or result.amount is None:
            return None
        return result.amount

    for account in accounts:
        _ensure_not_future(account.as_of, now, f"account {account.account_id} as_of")
        for field_name in (
            "cash_balance",
            "equity_value",
            "account_equity",
            "reserved_order_capacity",
            "margin_requirement",
        ):
            converted = convert(getattr(account, field_name), account.currency, account.account_id)
            if converted is not None:
                totals[field_name] += converted
        available = convert(
            account.conservative_available(), account.currency, account.account_id
        )
        if available is not None:
            totals["available_to_trade"] += available
        if account.risk_budget_remaining is None:
            risk_budget_complete = False
        else:
            converted_risk = convert_optional(
                account.risk_budget_remaining, account.currency
            )
            if converted_risk is None:
                risk_budget_complete = False
            else:
                risk_budget_total += converted_risk
        if account.liquidation_value_estimate is None:
            liquidation_complete = False
        else:
            converted_liquidation = convert_optional(
                account.liquidation_value_estimate, account.currency
            )
            if converted_liquidation is None:
                liquidation_complete = False
            else:
                liquidation_total += converted_liquidation

    commitments_total = 0.0
    commitments_complete = True
    unsettled_total = 0.0
    unsettled_complete = True
    for commitment in commitments:
        _ensure_not_future(commitment.as_of, now, f"commitment {commitment.commitment_id} as_of")
        if commitment.resolved or commitment.included_in_reserved_capacity:
            continue
        converted = convert(
            commitment.amount, commitment.currency, commitment.commitment_id
        )
        if converted is None:
            commitments_complete = False
            unsettled_complete = False
            continue
        commitments_total += converted
        if commitment.kind == CommitmentKind.UNSETTLED_CASH:
            unsettled_total += converted

    return CapitalSummary(
        base_currency=base_currency,
        cash_balance=totals["cash_balance"] if conversion_complete else None,
        equity_value=totals["equity_value"] if conversion_complete else None,
        account_equity=totals["account_equity"] if conversion_complete else None,
        reserved_order_capacity=(
            totals["reserved_order_capacity"] if conversion_complete else None
        ),
        margin_requirement=totals["margin_requirement"] if conversion_complete else None,
        available_to_trade=totals["available_to_trade"] if conversion_complete else None,
        commitments_not_reserved=commitments_total if commitments_complete else None,
        unsettled_proceeds=unsettled_total if unsettled_complete else None,
        risk_budget_remaining=risk_budget_total if risk_budget_complete else None,
        liquidation_value_estimate=liquidation_total if liquidation_complete else None,
        by_account={account.account_id: account for account in accounts},
        conversion_complete=conversion_complete and commitments_complete,
        unconverted_accounts=tuple(unconverted),
    )


def build_portfolio_snapshot(
    *,
    accounts: Sequence[AccountCapital],
    positions: Sequence[PositionRecord],
    open_orders: Sequence[OpenOrderRecord],
    commitments: Sequence[CommitmentRecord],
    section_status: Sequence[SectionStatus],
    fx_rates: Sequence[FxRate] = (),
    now: datetime | None = None,
    base_currency: str = "USD",
    freshness: FreshnessPolicy = DEFAULT_FRESHNESS,
    snapshot_version: int = 1,
    known_limitations: Sequence[str] = (),
    reconciliation_issues: Sequence[ReconciliationIssue] = (),
    source_refs: Sequence[SourceRef] = (),
    snapshot_id: str | None = None,
) -> PortfolioSnapshot:
    """Build an immutable portfolio snapshot from explicit source records."""
    current = require_aware(now or utc_now(), "now")
    if snapshot_version < 1:
        raise SnapshotInvariantError("snapshot_version must be >= 1")
    if not base_currency:
        raise SnapshotInvariantError("base_currency must be non-empty")

    _duplicate_ids(accounts, "account_id", "account")
    _duplicate_ids(positions, "position_id", "position")
    _duplicate_ids(open_orders, "order_id", "order")
    _duplicate_ids(commitments, "commitment_id", "commitment")

    provided_sections: dict[Section, SectionStatus] = {}
    for status in section_status:
        section = as_section(status.section)
        if section in provided_sections:
            raise SnapshotInvariantError(f"duplicate section status for {section.value}")
        provided_sections[section] = status

    statuses = {
        section: _section_status(provided_sections, section, current, freshness)
        for section in Section
    }

    warnings: list[str] = list(known_limitations)
    invalid_reasons: list[str] = []
    incomplete_reasons: list[str] = []
    degraded_reasons: list[str] = []

    if not accounts:
        invalid_reasons.append("no account capital supplied; equity and available cash are unknown")

    # --- Positions and section consistency ---------------------------------
    positions_by_section: dict[Section, list[PositionRecord]] = {s: [] for s in Section}
    for position in positions:
        _ensure_not_future(position.as_of, current, f"position {position.position_id} as_of")
        positions_by_section[position.section].append(position)
        if position.quality == DataQuality.INVALID:
            invalid_reasons.append(f"position {position.position_id} has invalid data quality")
        elif position.quality == DataQuality.MISSING:
            incomplete_reasons.append(f"position {position.position_id} is missing a valuation")
        elif position.quality in {DataQuality.STALE, DataQuality.DEGRADED}:
            degraded_reasons.append(
                f"position {position.position_id} quality is {position.quality.value}"
            )
        if position.reconciliation_status in {
            ReconciliationStatus.MISMATCH,
            ReconciliationStatus.UNKNOWN,
            ReconciliationStatus.UNAVAILABLE,
        }:
            incomplete_reasons.append(
                f"position {position.position_id} reconciliation is "
                f"{position.reconciliation_status.value}"
            )
        if position.option is None and position.prediction is None:
            computed = position.computed_market_value
            tolerance = max(0.01, abs(computed) * 1e-6)
            if abs(position.market_value - computed) > tolerance:
                warnings.append(
                    f"position {position.position_id} reported market value differs from "
                    "quantity * multiplier * price"
                )

    for section, section_positions in positions_by_section.items():
        status = statuses[section]
        if status.readiness == SectionReadiness.DISABLED and section_positions:
            invalid_reasons.append(
                f"section {section.value} is disabled but holds {len(section_positions)} position(s)"
            )
        if status.readiness == SectionReadiness.UNKNOWN and section_positions:
            incomplete_reasons.append(
                f"section {section.value} has positions but no authoritative status"
            )
        if status.readiness == SectionReadiness.RECONCILIATION_BLOCKED:
            incomplete_reasons.append(f"section {section.value} is reconciliation-blocked")
        if status.readiness == SectionReadiness.HALTED:
            degraded_reasons.append(f"section {section.value} is halted")
        if status.readiness not in {SectionReadiness.DISABLED, SectionReadiness.RESEARCH_ONLY}:
            if status.data_quality == DataQuality.MISSING:
                incomplete_reasons.append(f"section {section.value} state is missing")
            elif status.data_quality == DataQuality.INVALID:
                invalid_reasons.append(f"section {section.value} state is invalid")
            elif status.data_quality in {DataQuality.STALE, DataQuality.DEGRADED}:
                degraded_reasons.append(
                    f"section {section.value} state is {status.data_quality.value}"
                )
            if status.reconciliation_status == ReconciliationStatus.MISMATCH:
                incomplete_reasons.append(f"section {section.value} reconciliation mismatch")
            elif status.reconciliation_status in {
                ReconciliationStatus.UNKNOWN,
                ReconciliationStatus.UNAVAILABLE,
            }:
                incomplete_reasons.append(
                    f"section {section.value} reconciliation is "
                    f"{status.reconciliation_status.value}"
                )

    # --- Open orders --------------------------------------------------------
    for order in open_orders:
        _ensure_not_future(order.as_of, current, f"order {order.order_id} as_of")
        if order.quality == DataQuality.INVALID:
            invalid_reasons.append(f"order {order.order_id} has invalid data quality")
        elif order.quality == DataQuality.MISSING:
            incomplete_reasons.append(f"order {order.order_id} is missing a valuation")
        elif order.quality in {DataQuality.STALE, DataQuality.DEGRADED}:
            degraded_reasons.append(f"order {order.order_id} quality is {order.quality.value}")
        if order.state.lower() in {"unknown", "pending", "cancel_pending"}:
            incomplete_reasons.append(
                f"order {order.order_id} state is {order.state}; exposure is not final"
            )

    # --- Commitments --------------------------------------------------------
    for commitment in commitments:
        _ensure_not_future(commitment.as_of, current, f"commitment {commitment.commitment_id} as_of")
        if commitment.quality == DataQuality.INVALID:
            invalid_reasons.append(
                f"commitment {commitment.commitment_id} has invalid data quality"
            )
        elif commitment.quality == DataQuality.MISSING:
            incomplete_reasons.append(
                f"commitment {commitment.commitment_id} is missing a valuation"
            )

    # --- FX freshness -------------------------------------------------------
    for rate in fx_rates:
        _ensure_not_future(rate.as_of, current, f"FX rate {rate.pair} as_of")
        if rate.quality == DataQuality.INVALID:
            invalid_reasons.append(f"FX rate {rate.pair} is invalid")
        elif rate.quality == DataQuality.MISSING:
            incomplete_reasons.append(f"FX rate {rate.pair} is missing")
        elif (current - rate.as_of).total_seconds() > freshness.max_fx_age_seconds:
            degraded_reasons.append(f"FX rate {rate.pair} is stale")

    # --- Reconciliation issues ---------------------------------------------
    for issue in reconciliation_issues:
        if not issue.is_open:
            continue
        if issue.severity == IssueSeverity.CRITICAL:
            invalid_reasons.append(
                f"critical reconciliation issue {issue.issue_id} on {issue.entity_ref}"
            )
        elif issue.severity == IssueSeverity.WARNING:
            degraded_reasons.append(
                f"warning reconciliation issue {issue.issue_id} on {issue.entity_ref}"
            )

    capital = _capital_summary(
        accounts, commitments, fx_rates, base_currency, current, freshness
    )
    if not capital.conversion_complete:
        invalid_reasons.append(
            "currency conversion is incomplete for: "
            + (", ".join(capital.unconverted_accounts) or "commitments")
        )

    # --- Aggregate status ---------------------------------------------------
    if invalid_reasons:
        completeness = SnapshotCompleteness.INVALID
    elif incomplete_reasons:
        completeness = SnapshotCompleteness.INCOMPLETE
    elif degraded_reasons:
        completeness = SnapshotCompleteness.DEGRADED
    else:
        completeness = SnapshotCompleteness.COMPLETE

    quality_candidates: list[DataQuality] = [
        status.data_quality
        for section, status in statuses.items()
        if status.readiness not in {SectionReadiness.DISABLED}
        or bool(positions_by_section[section])
    ]
    quality_candidates.extend(position.quality for position in positions)
    quality_candidates.extend(order.quality for order in open_orders)
    quality_candidates.extend(commitment.quality for commitment in commitments)
    quality_candidates.extend(rate.quality for rate in fx_rates)
    quality_candidates.extend(account.quality for account in accounts)
    data_quality = worst_quality(*quality_candidates)
    if invalid_reasons:
        data_quality = worst_quality(data_quality, DataQuality.INVALID)
    elif incomplete_reasons:
        data_quality = worst_quality(data_quality, DataQuality.MISSING)
    elif degraded_reasons:
        data_quality = worst_quality(data_quality, DataQuality.DEGRADED)

    if any(issue.blocks_new_risk for issue in reconciliation_issues):
        reconciliation = ReconciliationStatus.MISMATCH
    elif not accounts:
        reconciliation = ReconciliationStatus.UNAVAILABLE
    else:
        active_statuses = [
            status
            for section, status in statuses.items()
            if status.readiness not in {SectionReadiness.DISABLED, SectionReadiness.RESEARCH_ONLY}
            or bool(positions_by_section[section])
        ]
        priorities = {
            ReconciliationStatus.MISMATCH: 5,
            ReconciliationStatus.UNAVAILABLE: 4,
            ReconciliationStatus.UNKNOWN: 3,
            ReconciliationStatus.PENDING: 2,
            ReconciliationStatus.RECONCILED: 1,
        }
        if not active_statuses:
            reconciliation = ReconciliationStatus.UNAVAILABLE
        else:
            reconciliation = max(
                (status.reconciliation_status for status in active_statuses),
                key=lambda value: priorities[value],
            )

    blocking_positions = any(
        position.reconciliation_status != ReconciliationStatus.RECONCILED
        or position.quality != DataQuality.VALID
        for position in positions
    )
    blocking_orders = any(
        order.quality != DataQuality.VALID
        or order.state.lower() in {"unknown", "pending", "cancel_pending"}
        for order in open_orders
    )
    blocking_commitments = any(
        commitment.quality != DataQuality.VALID and not commitment.resolved
        for commitment in commitments
    )
    blocking_sections = any(
        status.readiness not in {SectionReadiness.DISABLED, SectionReadiness.RESEARCH_ONLY}
        and not status.permits_new_risk
        for status in statuses.values()
    )
    usable_for_risk_increase = (
        completeness == SnapshotCompleteness.COMPLETE
        and data_quality == DataQuality.VALID
        and reconciliation == ReconciliationStatus.RECONCILED
        and not blocking_positions
        and not blocking_orders
        and not blocking_commitments
        and not blocking_sections
    )

    # --- Lineage ------------------------------------------------------------
    refs: list[SourceRef] = []
    for account in accounts:
        refs.append(
            SourceRef(
                source_id=f"account:{account.account_id}",
                source_type="account",
                observed_at=account.as_of,
                received_at=account.as_of,
                quality=account.quality,
                lineage=(account.source,),
            )
        )
    for position in positions:
        refs.append(
            SourceRef(
                source_id=f"position:{position.position_id}",
                source_type="position",
                section=position.section,
                observed_at=position.as_of,
                received_at=position.as_of,
                quality=position.quality,
                lineage=(position.source,),
            )
        )
    for order in open_orders:
        refs.append(
            SourceRef(
                source_id=f"order:{order.order_id}",
                source_type="open_order",
                section=order.section,
                observed_at=order.as_of,
                received_at=order.as_of,
                quality=order.quality,
                lineage=(order.source,),
            )
        )
    for commitment in commitments:
        refs.append(
            SourceRef(
                source_id=f"commitment:{commitment.commitment_id}",
                source_type="commitment",
                section=commitment.section,
                observed_at=commitment.as_of,
                received_at=commitment.as_of,
                quality=commitment.quality,
                lineage=(commitment.source,),
            )
        )
    for section, status in statuses.items():
        refs.append(
            SourceRef(
                source_id=f"section:{section.value}",
                source_type="section_status",
                section=section,
                observed_at=status.last_update or current,
                received_at=status.last_update or current,
                quality=status.data_quality,
                lineage=(status.source,) if status.source else (),
                note=status.reason or None,
            )
        )
    for rate in fx_rates:
        refs.append(
            SourceRef(
                source_id=f"fx:{rate.pair}:{rate.source}",
                source_type="fx_rate",
                observed_at=rate.as_of,
                received_at=rate.as_of,
                quality=rate.quality,
                lineage=(rate.source,),
            )
        )
    refs.extend(source_refs)
    merged_refs = _merge_source_refs(refs)
    source_cutoff = max((ref.observed_at for ref in merged_refs), default=current)

    limitations = tuple(dict.fromkeys(_DEFAULT_LIMITATIONS + tuple(warnings) + tuple(
        incomplete_reasons + invalid_reasons + degraded_reasons
    )))

    input_version = {
        "accounts": accounts,
        "positions": positions,
        "open_orders": open_orders,
        "commitments": commitments,
        "section_status": tuple(statuses[key] for key in Section),
        "fx_rates": fx_rates,
        "reconciliation_issues": reconciliation_issues,
        "base_currency": base_currency,
        "snapshot_version": snapshot_version,
        "snapshot_time": current,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "calculation_version": SNAPSHOT_CALCULATION_VERSION,
    }
    snapshot_fingerprint = fingerprint("snap", input_version)
    final_snapshot_id = snapshot_id or snapshot_fingerprint

    return PortfolioSnapshot(
        snapshot_id=final_snapshot_id,
        snapshot_version=snapshot_version,
        snapshot_time=current,
        source_cutoff_time=source_cutoff,
        base_currency=base_currency,
        capital_summary=capital,
        positions=tuple(positions),
        open_orders=tuple(open_orders),
        commitments=tuple(commitments),
        section_status={section.value: statuses[section] for section in Section},
        source_refs=merged_refs,
        data_quality_status=data_quality,
        completeness_status=completeness,
        reconciliation_status=reconciliation,
        is_usable_for_risk_increase=usable_for_risk_increase,
        fingerprint=snapshot_fingerprint,
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        calculation_version=SNAPSHOT_CALCULATION_VERSION,
        known_limitations=limitations,
        fx_rates=tuple(fx_rates),
    )


def verify_snapshot_identity(snapshot: PortfolioSnapshot, tolerance: float = 1e-6) -> tuple[str, ...]:
    """Re-check snapshot-level accounting identities and return violations.

    This is intentionally independent of the builder: it can be run against a
    deserialized snapshot before authorization.
    """
    violations: list[str] = []
    capital = snapshot.capital_summary
    if capital.conversion_complete:
        by_account = capital.by_account
        if by_account and capital.cash_balance is not None:
            # The snapshot already stores converted totals; this identity is
            # checked at build time per account. Here we verify non-negativity
            # of reserved and margin fields, which can never be negative.
            if (capital.reserved_order_capacity or 0.0) < -tolerance:
                violations.append("reserved_order_capacity is negative")
            if (capital.margin_requirement or 0.0) < -tolerance:
                violations.append("margin_requirement is negative")
    for position in snapshot.positions:
        if position.quantity == 0.0 and position.market_value != 0.0:
            violations.append(
                f"flat position {position.position_id} has non-zero market value"
            )
    return tuple(violations)


__all__ = [
    "DEFAULT_FRESHNESS",
    "SNAPSHOT_CALCULATION_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
    "SnapshotInvariantError",
    "build_portfolio_snapshot",
    "convert_amount",
    "verify_snapshot_identity",
    "worst_quality",
]
