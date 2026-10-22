"""
SQLAlchemy ORM models for the Bets / Prediction Markets section (Phase 9).

Append-only by default. Agent roles can insert proposals/research only;
execution tables (bets, fills, settlements, ledger) are owned by the
execution/deterministic service.
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


class EventModel(Base, TimestampMixin):
    """Canonical event (sports match, election, etc.)."""

    __tablename__ = "bets_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    sport: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    league: Mapped[str | None] = mapped_column(String(64))
    participants: Mapped[list[str] | None] = mapped_column(JSON)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    venue: Mapped[str | None] = mapped_column(String(128))
    identity_confidence: Mapped[float] = mapped_column(Float, default=1.0)

    markets: Mapped[list[MarketModel]] = relationship(
        "MarketModel", back_populates="event", cascade="all, delete-orphan"
    )


class MarketModel(Base, TimestampMixin):
    """A betting market on an event with rules and payout conventions."""

    __tablename__ = "bets_markets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    market_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    event_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("bets_events.event_id"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    outcomes: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="active")
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_authority: Mapped[str] = mapped_column(String(128), default="")

    event: Mapped[EventModel] = relationship("EventModel", back_populates="markets")
    odds_snapshots: Mapped[list[OddsSnapshotModel]] = relationship(
        "OddsSnapshotModel", back_populates="market", cascade="all, delete-orphan"
    )
    forecasts: Mapped[list[ForecastModel]] = relationship(
        "ForecastModel", back_populates="market", cascade="all, delete-orphan"
    )


class OddsSnapshotModel(Base):
    """Point-in-time odds snapshot for one market outcome."""

    __tablename__ = "bets_odds_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    market_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    odds: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    odds_format: Mapped[str] = mapped_column(String(32), default="decimal")
    liquidity: Mapped[float | None] = mapped_column(Float)
    limits: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="active")
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receive_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(64), default="")
    quality_flags: Mapped[list[str] | None] = mapped_column(JSON)

    market: Mapped[MarketModel] = relationship("MarketModel", back_populates="odds_snapshots")


class ForecastModel(Base, TimestampMixin):
    """Probability forecast for a market's outcomes."""

    __tablename__ = "bets_forecasts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    market_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    probabilities: Mapped[dict[str, float] | None] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calibrator_version: Mapped[str | None] = mapped_column(String(64))
    forecast_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uncertainty: Mapped[dict[str, float] | None] = mapped_column(JSON)
    data_snapshot_ids: Mapped[list[str] | None] = mapped_column(JSON)
    missingness: Mapped[dict[str, bool] | None] = mapped_column(JSON)
    ood_flags: Mapped[list[str] | None] = mapped_column(JSON)

    market: Mapped[MarketModel] = relationship("MarketModel", back_populates="forecasts")


class BetProposalModel(Base, TimestampMixin):
    """Untrusted bet proposal awaiting deterministic validation."""

    __tablename__ = "bets_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    market_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_stake: Mapped[float] = mapped_column(Float, nullable=False)
    price_limit: Mapped[float | None] = mapped_column(Float)
    forecast_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    value_assessment: Mapped[BetValueAssessmentModel | None] = relationship(
        "BetValueAssessmentModel", back_populates="proposal", uselist=False
    )
    intent: Mapped[BetIntentModel | None] = relationship(
        "BetIntentModel", back_populates="proposal", uselist=False
    )


class BetValueAssessmentModel(Base, TimestampMixin):
    """Deterministic value engine output for a bet proposal."""

    __tablename__ = "bets_value_assessments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("bets_proposals.proposal_id"), unique=True, nullable=False
    )
    market_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    market_implied_prob: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_prob: Mapped[float] = mapped_column(Float, nullable=False)
    ev: Mapped[float] = mapped_column(Float, nullable=False)
    conservative_ev: Mapped[float] = mapped_column(Float, nullable=False)
    costs: Mapped[float] = mapped_column(Float, default=0.0)
    edge_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSON)

    proposal: Mapped[BetProposalModel] = relationship(
        "BetProposalModel", back_populates="value_assessment"
    )


class BetIntentModel(Base, TimestampMixin):
    """Deterministic bet intent (stake sizing output)."""

    __tablename__ = "bets_intents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    proposal_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("bets_proposals.proposal_id"), unique=True, nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    market_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    ev: Mapped[float] = mapped_column(Float, nullable=False)
    conservative_ev: Mapped[float] = mapped_column(Float, nullable=False)
    kelly_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    bankroll_after: Mapped[float] = mapped_column(Float, nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSON)

    proposal: Mapped[BetProposalModel] = relationship(
        "BetProposalModel", back_populates="intent"
    )


class BetOrderModel(Base, TimestampMixin):
    """Bet order submitted to paper/execution adapter."""

    __tablename__ = "bets_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    intent_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    market_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    side: Mapped[str] = mapped_column(String(16), default="back")
    status: Mapped[str] = mapped_column(String(32), default="submitted", index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    fills: Mapped[list[BetFillModel]] = relationship(
        "BetFillModel", back_populates="order", cascade="all, delete-orphan"
    )


class BetFillModel(Base, TimestampMixin):
    """Execution fill for a bet order."""

    __tablename__ = "bets_fills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fill_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    order_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("bets_orders.order_id"), index=True, nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped[BetOrderModel] = relationship("BetOrderModel", back_populates="fills")


class BetSettlementModel(Base, TimestampMixin):
    """Settlement result for a bet."""

    __tablename__ = "bets_settlements"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    settlement_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    fill_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("bets_fills.fill_id"), index=True, nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)  # win/loss/push/void/dead_heat
    payout: Mapped[float] = mapped_column(Float, nullable=False)
    profit: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authority: Mapped[str] = mapped_column(String(128), default="")  # who settled it


class BankrollLedgerModel(Base, TimestampMixin):
    """Append-only bankroll ledger — every stake, payout, and adjustment."""

    __tablename__ = "bets_bankroll_ledger"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ledger_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(128), index=True)
    market_id: Mapped[str | None] = mapped_column(String(128), index=True)
    entry_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # stake, payout, refund, commission, adjustment
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    reference_id: Mapped[str | None] = mapped_column(String(128))  # bet/fill/settlement id


class CalibrationReportModel(Base, TimestampMixin):
    """Calibration report for a forecast model over a time window."""

    __tablename__ = "bets_calibration_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    log_loss: Mapped[float | None] = mapped_column(Float)
    brier_score: Mapped[float | None] = mapped_column(Float)
    calibration_slope: Mapped[float | None] = mapped_column(Float)
    calibration_intercept: Mapped[float | None] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
