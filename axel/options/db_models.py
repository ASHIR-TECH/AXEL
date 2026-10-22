"""
SQLAlchemy ORM models for the Options section (Phase 8).

Append-only by default. Execution tables (orders, fills, positions) are
owned by the execution service; agent roles can insert proposals/research
only.
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
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from axel.db.base import Base, TimestampMixin


class OptionContractModel(Base, TimestampMixin):
    """Option contract reference data. Immutable once written."""

    __tablename__ = "option_contracts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    underlying_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    underlying_symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    right: Mapped[str] = mapped_column(String(10), nullable=False)  # call | put
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    exercise_style: Mapped[str] = mapped_column(String(20), default="american")
    settlement_type: Mapped[str] = mapped_column(String(20), default="cash")
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    last_trading_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exercise_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    quotes: Mapped[list[OptionQuoteModel]] = relationship(
        "OptionQuoteModel", back_populates="contract", cascade="all, delete-orphan"
    )


class OptionQuoteModel(Base):
    """Raw option quote snapshot with event/receive time for point-in-time correctness."""

    __tablename__ = "option_quotes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("option_contracts.contract_id"), index=True, nullable=False
    )
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    receive_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    bid: Mapped[float | None] = mapped_column(Float)
    bid_size: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    ask_size: Mapped[float | None] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float)
    last_size: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(32), default="VALID")
    source: Mapped[str] = mapped_column(String(64), default="")
    schema_version: Mapped[str] = mapped_column(String(32), default="canonical-1")

    contract: Mapped[OptionContractModel] = relationship(
        "OptionContractModel", back_populates="quotes"
    )


class IVGreekSnapshotModel(Base, TimestampMixin):
    """Computed IV and Greeks per contract at a point in time."""

    __tablename__ = "iv_greek_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    time_to_expiry_years: Mapped[float] = mapped_column(Float, nullable=False)
    risk_free_rate: Mapped[float] = mapped_column(Float, nullable=False)
    volatility: Mapped[float] = mapped_column(Float, nullable=False)  # solved IV
    dividend_yield: Mapped[float] = mapped_column(Float, default=0.0)
    price_theoretical: Mapped[float] = mapped_column(Float, nullable=False)
    delta: Mapped[float] = mapped_column(Float)
    gamma: Mapped[float] = mapped_column(Float)
    vega: Mapped[float] = mapped_column(Float)
    theta: Mapped[float] = mapped_column(Float)
    rho: Mapped[float] = mapped_column(Float)
    iv: Mapped[float] = mapped_column(Float)
    model: Mapped[str] = mapped_column(String(64), default="black_scholes")
    solver_converged: Mapped[bool] = mapped_column(Boolean, default=True)
    solver_iterations: Mapped[int] = mapped_column(Integer, default=0)


class OptionsProposalModel(Base, TimestampMixin):
    """Untrusted options proposal awaiting deterministic validation."""

    __tablename__ = "options_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    section: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    underlying_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    underlying_symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    limit_policy: Mapped[str] = mapped_column(String(32), default="limit_only")
    max_loss: Mapped[float | None] = mapped_column(Float)
    max_gain: Mapped[float | None] = mapped_column(Float)
    break_even_points: Mapped[list[float] | None] = mapped_column(JSON)
    greeks: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    thesis: Mapped[str] = mapped_column(Text, default="")
    uncertainty: Mapped[str] = mapped_column(Text, default="")
    agent_trace_id: Mapped[str | None] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16), default="paper")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    risk_decision: Mapped[OptionsRiskDecisionModel | None] = relationship(
        "OptionsRiskDecisionModel", back_populates="proposal", uselist=False
    )
    legs: Mapped[list[OptionsLegModel]] = relationship(
        "OptionsLegModel", back_populates="proposal", cascade="all, delete-orphan"
    )


class OptionsLegModel(Base, TimestampMixin):
    """Leg of an options proposal."""

    __tablename__ = "options_legs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("options_proposals.id"), index=True, nullable=False
    )
    contract_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    right: Mapped[str] = mapped_column(String(10), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # long | short
    ratio: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float)
    exercise_style: Mapped[str] = mapped_column(String(20), default="american")
    settlement_type: Mapped[str] = mapped_column(String(20), default="cash")
    multiplier: Mapped[float] = mapped_column(Float, default=100.0)

    proposal: Mapped[OptionsProposalModel] = relationship(
        "OptionsProposalModel", back_populates="legs"
    )


class OptionsRiskDecisionModel(Base, TimestampMixin):
    """Deterministic audit record for every options proposal validated."""

    __tablename__ = "options_risk_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("options_proposals.id"), unique=True, nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons: Mapped[list[str] | None] = mapped_column(JSON, default=list)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSON, default=list)
    max_loss: Mapped[float | None] = mapped_column(Float)
    max_gain: Mapped[float | None] = mapped_column(Float)
    break_even_points: Mapped[list[float] | None] = mapped_column(JSON)
    margin_buffer_usd: Mapped[float] = mapped_column(Float, default=0.0)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    greeks: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    proposal: Mapped[OptionsProposalModel] = relationship(
        "OptionsProposalModel", back_populates="risk_decision"
    )


class OptionsPositionModel(Base, TimestampMixin):
    """Current open options position per contract."""

    __tablename__ = "options_positions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    underlying_symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    right: Mapped[str] = mapped_column(String(10), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)  # net contracts
    avg_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    max_loss_at_entry: Mapped[float | None] = mapped_column(Float)


class AssignmentEventModel(Base, TimestampMixin):
    """Early assignment / exercise event for a short option leg."""

    __tablename__ = "assignment_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    position_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    exercise_type: Mapped[str] = mapped_column(String(32), nullable=False)  # early_assignment | expiry_exercise
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    cash_impact_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | resolved | disputed


class ExpiryEventModel(Base, TimestampMixin):
    """Contract expiry event and settlement outcome."""

    __tablename__ = "expiry_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    expiry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    settlement_price: Mapped[float | None] = mapped_column(Float)
    payout_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | settled | disputed
