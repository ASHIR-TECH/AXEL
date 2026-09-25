"""
Read-only decision-log and portfolio projections for the Phase 4 dashboard.

All functions are SELECT-only — no INSERT, UPDATE, or DELETE paths exist.
They are the single source of truth for the "why did it take that trade?" audit trail.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from axel.db.models import (
    AgentRunModel,
    EquitySnapshotModel,
    FillModel,
    KillSwitchEventModel,
    OrderModel,
    PanelDecisionModel,
    PositionModel,
    RiskDecisionModel,
    SignalModel,
    TradeProposalModel,
)

# ── Core lineage view ───────────────────────────────────────────────────────

def recent_decisions(session: Session, limit: int = 100) -> list[dict[str, object]]:
    """Return traceable proposal-to-risk records; deliberately has no mutation API."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    query = (
        select(TradeProposalModel, RiskDecisionModel)
        .outerjoin(RiskDecisionModel, RiskDecisionModel.proposal_id == TradeProposalModel.id)
        .order_by(TradeProposalModel.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "proposal_id": proposal.id,
            "symbol": proposal.symbol,
            "strategy_id": proposal.strategy_id,
            "confidence": proposal.confidence_score,
            "status": proposal.status,
            "risk_verdict": decision.verdict if decision else None,
            "risk_reasons": decision.reasons if decision else [],
        }
        for proposal, decision in session.execute(query)
    ]


def proposal_lineage(session: Session, proposal_id: str) -> dict[str, object]:
    """
    Returns the complete decision chain for a single proposal:
      signals → panel decision → proposal → risk decision → orders → fills

    This is the answer to "why did AXEL take (or not take) that trade?"
    """
    # Proposal
    proposal = session.get(TradeProposalModel, proposal_id)
    if proposal is None:
        raise KeyError(f"Proposal '{proposal_id}' not found.")

    # Risk decision (1:1)
    risk_stmt = select(RiskDecisionModel).where(RiskDecisionModel.proposal_id == proposal_id)
    risk_decision = session.execute(risk_stmt).scalar_one_or_none()

    # Panel decision (matched by proposal_id stored on panel record)
    panel_stmt = select(PanelDecisionModel).where(PanelDecisionModel.proposal_id == proposal_id)
    panel = session.execute(panel_stmt).scalar_one_or_none()

    # Analyst signals — matched by symbol + section within a short window around the proposal
    signal_stmt = (
        select(SignalModel)
        .where(
            SignalModel.symbol == proposal.symbol,
            SignalModel.section == proposal.section,
            SignalModel.ts >= proposal.created_at - timedelta(minutes=30),
            SignalModel.ts <= proposal.created_at + timedelta(minutes=5),
        )
        .order_by(SignalModel.ts.asc())
    )
    signals = session.execute(signal_stmt).scalars().all()

    # Orders spawned by this proposal
    order_stmt = (
        select(OrderModel)
        .where(OrderModel.proposal_id == proposal_id)
        .order_by(OrderModel.created_at.asc())
    )
    orders = session.execute(order_stmt).scalars().all()

    # Fills for those orders
    fills: list[dict] = []
    for order in orders:
        fill_stmt = (
            select(FillModel)
            .where(FillModel.client_order_id == order.client_order_id)
            .order_by(FillModel.timestamp.asc())
        )
        order_fills = session.execute(fill_stmt).scalars().all()
        fills.extend(
            {
                "fill_id": f.id,
                "client_order_id": f.client_order_id,
                "qty": f.qty,
                "price": f.price,
                "fee": f.fee,
                "slippage": f.slippage,
                "timestamp": f.timestamp.isoformat(),
            }
            for f in order_fills
        )

    return {
        "proposal": {
            "id": proposal.id,
            "symbol": proposal.symbol,
            "section": proposal.section,
            "side": proposal.side,
            "entry_price": proposal.entry_price,
            "stop_loss": proposal.stop_loss,
            "take_profit": proposal.take_profit,
            "confidence": proposal.confidence_score,
            "strategy_id": proposal.strategy_id,
            "mode": proposal.mode,
            "status": proposal.status,
            "created_at": proposal.created_at.isoformat(),
        },
        "signals": [
            {
                "id": s.id,
                "analyst": s.analyst,
                "direction": s.direction,
                "strength": s.strength,
                "horizon": s.horizon,
                "ts": s.ts.isoformat(),
            }
            for s in signals
        ],
        "panel_decision": {
            "id": panel.id,
            "confidence": panel.confidence,
            "bull_summary": panel.bull_summary,
            "bear_summary": panel.bear_summary,
            "synthesis_rationale": panel.synthesis_rationale,
        } if panel else None,
        "risk_decision": {
            "id": risk_decision.id,
            "approved": risk_decision.approved,
            "verdict": risk_decision.verdict,
            "approved_qty": risk_decision.approved_qty,
            "approved_notional_usd": risk_decision.approved_notional_usd,
            "binding_limit": risk_decision.binding_limit,
            "checks_passed": risk_decision.checks_passed,
            "checks_failed": risk_decision.checks_failed,
            "reasons": risk_decision.reasons,
        } if risk_decision else None,
        "orders": [
            {
                "client_order_id": o.client_order_id,
                "broker_order_id": o.broker_order_id,
                "side": o.side,
                "qty": o.qty,
                "type": o.type,
                "state": o.state,
                "filled_qty": o.filled_qty,
                "filled_avg_price": o.filled_avg_price,
            }
            for o in orders
        ],
        "fills": fills,
    }


# ── Portfolio / equity views ────────────────────────────────────────────────

def equity_curve(
    session: Session, section: str = "global", limit: int = 500
) -> list[dict[str, object]]:
    """
    Returns ordered equity snapshot rows for charting P&L, drawdown, and exposure.
    Suitable for direct JSON serialization to a Grafana/frontend datasource.
    """
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    stmt = (
        select(EquitySnapshotModel)
        .where(EquitySnapshotModel.section == section)
        .order_by(EquitySnapshotModel.timestamp.asc())
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return [
        {
            "timestamp": r.timestamp.isoformat(),
            "section": r.section,
            "nav": r.nav,
            "cash": r.cash,
            "gross_exposure": r.gross_exposure,
            "net_exposure": r.net_exposure,
            "realized_pnl": r.realized_pnl,
            "unrealized_pnl": r.unrealized_pnl,
            "drawdown_pct": r.drawdown_pct,
            "high_water_mark": r.high_water_mark,
        }
        for r in rows
    ]


def open_positions_snapshot(session: Session) -> list[dict[str, object]]:
    """Returns all current open position records."""
    stmt = select(PositionModel).order_by(PositionModel.section, PositionModel.symbol)
    rows = session.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "section": r.section,
            "symbol": r.symbol,
            "side": r.side,
            "qty": r.qty,
            "entry_price": r.entry_price,
            "current_price": r.current_price,
            "unrealized_pnl": r.unrealized_pnl,
            "realized_pnl": r.realized_pnl,
        }
        for r in rows
    ]


# ── Audit / operational views ───────────────────────────────────────────────

def killswitch_history(session: Session, limit: int = 50) -> list[dict[str, object]]:
    """Returns ordered kill switch event records (trips and re-arms)."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    stmt = (
        select(KillSwitchEventModel)
        .order_by(KillSwitchEventModel.timestamp.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "reason": r.reason,
            "section": r.section,
            "action_taken": r.action_taken,
            "is_active": r.is_active,
        }
        for r in rows
    ]


def agent_run_summary(session: Session, hours: int = 24) -> dict[str, object]:
    """
    Aggregates LLM cost and token usage over the last N hours.
    Used for budget monitoring on the dashboard.
    """
    if not 1 <= hours <= 168:
        raise ValueError("hours must be between 1 and 168")
    since = datetime.now(UTC) - timedelta(hours=hours)
    stmt = select(AgentRunModel).where(AgentRunModel.timestamp >= since)
    rows = session.execute(stmt).scalars().all()

    total_cost = sum(r.cost_usd for r in rows)
    total_prompt_tokens = sum(r.prompt_tokens for r in rows)
    total_completion_tokens = sum(r.completion_tokens for r in rows)
    avg_latency_ms = (
        round(sum(r.latency_ms for r in rows) / len(rows)) if rows else 0
    )

    # Per-agent breakdown
    by_agent: dict[str, dict] = {}
    for r in rows:
        entry = by_agent.setdefault(r.agent_name, {"runs": 0, "cost_usd": 0.0, "tokens": 0})
        entry["runs"] += 1
        entry["cost_usd"] += r.cost_usd
        entry["tokens"] += r.prompt_tokens + r.completion_tokens

    return {
        "window_hours": hours,
        "since": since.isoformat(),
        "total_runs": len(rows),
        "total_cost_usd": round(total_cost, 6),
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "avg_latency_ms": avg_latency_ms,
        "by_agent": by_agent,
    }
