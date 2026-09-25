"""
Read-only dashboard query service for AXEL.

DashboardQuery exposes paginated, filterable views over stored records.
It enforces a write guard that raises PermissionError if any flush/mutation
is attempted through the session — making it structurally impossible to
accidentally modify data via this interface.

Usage:
    query = DashboardQuery(session)
    proposals = query.search_proposals(symbol="AAPL", limit=20)
"""

from datetime import datetime

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    OrderModel,
    StrategyRegistryModel,
    TradeProposalModel,
)


def _install_write_guard(session: Session) -> None:
    """
    Registers a SQLAlchemy before_flush listener that rejects any pending
    inserts, updates, or deletes on this session. Fires once per instance.
    """

    @event.listens_for(session, "before_flush")
    def _block_writes(sess, flush_context, instances):
        if sess.new or sess.dirty or sess.deleted:
            raise PermissionError(
                "DashboardQuery: write operations are not permitted on a read-only query session. "
                "Use the appropriate service (ApprovalService, ReconciliationEngine, etc.) for mutations."
            )


class DashboardQuery:
    """
    Read-only query service for the AXEL dashboard.

    All public methods return plain dict lists — no ORM objects leak out.
    The session is write-guarded on construction.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        _install_write_guard(session)

    # ── Proposals ───────────────────────────────────────────────────────

    def search_proposals(
        self,
        *,
        symbol: str | None = None,
        section: str | None = None,
        status: str | None = None,
        strategy_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Filtered, paginated list of trade proposals."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        stmt = select(TradeProposalModel).order_by(TradeProposalModel.created_at.desc())
        if symbol:
            stmt = stmt.where(TradeProposalModel.symbol == symbol.upper())
        if section:
            stmt = stmt.where(TradeProposalModel.section == section)
        if status:
            stmt = stmt.where(TradeProposalModel.status == status)
        if strategy_id:
            stmt = stmt.where(TradeProposalModel.strategy_id == strategy_id)
        if since:
            stmt = stmt.where(TradeProposalModel.created_at >= since)
        if until:
            stmt = stmt.where(TradeProposalModel.created_at <= until)
        stmt = stmt.limit(limit).offset(offset)
        rows = self._session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "symbol": r.symbol,
                "section": r.section,
                "side": r.side,
                "entry_price": r.entry_price,
                "stop_loss": r.stop_loss,
                "take_profit": r.take_profit,
                "confidence": r.confidence_score,
                "strategy_id": r.strategy_id,
                "status": r.status,
                "mode": r.mode,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    # ── Orders ───────────────────────────────────────────────────────────

    def get_order_history(
        self,
        *,
        symbol: str | None = None,
        state: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Paginated order history with optional symbol / state filter."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        stmt = select(OrderModel).order_by(OrderModel.created_at.desc())
        if symbol:
            stmt = stmt.where(OrderModel.symbol == symbol.upper())
        if state:
            stmt = stmt.where(OrderModel.state == state)
        if since:
            stmt = stmt.where(OrderModel.created_at >= since)
        stmt = stmt.limit(limit).offset(offset)
        rows = self._session.execute(stmt).scalars().all()
        return [
            {
                "client_order_id": r.client_order_id,
                "broker_order_id": r.broker_order_id,
                "proposal_id": r.proposal_id,
                "symbol": r.symbol,
                "side": r.side,
                "qty": r.qty,
                "type": r.type,
                "state": r.state,
                "filled_qty": r.filled_qty,
                "filled_avg_price": r.filled_avg_price,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    # ── Fills ─────────────────────────────────────────────────────────────

    def get_fill_history(
        self,
        *,
        symbol: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """Paginated fill history for P&L analysis."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        stmt = select(FillModel).order_by(FillModel.timestamp.desc())
        if symbol:
            stmt = stmt.where(FillModel.symbol == symbol.upper())
        if since:
            stmt = stmt.where(FillModel.timestamp >= since)
        stmt = stmt.limit(limit).offset(offset)
        rows = self._session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "client_order_id": r.client_order_id,
                "symbol": r.symbol,
                "side": r.side,
                "qty": r.qty,
                "price": r.price,
                "fee": r.fee,
                "slippage": r.slippage,
                "timestamp": r.timestamp.isoformat(),
            }
            for r in rows
        ]

    # ── Equity snapshots ──────────────────────────────────────────────────

    def get_equity_snapshots(
        self,
        *,
        section: str = "global",
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Ordered equity snapshots for charting (ascending by time)."""
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        stmt = (
            select(EquitySnapshotModel)
            .where(EquitySnapshotModel.section == section)
            .order_by(EquitySnapshotModel.timestamp.asc())
        )
        if since:
            stmt = stmt.where(EquitySnapshotModel.timestamp >= since)
        stmt = stmt.limit(limit)
        rows = self._session.execute(stmt).scalars().all()
        return [
            {
                "timestamp": r.timestamp.isoformat(),
                "section": r.section,
                "nav": r.nav,
                "cash": r.cash,
                "gross_exposure": r.gross_exposure,
                "drawdown_pct": r.drawdown_pct,
                "high_water_mark": r.high_water_mark,
                "realized_pnl": r.realized_pnl,
                "unrealized_pnl": r.unrealized_pnl,
            }
            for r in rows
        ]

    # ── Audit log ─────────────────────────────────────────────────────────

    def get_audit_log(
        self,
        *,
        event_type: str | None = None,
        actor: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """Paginated audit log with optional type/actor filter."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        stmt = select(AuditLogModel).order_by(AuditLogModel.timestamp.desc())
        if event_type:
            stmt = stmt.where(AuditLogModel.event_type == event_type)
        if actor:
            stmt = stmt.where(AuditLogModel.actor == actor)
        if since:
            stmt = stmt.where(AuditLogModel.timestamp >= since)
        stmt = stmt.limit(limit).offset(offset)
        rows = self._session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat(),
                "event_type": r.event_type,
                "actor": r.actor,
                "payload": r.payload,
            }
            for r in rows
        ]

    # ── Strategy registry ─────────────────────────────────────────────────

    def get_strategy_registry(
        self,
        *,
        section: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Returns all strategy registry entries, optionally filtered."""
        stmt = select(StrategyRegistryModel).order_by(
            StrategyRegistryModel.section, StrategyRegistryModel.strategy_id
        )
        if section:
            stmt = stmt.where(StrategyRegistryModel.section == section)
        if status:
            stmt = stmt.where(StrategyRegistryModel.status == status)
        rows = self._session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "strategy_id": r.strategy_id,
                "section": r.section,
                "status": r.status,
                "parameters_hash": r.parameters_hash,
                "backtest_metrics": r.backtest_metrics,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
