"""
Phase 11 portfolio-intelligence persistence models.

The ORM stores immutable snapshot versions and append-only proposal/audit
records. Domain objects remain frozen dataclasses in ``axel.portfolio``; these
rows are the durable audit trail, not a second source of business logic.

All monetary values are in the snapshot base currency unless a column says
otherwise. JSON payloads are canonicalized by ``axel.portfolio.serialization``
before persistence so replay and audit comparisons remain deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from axel.db.base import Base, TimestampMixin


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PortfolioSnapshotRow(Base, TimestampMixin):
    """Immutable consolidated portfolio snapshot version."""

    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_version", "source_cutoff_time", name="uq_portfolio_snapshot_version"
        ),
        Index("ix_portfolio_snapshots_time", "snapshot_time"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_cutoff_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    capital_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    data_quality_status: Mapped[str] = mapped_column(String(20), nullable=False)
    completeness_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_usable_for_risk_increase: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    known_limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    fx_rates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    sources: Mapped[list[PortfolioSourceRow]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    positions: Mapped[list[PortfolioPositionRow]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    open_orders: Mapped[list[PortfolioOpenOrderRow]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    commitments: Mapped[list[PortfolioCommitmentRow]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class PortfolioSourceRow(Base, TimestampMixin):
    """One authoritative or supporting source used by a snapshot."""

    __tablename__ = "portfolio_sources"
    __table_args__ = (
        Index("ix_portfolio_sources_snapshot", "snapshot_id"),
        Index("ix_portfolio_sources_as_of", "as_of"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(80))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingestion_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    is_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    freshness_status: Mapped[str] = mapped_column(String(20), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped[PortfolioSnapshotRow] = relationship(back_populates="sources")


class PortfolioPositionRow(Base, TimestampMixin):
    """One position in a snapshot version."""

    __tablename__ = "portfolio_positions"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "position_id", name="uq_portfolio_position_snapshot"
        ),
        Index("ix_portfolio_positions_instrument", "instrument_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    position_id: Mapped[str] = mapped_column(String(80), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(80), nullable=False)
    venue: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    market_value: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    settlement_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(80))
    option: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped[PortfolioSnapshotRow] = relationship(back_populates="positions")


class PortfolioOpenOrderRow(Base, TimestampMixin):
    """One open order in a snapshot version."""

    __tablename__ = "portfolio_open_orders"
    __table_args__ = (Index("ix_portfolio_orders_snapshot", "snapshot_id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(80), nullable=False)
    venue: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(80))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped[PortfolioSnapshotRow] = relationship(back_populates="open_orders")


class PortfolioCommitmentRow(Base, TimestampMixin):
    """One capital commitment or reserve in a snapshot version."""

    __tablename__ = "portfolio_commitments"
    __table_args__ = (Index("ix_portfolio_commitments_snapshot", "snapshot_id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    commitment_id: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(80))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped[PortfolioSnapshotRow] = relationship(back_populates="commitments")


class PortfolioExposureRow(Base, TimestampMixin):
    """Normalized cross-asset exposure for one instrument and snapshot."""

    __tablename__ = "portfolio_exposures"
    __table_args__ = (
        UniqueConstraint(
            "exposure_snapshot_id",
            "instrument_id",
            "instrument_kind",
            name="uq_portfolio_exposure_instrument",
        ),
        Index("ix_portfolio_exposures_snapshot", "portfolio_snapshot_id"),
        Index("ix_portfolio_exposures_section", "section"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    exposure_snapshot_id: Mapped[str] = mapped_column(String(80), nullable=False)
    portfolio_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[str] = mapped_column(String(80), nullable=False)
    instrument_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="position")
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    venue: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    market_value: Mapped[float] = mapped_column(Float, nullable=False)
    gross_market_value: Mapped[float] = mapped_column(Float, nullable=False)
    delta_equivalent: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)
    gamma: Mapped[float | None] = mapped_column(Float)
    vega: Mapped[float | None] = mapped_column(Float)
    theta: Mapped[float | None] = mapped_column(Float)
    underlying_price: Mapped[float | None] = mapped_column(Float)
    max_loss: Mapped[float | None] = mapped_column(Float)
    margin_requirement: Mapped[float | None] = mapped_column(Float)
    expected_payoff: Mapped[float | None] = mapped_column(Float)
    liquidity_horizon_days: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[str] = mapped_column(String(20), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    factor_exposures: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    currency_exposures: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    uncertainty_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class PortfolioRiskBudgetRow(Base, TimestampMixin):
    """A proposed or approved section risk-budget allocation."""

    __tablename__ = "portfolio_risk_budgets"
    __table_args__ = (
        UniqueConstraint("budget_id", name="uq_portfolio_budget_id"),
        Index("ix_portfolio_budgets_snapshot", "portfolio_snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    budget_id: Mapped[str] = mapped_column(String(80), nullable=False)
    portfolio_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    total_risk_budget: Mapped[float] = mapped_column(Float, nullable=False)
    section_budgets: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class PortfolioProposalRow(Base, TimestampMixin):
    """Append-only allocation proposal. Payload preserves the full domain object."""

    __tablename__ = "portfolio_proposals"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_portfolio_proposal_id"),
        UniqueConstraint("idempotency_key", name="uq_portfolio_proposal_idempotency"),
        Index("ix_portfolio_proposals_snapshot", "portfolio_snapshot_id"),
        Index("ix_portfolio_proposals_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(80), nullable=False)
    portfolio_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    exposure_snapshot_id: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_budget_proposal_id: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    optimizer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    solver_status: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    stress_report_id: Mapped[str | None] = mapped_column(String(80))
    stress_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_quality_status: Mapped[str] = mapped_column(String(20), nullable=False)
    estimated_turnover: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_cutoff_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    targets: Mapped[list[PortfolioProposalTargetRow]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )
    constraints: Mapped[list[PortfolioConstraintResultRow]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )


class PortfolioProposalTargetRow(Base, TimestampMixin):
    """One instrument target inside an allocation proposal."""

    __tablename__ = "portfolio_proposal_targets"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id", "instrument_id", name="uq_portfolio_proposal_target"
        ),
        Index("ix_portfolio_targets_section", "section"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_proposals.proposal_id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[str] = mapped_column(String(80), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    current_weight: Mapped[float] = mapped_column(Float, nullable=False)
    proposed_weight: Mapped[float] = mapped_column(Float, nullable=False)
    current_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    proposed_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    delta_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    proposal: Mapped[PortfolioProposalRow] = relationship(back_populates="targets")


class PortfolioConstraintResultRow(Base, TimestampMixin):
    """One independently evaluated constraint attached to a proposal."""

    __tablename__ = "portfolio_constraint_results"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id", "name", "unit", name="uq_portfolio_constraint_result"
        ),
        Index("ix_portfolio_constraints_proposal", "proposal_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_proposals.proposal_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual: Mapped[float | None] = mapped_column(Float)
    limit: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    proposal: Mapped[PortfolioProposalRow] = relationship(back_populates="constraints")


class PortfolioStressRunRow(Base, TimestampMixin):
    """Versioned stress-suite result for a snapshot and exposure version."""

    __tablename__ = "portfolio_stress_runs"
    __table_args__ = (
        UniqueConstraint("report_id", name="uq_portfolio_stress_report_id"),
        Index("ix_portfolio_stress_snapshot", "portfolio_snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(80), nullable=False)
    portfolio_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    exposure_snapshot_id: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    worst_case_loss: Mapped[float] = mapped_column(Float, nullable=False)
    worst_scenario_id: Mapped[str | None] = mapped_column(String(80))
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class PortfolioRebalanceRow(Base, TimestampMixin):
    """Append-only rebalance proposal. Never an order record."""

    __tablename__ = "portfolio_rebalances"
    __table_args__ = (
        UniqueConstraint("rebalance_id", name="uq_portfolio_rebalance_id"),
        UniqueConstraint("idempotency_key", name="uq_portfolio_rebalance_idempotency"),
        Index("ix_portfolio_rebalances_proposal", "allocation_proposal_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    rebalance_id: Mapped[str] = mapped_column(String(80), nullable=False)
    allocation_proposal_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_proposals.proposal_id", ondelete="CASCADE"), nullable=False
    )
    portfolio_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    validation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False)
    required_capital: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class PortfolioReconciliationIssueRow(Base, TimestampMixin):
    """Discrepancy between AXEL's view and an authoritative source."""

    __tablename__ = "portfolio_reconciliation_issues"
    __table_args__ = (
        UniqueConstraint("issue_id", name="uq_portfolio_reconciliation_issue"),
        Index("ix_portfolio_reconciliation_status", "status"),
        Index("ix_portfolio_reconciliation_detected", "detected_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_systems: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    expected_value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    observed_value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    difference: Mapped[float | None] = mapped_column(Float)
    resolution_ref: Mapped[str | None] = mapped_column(String(80))
    responsible: Mapped[str | None] = mapped_column(String(80))
    blocks_risk_increase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PortfolioDecisionAuditRow(Base, TimestampMixin):
    """Append-only authorization decision and its independent checks."""

    __tablename__ = "portfolio_decision_audits"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_portfolio_decision_id"),
        Index("ix_portfolio_decisions_proposal", "proposal_id"),
        Index("ix_portfolio_decisions_validated", "validated_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(80), nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_id: Mapped[str | None] = mapped_column(String(80))
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_permitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    human_approval_ref: Mapped[str | None] = mapped_column(String(80))


class PortfolioModelVersionRow(Base, TimestampMixin):
    """Approved model/calculation version used by a portfolio decision."""

    __tablename__ = "portfolio_model_versions"
    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_portfolio_model_version"),
        Index("ix_portfolio_model_effective", "effective_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(256), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(80))


class PortfolioAlertRow(Base, TimestampMixin):
    """Persisted portfolio alert for operators and the existing watchdog."""

    __tablename__ = "portfolio_alerts"
    __table_args__ = (
        UniqueConstraint("alert_id", name="uq_portfolio_alert_id"),
        Index("ix_portfolio_alerts_detected", "detected_at"),
        Index("ix_portfolio_alerts_severity", "severity"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


__all__ = [
    "PortfolioAlertRow",
    "PortfolioCommitmentRow",
    "PortfolioConstraintResultRow",
    "PortfolioDecisionAuditRow",
    "PortfolioExposureRow",
    "PortfolioModelVersionRow",
    "PortfolioOpenOrderRow",
    "PortfolioPositionRow",
    "PortfolioProposalRow",
    "PortfolioProposalTargetRow",
    "PortfolioRebalanceRow",
    "PortfolioReconciliationIssueRow",
    "PortfolioRiskBudgetRow",
    "PortfolioSnapshotRow",
    "PortfolioSourceRow",
    "PortfolioStressRunRow",
]
