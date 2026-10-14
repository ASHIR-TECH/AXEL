"""
SQLAlchemy ORM models for AXEL.
Covers core tables, time-series entities, audit trails, and execution states.
"""

from datetime import UTC, datetime
from typing import Any, Optional

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


class Instrument(Base, TimestampMixin):
    """Financial instrument metadata across supported venues."""
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    venue: Mapped[str] = mapped_column(String(32), nullable=False)  # alpaca, kraken, kalshi
    section: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(10), default="USD")
    quote_currency: Mapped[str] = mapped_column(String(10), default="USD")
    min_order_size: Mapped[float] = mapped_column(Float, default=1.0)
    tick_size: Mapped[float] = mapped_column(Float, default=0.01)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MarketCalendar(Base, TimestampMixin):
    """Trading sessions, holidays, and market schedule."""
    __tablename__ = "calendars"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    venue: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)  # YYYY-MM-DD
    market_open: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    market_close: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_trading_day: Mapped[bool] = mapped_column(Boolean, default=True)


class OhlcvBar(Base):
    """
    Market price bars. Target for TimescaleDB hypertable partitioning on timestamp.
    Includes both bar event time and ingestion time for point-in-time correctness.
    """
    __tablename__ = "ohlcv_bars"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    ingestion_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    timeframe: Mapped[str] = mapped_column(String(10), default="1m")  # 1m, 1h, 1d
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    vwap: Mapped[float | None] = mapped_column(Float)


class NewsItem(Base):
    """Raw ingested external news, social, or filing text."""
    __tablename__ = "news_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    trust_flag: Mapped[bool] = mapped_column(Boolean, default=False)


class SignalModel(Base):
    """Analyst signal records. Hypertable partition candidate."""
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    section: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    analyst: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    strength: Mapped[float] = mapped_column(Float, nullable=False)
    horizon: Mapped[str] = mapped_column(String(10), default="1d")
    features_ref: Mapped[str | None] = mapped_column(String(128))


class PanelDecisionModel(Base, TimestampMixin):
    """Debate results from the Expert Panel."""
    __tablename__ = "panel_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    section: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bull_summary: Mapped[str | None] = mapped_column(Text)
    bear_summary: Mapped[str | None] = mapped_column(Text)
    synthesis_rationale: Mapped[str | None] = mapped_column(Text)


class TradeProposalModel(Base, TimestampMixin):
    """Concrete trade proposals awaiting deterministic risk evaluation."""
    __tablename__ = "trade_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    section: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    r_multiple_target: Mapped[float] = mapped_column(Float, default=1.5)
    position_size_usd: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), default="paper")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    ttl_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    agent_reasoning: Mapped[str | None] = mapped_column(Text)

    # Relationships
    risk_decision: Mapped[Optional["RiskDecisionModel"]] = relationship(
        "RiskDecisionModel", back_populates="proposal", uselist=False
    )
    orders: Mapped[list["OrderModel"]] = relationship("OrderModel", back_populates="proposal")


class RiskDecisionModel(Base, TimestampMixin):
    """Deterministic audit record for every proposal evaluated by RiskEngine."""
    __tablename__ = "risk_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("trade_proposals.id"), unique=True, nullable=False
    )
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_qty: Mapped[float] = mapped_column(Float, default=0.0)
    approved_notional_usd: Mapped[float] = mapped_column(Float, default=0.0)
    binding_limit: Mapped[str | None] = mapped_column(String(64))
    reasons: Mapped[Any | None] = mapped_column(JSON, default=list)
    checks_passed: Mapped[Any | None] = mapped_column(JSON, default=list)
    checks_failed: Mapped[Any | None] = mapped_column(JSON, default=list)

    proposal: Mapped["TradeProposalModel"] = relationship(
        "TradeProposalModel", back_populates="risk_decision"
    )


class OrderModel(Base, TimestampMixin):
    """Submitted broker orders and state machine history."""
    __tablename__ = "orders"

    client_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("trade_proposals.id"), index=True, nullable=False
    )
    broker_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="limit")
    limit_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    time_in_force: Mapped[str] = mapped_column(String(10), default="day")
    state: Mapped[str] = mapped_column(String(20), index=True, default="submitted")
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_avg_price: Mapped[float | None] = mapped_column(Float)

    proposal: Mapped["TradeProposalModel"] = relationship("TradeProposalModel", back_populates="orders")
    fills: Mapped[list["FillModel"]] = relationship("FillModel", back_populates="order")


class FillModel(Base):
    """Individual trade execution fills."""
    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.client_order_id"), index=True, nullable=False
    )
    broker_fill_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
        nullable=False,
    )

    order: Mapped["OrderModel"] = relationship("OrderModel", back_populates="fills")


class PositionModel(Base, TimestampMixin):
    """Current open position snapshot per section and symbol."""
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    section: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # LONG, SHORT
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class EquitySnapshotModel(Base):
    """Periodic equity and drawdown snapshots."""
    __tablename__ = "equity_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
        nullable=False,
    )
    section: Mapped[str] = mapped_column(String(20), index=True, nullable=False)  # or 'global'
    nav: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    gross_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    net_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    high_water_mark: Mapped[float] = mapped_column(Float, default=0.0)


class KillSwitchEventModel(Base):
    """Append-only audit trail of kill switch trips and re-arms."""
    __tablename__ = "killswitch_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    section: Mapped[str | None] = mapped_column(String(20))
    action_taken: Mapped[str] = mapped_column(String(64), default="HALT_NEW_ORDERS")
    operator_token_hash: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StrategyRegistryModel(Base, TimestampMixin):
    """Approved strategy definitions and backtest metrics."""
    __tablename__ = "strategy_registry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    section: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    parameters_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    backtest_metrics: Mapped[Any | None] = mapped_column(JSON)


class AgentRunModel(Base):
    """LLM cost, latency, and token observability log."""
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
        nullable=False,
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class AuditLogModel(Base):
    """Append-only system audit log."""
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Any | None] = mapped_column(JSON)


# ── Phase 6 tiny-live tables ─────────────────────────────────────────────────
# Money/quantity columns in the Phase 6 tables are stored as exact decimal
# strings (``String``) rather than floats so that persisted values round-trip
# without binary rounding error, matching the in-memory Decimal contracts in
# ``axel.core.money``. Raw provider payloads and reason/status sets are stored as
# JSON.


class OrderEventModel(Base):
    """Append-only Phase 6 order event log (source of truth for projections)."""

    __tablename__ = "order_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    side: Mapped[str | None] = mapped_column(String(10))
    qty: Mapped[str | None] = mapped_column(String(64))
    filled_qty: Mapped[str | None] = mapped_column(String(64))
    broker_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    order_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    config_version: Mapped[str | None] = mapped_column(String(64))
    fill_id: Mapped[str | None] = mapped_column(String(128), index=True)
    anomaly: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[Any | None] = mapped_column(JSON)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ApprovalModel(Base):
    """Single-use, order-hash-bound approval record (spec §8)."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    order_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    auth_reference: Mapped[str | None] = mapped_column(String(128))
    rationale: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentModel(Base):
    """Durable incident record with severity and deterministic action set."""

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, default="open", nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[Any | None] = mapped_column(JSON)


class ReconciliationRunModel(Base):
    """Result of one reconciliation cycle (spec §10)."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broker_account_id: Mapped[str | None] = mapped_column(String(64))
    healthy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    blocks_new_risk: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mismatch_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reason_codes: Mapped[Any | None] = mapped_column(JSON, default=list)
    details: Mapped[Any | None] = mapped_column(JSON)


class ExecutionMetricModel(Base):
    """Derived, versioned execution metrics for one order (spec §11)."""

    __tablename__ = "execution_metrics"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    requested_qty: Mapped[str] = mapped_column(String(64), nullable=False)
    filled_qty: Mapped[str] = mapped_column(String(64), nullable=False)
    fill_ratio: Mapped[str] = mapped_column(String(64), nullable=False)
    avg_fill_price: Mapped[str | None] = mapped_column(String(64))
    filled_notional: Mapped[str] = mapped_column(String(64), nullable=False)
    fees_total: Mapped[str] = mapped_column(String(64), nullable=False)
    implementation_shortfall_per_share: Mapped[str | None] = mapped_column(String(64))
    implementation_shortfall_filled: Mapped[str | None] = mapped_column(String(64))
    net_implementation_shortfall_filled: Mapped[str | None] = mapped_column(String(64))
    vwap_benchmark: Mapped[str | None] = mapped_column(String(64))
    vwap_slippage_per_share: Mapped[str | None] = mapped_column(String(64))
    vwap_benchmark_source: Mapped[str | None] = mapped_column(String(128))
    vwap_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vwap_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vwap_print_count: Mapped[int | None] = mapped_column(Integer)
    arrival_spread: Mapped[str | None] = mapped_column(String(64))
    cancel_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reject_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ambiguous_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mismatch_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recon_delay_seconds: Mapped[float | None] = mapped_column(Float)
    calculation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[Any | None] = mapped_column(JSON)
    raw: Mapped[Any | None] = mapped_column(JSON)


class LiveConfigModel(Base):
    """Persisted, versioned live activation configuration (spec §4.5)."""

    __tablename__ = "live_configs"

    config_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    environment: Mapped[str] = mapped_column(String(10), nullable=False)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    allowed_section: Mapped[str] = mapped_column(String(20), nullable=False)
    max_live_capital: Mapped[str] = mapped_column(String(64), nullable=False)
    max_position_notional: Mapped[str] = mapped_column(String(64), nullable=False)
    max_order_notional: Mapped[str] = mapped_column(String(64), nullable=False)
    max_daily_loss: Mapped[str] = mapped_column(String(64), nullable=False)
    max_concurrent_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_order_frequency: Mapped[int] = mapped_column(Integer, nullable=False)
    order_frequency_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_symbols: Mapped[Any | None] = mapped_column(JSON, default=list)
    manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    phase5_evidence_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    phase5_gate_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approval_approved_by: Mapped[str | None] = mapped_column(String(64))
    approval_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str | None] = mapped_column(String(128))
    kill_switch_clear: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
