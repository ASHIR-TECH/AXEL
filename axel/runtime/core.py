"""Core worker: deterministic risk gate, paper execution, and accounting.

This is the only runtime module allowed to authorize or execute.  It consumes
pending proposals written by :mod:`axel.runtime.agent`, evaluates them with the
deterministic :class:`~axel.risk.engine.SectionRiskAgent`, and only then writes
orders, fills, positions, equity snapshots and the kill-switch audit trail.

No LLM or agent output can bypass :meth:`SectionRiskAgent.evaluate`; an order is
created exclusively from an ``APPROVED`` :class:`RiskDecision`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.core.contracts import Order, Proposal
from axel.core.ids import generate_client_order_id, generate_id
from axel.core.types import OrderSide, OrderState, OrderType, Section, TimeInForce
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    KillSwitchEventModel,
    OrderModel,
    PositionModel,
    RiskDecisionModel,
    TradeProposalModel,
)
from axel.risk.engine import (
    AccountState,
    OpenPositionInfo,
    SectionRiskAgent,
    SectionRiskState,
)
from axel.risk.killswitch import KillSwitch
from axel.risk.limits import LIMITS
from axel.runtime import audit
from axel.runtime.execution import ExecutionBackend, build_backend
from axel.runtime.paper_feed import latest_price

INITIAL_EQUITY = 10_000.0
SECTION_STARTS: dict[str, float] = {
    "STOCKS": 4_500.0,
    "OPTIONS": 2_500.0,
    "CRYPTO": 1_500.0,
    "FOREX": 1_000.0,
    "PREDICTIONS": 500.0,
}
MAX_HOLD_SECONDS = 300


def _initial_equity() -> float:
    """Opening cash of the paper ledger (matches the broker in Alpaca mode)."""
    start = float(settings.paper_starting_equity)
    return start if start > 0 else INITIAL_EQUITY


def _section_starts() -> dict[str, float]:
    """Per-section starting equity, scaled to the configured ledger size."""
    total = float(sum(SECTION_STARTS.values()))
    scale = _initial_equity() / total
    return {section: value * scale for section, value in SECTION_STARTS.items()}

@dataclass(frozen=True)
class CycleResult:
    evaluated: int
    approved: int
    rejected: int
    orders: int
    closed: int
    nav: float
    drawdown_pct: float
    halted: bool


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _latest_global(session: Session) -> EquitySnapshotModel | None:
    stmt = (
        select(EquitySnapshotModel)
        .where(EquitySnapshotModel.section == "global")
        .order_by(EquitySnapshotModel.timestamp.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _latest_section(session: Session, section: str) -> EquitySnapshotModel | None:
    stmt = (
        select(EquitySnapshotModel)
        .where(EquitySnapshotModel.section == section)
        .order_by(EquitySnapshotModel.timestamp.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _open_positions(session: Session) -> list[PositionModel]:
    stmt = select(PositionModel).where(PositionModel.qty > 0).order_by(PositionModel.created_at.asc())
    return list(session.execute(stmt).scalars())


def _closed_trades(session: Session) -> list[dict[str, object]]:
    rows = session.execute(
        select(AuditLogModel).where(AuditLogModel.event_type == "trade.closed")
    ).scalars()
    return [row.payload if isinstance(row.payload, dict) else {} for row in rows]


def _realized_pnl(session: Session, section: str | None = None) -> float:
    total = 0.0
    for payload in _closed_trades(session):
        if section is None or str(payload.get("section") or "").upper() == section.upper():
            total += float(payload.get("pnl") or 0.0)
    return total


def _section_state(session: Session, section: str) -> tuple[float, float, int]:
    """Return ``(nav, unrealized, open_count)`` for one section."""
    start = _section_starts()[section]
    positions = [row for row in _open_positions(session) if str(row.section).upper() == section]
    unrealized = sum(float(row.unrealized_pnl or 0.0) for row in positions)
    realized = _realized_pnl(session, section)
    nav = start + realized + unrealized
    return nav, unrealized, len(positions)


def _global_nav(session: Session) -> tuple[float, float]:
    """Return ``(nav, unrealized)`` across all sections."""
    nav = 0.0
    unrealized = 0.0
    for section in _section_starts():
        section_nav, section_unrealized, _count = _section_state(session, section)
        nav += section_nav
        unrealized += section_unrealized
    return nav, unrealized


def _gross_exposure(session: Session) -> float:
    return sum(abs(float(row.qty) * float(row.current_price)) for row in _open_positions(session))


def _active_kill(session: Session) -> KillSwitchEventModel | None:
    stmt = (
        select(KillSwitchEventModel)
        .order_by(KillSwitchEventModel.timestamp.desc())
        .limit(1)
    )
    row = session.execute(stmt).scalar_one_or_none()
    return row if row is not None and row.is_active else None


def _mark_positions(session: Session) -> None:
    for position in _open_positions(session):
        price = latest_price(session, position.symbol)
        if price is None:
            continue
        sign = 1.0 if str(position.side).upper().startswith("L") else -1.0
        position.current_price = price
        position.unrealized_pnl = (price - float(position.entry_price)) * float(position.qty) * sign


def _proposal_for_symbol(session: Session, symbol: str) -> TradeProposalModel | None:
    stmt = (
        select(TradeProposalModel)
        .where(
            TradeProposalModel.symbol == symbol,
            TradeProposalModel.status == "filled",
        )
        .order_by(TradeProposalModel.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _close_position(
    session: Session,
    position: PositionModel,
    price: float,
    now: datetime,
    *,
    reason: str,
    backend: ExecutionBackend,
) -> float | None:
    """Close one position through ``backend``; returns realized P&L or ``None``."""
    sign = 1.0 if str(position.side).upper().startswith("L") else -1.0
    qty = float(position.qty)
    proposal = _proposal_for_symbol(session, position.symbol)
    if proposal is None:
        return None

    closing_id = generate_client_order_id(proposal.id, attempt=2)
    side = OrderSide.SELL if sign > 0 else OrderSide.BUY
    order = Order(
        client_order_id=closing_id,
        proposal_id=proposal.id,
        symbol=position.symbol,
        side=side,
        qty=qty,
        type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        state=OrderState.SUBMITTED,
        strategy_id=proposal.strategy_id,
        decision_ref_price=price,
        decision_ref_ts=now,
    )
    report = backend.execute(order, reference_price=price, now=now)
    if report is None:
        audit.record(
            session,
            event_type="order.deferred",
            actor="axel.execution.broker",
            message=f"close {position.symbol} not filled · {reason}",
            logger="axel.execution.broker",
            channel="EXEC",
            level="WARN",
            timestamp=now,
            symbol=position.symbol,
            reason=reason,
        )
        return None

    qty = report.qty
    exit_price = report.price
    pnl = (exit_price - float(position.entry_price)) * qty * sign
    session.add(
        OrderModel(
            client_order_id=closing_id,
            proposal_id=proposal.id,
            broker_order_id=report.broker_order_id,
            symbol=position.symbol,
            side=side.value,
            qty=qty,
            type=order.type.value,
            state=report.state,
            filled_qty=qty,
            filled_avg_price=exit_price,
            created_at=now,
        )
    )
    session.add(
        FillModel(
            id=generate_id("fill"),
            client_order_id=closing_id,
            symbol=position.symbol,
            side=side.value,
            qty=qty,
            price=exit_price,
            fee=report.fee,
            slippage=abs(exit_price - price),
            timestamp=now,
        )
    )
    audit.record(
        session,
        event_type="trade.closed",
        actor="axel.execution.broker",
        message=(
            f"close {position.symbol} {position.side} {qty:g}@{exit_price:.2f} · "
            f"pnl={pnl:+.2f} · {reason}"
        ),
        logger="axel.execution.broker",
        channel="EXEC",
        level="FILLED",
        timestamp=now,
        section=str(position.section).upper(),
        symbol=position.symbol,
        qty=qty,
        entry=float(position.entry_price),
        exit=exit_price,
        pnl=round(pnl, 6),
        win=pnl > 0,
        reason=reason,
    )
    session.delete(position)
    return pnl


def manage_positions(
    session: Session,
    *,
    now: datetime | None = None,
    backend: ExecutionBackend | None = None,
) -> int:
    """Mark and exit positions at stop, target, or the paper max-hold horizon."""
    stamp = now or datetime.now(UTC)
    runner = backend or build_backend()
    closed = 0
    for position in _open_positions(session):
        price = latest_price(session, position.symbol)
        if price is None:
            continue
        proposal = _proposal_for_symbol(session, position.symbol)
        long = str(position.side).upper().startswith("L")
        reason = None
        if proposal is not None:
            stop = float(proposal.stop_loss)
            target = float(proposal.take_profit)
            if long and price <= stop:
                reason = "stop"
            elif long and price >= target:
                reason = "target"
            elif not long and price >= stop:
                reason = "stop"
            elif not long and price <= target:
                reason = "target"
        held = (stamp - _utc(position.created_at)).total_seconds()
        if reason is None and held >= MAX_HOLD_SECONDS:
            reason = "max-hold"
        if reason is None:
            continue
        result = _close_position(
            session, position, price, stamp, reason=reason, backend=runner
        )
        if result is not None:
            closed += 1
    return closed


def _execute(
    session: Session,
    proposal: TradeProposalModel,
    decision: RiskDecisionModel,
    now: datetime,
    backend: ExecutionBackend,
) -> bool:
    """Persist an approved order and the fill reported by ``backend``."""
    qty = float(decision.approved_qty)
    if qty <= 0:
        proposal.status = "rejected"
        return False
    client_order_id = generate_client_order_id(proposal.id)
    side = OrderSide.BUY if str(proposal.side).lower() in {"buy", "long"} else OrderSide.SELL
    live_venue = backend.name == "alpaca"
    order = Order(
        client_order_id=client_order_id,
        proposal_id=proposal.id,
        symbol=proposal.symbol,
        side=side,
        qty=qty,
        type=OrderType.MARKET if live_venue else OrderType.LIMIT,
        limit_price=None if live_venue else float(proposal.entry_price),
        time_in_force=TimeInForce.DAY,
        state=OrderState.SUBMITTED,
        risk_decision_id=decision.id,
        strategy_id=proposal.strategy_id,
        decision_ref_price=float(proposal.entry_price),
        decision_ref_ts=now,
    )
    report = backend.execute(order, reference_price=float(proposal.entry_price), now=now)
    if report is None:
        # Nothing filled (venue closed or order unfilled): leave it pending so
        # the next cycle can decide again instead of inventing a fill.
        proposal.status = "pending"
        audit.record(
            session,
            event_type="order.deferred",
            actor="axel.execution.broker",
            message=(
                f"order not filled {proposal.symbol} {side.value.upper()} "
                f"{qty:g} · {backend.name}"
            ),
            logger="axel.execution.broker",
            channel="EXEC",
            level="WARN",
            timestamp=now,
            proposal_id=proposal.id,
            symbol=proposal.symbol,
            backend=backend.name,
        )
        return False

    qty = report.qty
    fill_price = report.price
    order_row = OrderModel(
        client_order_id=order.client_order_id,
        proposal_id=order.proposal_id,
        broker_order_id=report.broker_order_id,
        symbol=order.symbol,
        side=side.value,
        qty=qty,
        type=order.type.value,
        limit_price=order.limit_price,
        time_in_force="day",
        state=report.state,
        filled_qty=qty,
        filled_avg_price=fill_price,
        created_at=now,
    )
    session.add(order_row)
    session.add(
        FillModel(
            id=generate_id("fill"),
            client_order_id=order.client_order_id,
            symbol=proposal.symbol,
            side=side.value,
            qty=qty,
            price=fill_price,
            fee=report.fee,
            slippage=abs(fill_price - float(proposal.entry_price)),
            timestamp=now,
        )
    )
    order_row.state = report.state
    order_row.filled_qty = qty
    order_row.filled_avg_price = fill_price

    section = str(proposal.section).upper()
    # Production sessions disable autoflush, so a position added earlier in this
    # same cycle is still pending and invisible to ``session.get``. Flush first
    # so two fills for one symbol merge instead of inserting a duplicate key.
    session.flush()
    position = session.get(PositionModel, f"pos_{proposal.symbol}")
    if position is None:
        session.add(
            PositionModel(
                id=f"pos_{proposal.symbol}",
                section=section,
                symbol=proposal.symbol,
                side="LONG" if side is OrderSide.BUY else "SHORT",
                qty=qty,
                entry_price=fill_price,
                current_price=fill_price,
                unrealized_pnl=0.0,
                created_at=now,
            )
        )
    else:
        total = float(position.qty) + qty
        position.entry_price = (
            float(position.entry_price) * float(position.qty) + fill_price * qty
        ) / total
        position.qty = total
        position.current_price = fill_price

    proposal.status = "filled"
    audit.record(
        session,
        event_type="order.filled",
        actor="axel.execution.broker",
        message=(
            f"fill {order.client_order_id} {proposal.symbol} {side.value.upper()} "
            f"{qty:g}@{fill_price:.2f} · {section} · {proposal.strategy_id}"
        ),
        logger="axel.execution.broker",
        channel="EXEC",
        level="FILLED",
        timestamp=now,
        proposal_id=proposal.id,
        order_id=order.client_order_id,
        section=section,
        symbol=proposal.symbol,
    )
    return True


def _evaluate_pending(
    session: Session,
    *,
    now: datetime,
    engine: SectionRiskAgent,
    halted: bool,
    drawdown_pct: float,
    backend: ExecutionBackend,
) -> tuple[int, int, int, int]:
    """Evaluate pending proposals; returns ``(evaluated, approved, rejected, orders)``."""
    nav, _unrealized = _global_nav(session)
    cash = max(0.0, nav - _gross_exposure(session))
    positions = _open_positions(session)
    stmt = (
        select(TradeProposalModel)
        .where(TradeProposalModel.status == "pending")
        .order_by(TradeProposalModel.created_at.asc())
    )
    evaluated = approved = rejected = orders = 0
    for proposal in list(session.execute(stmt).scalars()):
        expires = _utc(proposal.ttl_expires_at)
        if expires <= now:
            proposal.status = "expired"
            audit.record(
                session,
                event_type="proposal.expired",
                actor="axel.risk.engine",
                message=f"proposal {proposal.id} expired before evaluation",
                logger="axel.risk.engine",
                channel="RISK",
                level="WARN",
                timestamp=now,
                proposal_id=proposal.id,
            )
            continue
        section = str(proposal.section).upper()
        section_nav, section_unrealized, section_count = _section_state(session, section)
        nav, _unrealized = _global_nav(session)
        cash = max(0.0, nav - _gross_exposure(session))
        positions = _open_positions(session)
        contract = Proposal(
            id=proposal.id,
            ts=_utc(proposal.created_at),
            section=Section(section.lower()),
            symbol=proposal.symbol,
            side=OrderSide.BUY
            if str(proposal.side).lower() in {"buy", "long"}
            else OrderSide.SELL,
            entry=float(proposal.entry_price),
            stop=float(proposal.stop_loss),
            target=float(proposal.take_profit),
            strategy_id=proposal.strategy_id,
            panel_confidence=float(proposal.confidence_score),
            win_rate=0.55,
            avg_win_loss_ratio=float(proposal.r_multiple_target or 2.0),
            rationale_ref=proposal.agent_reasoning,
            ttl_seconds=300,
        )
        open_info = [
            OpenPositionInfo(
                symbol=row.symbol,
                section=Section(str(row.section).lower()),
                side=OrderSide.BUY if str(row.side).upper().startswith("L") else OrderSide.SELL,
                notional_usd=float(row.qty) * float(row.current_price),
                correlation_with_proposal=0.1,
            )
            for row in positions
            if str(row.section).upper() == section
        ]
        decision = engine.evaluate(
            contract,
            AccountState(
                total_equity=nav,
                cash=cash,
                global_drawdown_pct=drawdown_pct,
                kill_switch_active=halted,
            ),
            SectionRiskState(
                section=Section(section.lower()),
                section_nav=section_nav,
                start_of_day_equity=_section_starts()[section],
                realized_pnl=_realized_pnl(session, section),
                unrealized_pnl=section_unrealized,
                active_positions_count=section_count,
            ),
            open_info,
        )
        decision_row = RiskDecisionModel(
            id=decision.id,
            proposal_id=proposal.id,
            approved=decision.approved,
            verdict=decision.verdict.value,
            approved_qty=decision.approved_qty,
            approved_notional_usd=decision.approved_notional_usd,
            binding_limit=decision.binding_limit,
            reasons=list(decision.reasons),
            checks_passed=list(decision.checks_passed),
            checks_failed=list(decision.checks_failed),
            created_at=now,
        )
        session.add(decision_row)
        evaluated += 1
        proposal.status = "approved" if decision.approved else "rejected"
        level = "APPROVED" if decision.approved else "VETO"
        audit.record(
            session,
            event_type="risk.decision",
            actor="axel.risk.engine",
            message=(
                f"{decision.verdict.value.upper()} {section} {proposal.symbol} "
                f"qty={decision.approved_qty:g} binding={decision.binding_limit or 'n/a'}"
                + (
                    ""
                    if decision.approved
                    else f" · {decision.checks_failed[0] if decision.checks_failed else 'rejected'}"
                )
            ),
            logger="axel.risk.engine",
            channel="RISK",
            level=level,
            timestamp=now,
            proposal_id=proposal.id,
            verdict=decision.verdict.value,
            checks_passed=list(decision.checks_passed),
            checks_failed=list(decision.checks_failed),
        )
        if decision.approved:
            approved += 1
            if _execute(session, proposal, decision_row, now, backend):
                orders += 1
        else:
            rejected += 1
    return evaluated, approved, rejected, orders


def _reconcile_broker(
    session: Session,
    backend: ExecutionBackend,
    *,
    now: datetime,
    nav: float,
) -> None:
    """Log when the local ledger drifts from the broker's paper account.

    The backend only exposes ``account_summary`` for real venues; the internal
    backend has no broker to compare against.
    """
    summary = getattr(backend, "account_summary", None)
    if summary is None:
        return
    data = summary()
    if not data:
        return
    try:
        broker_equity = float(data.get("total_equity") or 0.0)
        broker_cash = float(data.get("cash") or 0.0)
    except (TypeError, ValueError):
        return
    if broker_equity <= 0:
        return
    delta = nav - broker_equity
    if abs(delta) <= max(1.0, broker_equity * 0.01):
        return
    audit.record(
        session,
        event_type="paper.reconcile",
        actor="axel.runtime.core",
        message=(
            f"ledger drift · local_nav={nav:.2f} broker_equity={broker_equity:.2f} "
            f"delta={delta:+.2f} · broker_cash={broker_cash:.2f}"
        ),
        logger="axel.runtime.core",
        channel="SYS",
        level="WARN",
        timestamp=now,
        local_nav=round(nav, 6),
        broker_equity=round(broker_equity, 6),
        broker_cash=round(broker_cash, 6),
        delta=round(delta, 6),
    )


def _write_snapshots(
    session: Session, *, now: datetime, halted: bool
) -> tuple[float, float]:
    nav, unrealized = _global_nav(session)
    previous = _latest_global(session)
    start_equity = _initial_equity()
    previous_hwm = float(previous.high_water_mark) if previous is not None else start_equity
    hwm = max(start_equity, previous_hwm, nav)
    drawdown = (hwm - nav) / hwm if hwm > 0 else 0.0
    stamp = int(now.timestamp() * 1000)
    gross = _gross_exposure(session)
    session.add(
        EquitySnapshotModel(
            id=f"eq_global_{stamp}",
            timestamp=now,
            section="global",
            nav=round(nav, 6),
            cash=round(max(0.0, nav - gross), 6),
            gross_exposure=round(gross, 6),
            net_exposure=round(gross, 6),
            realized_pnl=round(_realized_pnl(session), 6),
            unrealized_pnl=round(unrealized, 6),
            drawdown_pct=round(drawdown, 6),
            high_water_mark=round(hwm, 6),
        )
    )
    for section, start in _section_starts().items():
        section_nav, section_unrealized, _count = _section_state(session, section)
        previous_section = _latest_section(session, section)
        section_hwm = max(
            start,
            float(previous_section.high_water_mark) if previous_section is not None else 0.0,
            section_nav,
        )
        section_gross = sum(
            abs(float(row.qty) * float(row.current_price))
            for row in _open_positions(session)
            if str(row.section).upper() == section
        )
        session.add(
            EquitySnapshotModel(
                id=f"eq_{section.lower()}_{stamp}",
                timestamp=now,
                section=section,
                nav=round(section_nav, 6),
                cash=round(max(0.0, section_nav - section_gross), 6),
                gross_exposure=round(section_gross, 6),
                net_exposure=round(section_gross, 6),
                realized_pnl=round(_realized_pnl(session, section), 6),
                unrealized_pnl=round(section_unrealized, 6),
                drawdown_pct=round(
                    (section_hwm - section_nav) / section_hwm if section_hwm > 0 else 0.0,
                    6,
                ),
                high_water_mark=round(section_hwm, 6),
            )
        )
    if halted:
        audit.record(
            session,
            event_type="runtime.halted",
            actor="axel.risk.killswitch",
            message="new orders blocked by the global kill switch",
            logger="axel.risk.killswitch",
            channel="RISK",
            level="CRITICAL",
            timestamp=now,
        )
    return nav, drawdown


def core_once(
    session: Session,
    *,
    now: datetime | None = None,
    engine: SectionRiskAgent | None = None,
    killswitch: KillSwitch | None = None,
    backend: ExecutionBackend | None = None,
) -> CycleResult:
    """Run one deterministic core cycle: mark, exit, evaluate, execute, snapshot."""
    stamp = now or datetime.now(UTC)
    risk_engine = engine or SectionRiskAgent(LIMITS)
    switch = killswitch or KillSwitch()
    runner = backend or build_backend()

    _mark_positions(session)
    closed = manage_positions(session, now=stamp, backend=runner)

    nav_before, _unrealized = _global_nav(session)
    previous = _latest_global(session)
    start_equity = _initial_equity()
    previous_hwm = float(previous.high_water_mark) if previous is not None else start_equity
    switch.set_high_water_mark(max(start_equity, previous_hwm))
    halted, drawdown = switch.check_equity(nav_before)
    active = _active_kill(session)
    if halted and active is None:
        reason = switch.halt_reason or "global drawdown breach"
        session.add(
            KillSwitchEventModel(
                id=generate_id("kill"),
                timestamp=stamp,
                reason=reason,
                action_taken="HALT_NEW_ORDERS",
                is_active=True,
            )
        )
        audit.record(
            session,
            event_type="killswitch.tripped",
            actor="axel.risk.killswitch",
            message=f"KILL-SWITCH ACTIVE — {reason}",
            logger="axel.risk.killswitch",
            channel="SYS",
            level="CRITICAL",
            timestamp=stamp,
        )
    halted = halted or active is not None

    evaluated, approved, rejected, orders = _evaluate_pending(
        session,
        now=stamp,
        engine=risk_engine,
        halted=halted,
        drawdown_pct=drawdown,
        backend=runner,
    )
    _mark_positions(session)
    nav, drawdown = _write_snapshots(session, now=stamp, halted=halted)
    _reconcile_broker(session, runner, now=stamp, nav=nav)

    return CycleResult(
        evaluated=evaluated,
        approved=approved,
        rejected=rejected,
        orders=orders,
        closed=closed,
        nav=round(nav, 6),
        drawdown_pct=round(drawdown, 6),
        halted=halted,
    )


__all__ = [
    "INITIAL_EQUITY",
    "MAX_HOLD_SECONDS",
    "SECTION_STARTS",
    "CycleResult",
    "core_once",
    "manage_positions",
]
