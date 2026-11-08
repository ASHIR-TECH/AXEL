"""Tests for the deterministic paper runtime workers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import axel.db.models
import axel.portfolio.db_models  # noqa: F401 — register tables
from axel.core.contracts import Proposal
from axel.core.ids import generate_client_order_id
from axel.core.types import OrderSide, Section, TradingMode
from axel.dashboard.projector import build_snapshot
from axel.db.base import Base
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    KillSwitchEventModel,
    OhlcvBar,
    OrderModel,
    PositionModel,
    RiskDecisionModel,
    SignalModel,
    TradeProposalModel,
)
from axel.risk.killswitch import KillSwitch
from axel.runtime.agent import agent_once, generate_signals
from axel.runtime.core import core_once, manage_positions
from axel.runtime.ingest import ingest_once
from axel.runtime.paper_feed import (
    all_symbols,
    bar_id,
    next_bar,
    persist_bar,
)


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def killswitch(tmp_path):
    return KillSwitch(state_file_path=tmp_path / "killswitch.json")


def _seed_bars(session: Session, now: datetime, cycles: int = 10) -> None:
    for index in range(cycles):
        ingest_once(
            session,
            now=now + timedelta(seconds=index * 5),
            interval_seconds=5,
            seed=7,
        )


def _seed_proposal(
    session: Session,
    now: datetime,
    *,
    confidence: float = 0.8,
    entry: float = 100.0,
    stop: float = 98.0,
    target: float = 104.0,
    side: OrderSide = OrderSide.BUY,
    symbol: str = "AAPL",
) -> TradeProposalModel:
    proposal = Proposal(
        section=Section.STOCKS,
        symbol=symbol,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        strategy_id="TEST_STRATEGY",
        panel_confidence=confidence,
        win_rate=0.55,
        avg_win_loss_ratio=2.0,
        mode=TradingMode.PAPER,
        ttl_seconds=300,
    )
    row = TradeProposalModel(
        id=proposal.id,
        section="STOCKS",
        symbol=proposal.symbol,
        side=side.value,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        r_multiple_target=2.0,
        position_size_usd=0.0,
        confidence_score=confidence,
        strategy_id=proposal.strategy_id,
        mode="paper",
        status="pending",
        ttl_expires_at=now + timedelta(seconds=300),
        agent_reasoning="unit test",
        created_at=now,
    )
    session.add(row)
    session.commit()
    return row


def test_paper_feed_is_deterministic_and_idempotent(session_factory):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        first = next_bar(session, "AAPL", now, interval_seconds=5, seed=7)
        second = next_bar(session, "AAPL", now, interval_seconds=5, seed=7)
        other = next_bar(session, "MSFT", now, interval_seconds=5, seed=7)
        assert first == second
        assert first.symbol != other.symbol
        assert first.low <= first.open <= first.high
        assert first.low <= first.close <= first.high

        persist_bar(session, first, interval_seconds=5)
        persist_bar(session, second, interval_seconds=5)
        session.commit()
        count = session.execute(select(func.count()).select_from(OhlcvBar)).scalar_one()
        assert count == 1
        assert session.get(OhlcvBar, bar_id("AAPL", now, 5)) is not None


def test_ingest_writes_every_symbol_and_periodic_macro(session_factory):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        first = ingest_once(session, now=now, interval_seconds=5, seed=7)
        session.commit()
        second = ingest_once(session, now=now, interval_seconds=5, seed=7)
        session.commit()

        assert first == {"bars": len(all_symbols()), "news": 4}
        assert second == {"bars": len(all_symbols()), "news": 0}
        assert session.execute(select(func.count()).select_from(OhlcvBar)).scalar_one() == len(
            all_symbols()
        )
        logs = session.execute(
            select(AuditLogModel).where(AuditLogModel.event_type == "market.ingest")
        ).scalars().all()
        assert len(logs) == 2


def test_agent_proposes_but_never_orders_or_decides(session_factory):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        _seed_bars(session, now, cycles=10)
        session.commit()
        result = agent_once(session, now=now + timedelta(seconds=50))
        session.commit()

        assert result["signals"] > 0
        assert result["proposals"] > 0
        proposals = session.execute(select(TradeProposalModel)).scalars().all()
        assert proposals
        assert all(row.status == "pending" for row in proposals)
        assert session.execute(select(func.count()).select_from(RiskDecisionModel)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(OrderModel)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(FillModel)).scalar_one() == 0


def test_agent_signals_are_throttled_and_mapped(session_factory):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        _seed_bars(session, now, cycles=10)
        session.commit()
        first = generate_signals(session, now=now + timedelta(seconds=50))
        session.commit()
        second = generate_signals(session, now=now + timedelta(seconds=55))
        session.commit()

        assert first > 0
        # Unchanged signals are not rewritten on every cycle.
        assert second < first
        signals = session.execute(select(SignalModel)).scalars().all()
        assert {row.section for row in signals} <= {"STOCKS", "OPTIONS", "CRYPTO", "FOREX", "PREDICTIONS"}


def test_core_approves_executes_and_snapshots(session_factory, killswitch):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        _seed_bars(session, now, cycles=10)
        proposal = _seed_proposal(session, now + timedelta(seconds=50))
        session.commit()

        result = core_once(session, now=now + timedelta(seconds=50), killswitch=killswitch)
        session.commit()

        assert result.evaluated == 1
        assert result.approved == 1
        assert result.orders == 1
        decision = session.execute(select(RiskDecisionModel)).scalar_one()
        assert decision.verdict == "approved"
        assert decision.approved_qty > 0
        order = session.get(OrderModel, generate_client_order_id(proposal.id))
        assert order is not None
        assert order.state == "filled"
        assert order.filled_qty == decision.approved_qty
        fill = session.execute(select(FillModel)).scalar_one()
        assert fill.symbol == "AAPL"
        position = session.execute(select(PositionModel)).scalar_one()
        assert position.side == "LONG"
        session.refresh(proposal)
        assert proposal.status == "filled"

        snapshots = session.execute(select(EquitySnapshotModel)).scalars().all()
        assert {row.section for row in snapshots} == {
            "global",
            "STOCKS",
            "OPTIONS",
            "CRYPTO",
            "FOREX",
            "PREDICTIONS",
        }
        global_snapshot = next(row for row in snapshots if row.section == "global")
        assert global_snapshot.nav == pytest.approx(result.nav)
        assert global_snapshot.high_water_mark >= global_snapshot.nav


def test_core_rejects_without_creating_an_order(session_factory, killswitch):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        _seed_bars(session, now, cycles=10)
        proposal = _seed_proposal(session, now + timedelta(seconds=50), confidence=0.1)
        session.commit()

        result = core_once(session, now=now + timedelta(seconds=50), killswitch=killswitch)
        session.commit()

        assert result.evaluated == 1
        assert result.approved == 0
        assert result.rejected == 1
        assert result.orders == 0
        decision = session.execute(select(RiskDecisionModel)).scalar_one()
        assert decision.verdict == "rejected"
        assert "CONFIDENCE_BELOW_THRESHOLD" in decision.checks_failed
        assert session.execute(select(func.count()).select_from(OrderModel)).scalar_one() == 0
        session.refresh(proposal)
        assert proposal.status == "rejected"


def test_core_honours_an_active_kill_switch(session_factory, killswitch):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        _seed_bars(session, now, cycles=10)
        session.add(
            KillSwitchEventModel(
                id="kill_active",
                timestamp=now,
                reason="unit-test drawdown breach",
                action_taken="HALT_NEW_ORDERS",
                is_active=True,
            )
        )
        _seed_proposal(session, now + timedelta(seconds=50))
        session.commit()

        result = core_once(session, now=now + timedelta(seconds=50), killswitch=killswitch)
        session.commit()

        assert result.halted is True
        assert result.approved == 0
        decision = session.execute(select(RiskDecisionModel)).scalar_one()
        assert decision.verdict == "halted"
        assert "GLOBAL_KILL_SWITCH" in decision.checks_failed
        assert session.execute(select(func.count()).select_from(OrderModel)).scalar_one() == 0


def test_manage_positions_closes_at_target_and_records_pnl(session_factory):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        _seed_bars(session, now, cycles=10)
        proposal = _seed_proposal(
            session,
            now,
            entry=100.0,
            stop=98.0,
            target=101.0,
        )
        proposal.status = "filled"
        session.add(
            PositionModel(
                id="pos_AAPL",
                section="STOCKS",
                symbol="AAPL",
                side="LONG",
                qty=10.0,
                entry_price=100.0,
                current_price=100.0,
                unrealized_pnl=0.0,
                created_at=now,
            )
        )
        # A fresh bar above the target triggers the exit.
        persist_bar(
            session,
            next_bar(session, "AAPL", now + timedelta(seconds=60), interval_seconds=5, seed=7),
            interval_seconds=5,
        )
        session.commit()

        # Force the mark above the target deterministically.
        bar = session.execute(
            select(OhlcvBar).where(OhlcvBar.symbol == "AAPL").order_by(OhlcvBar.timestamp.desc()).limit(1)
        ).scalar_one()
        bar.close = 105.0
        session.commit()

        closed = manage_positions(session, now=now + timedelta(seconds=120))
        session.commit()

        assert closed == 1
        assert session.execute(select(func.count()).select_from(PositionModel)).scalar_one() == 0
        audit = session.execute(
            select(AuditLogModel).where(AuditLogModel.event_type == "trade.closed")
        ).scalar_one()
        assert audit.payload["symbol"] == "AAPL"
        assert audit.payload["reason"] == "target"
        assert audit.payload["pnl"] > 0


def test_runtime_activity_reaches_the_dashboard_projection(session_factory, killswitch):
    now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session:
        for index in range(12):
            stamp = now + timedelta(seconds=index * 5)
            ingest_once(session, now=stamp, interval_seconds=5, seed=7)
            agent_once(session, now=stamp)
            core_once(session, now=stamp, killswitch=killswitch)
            session.commit()

        snapshot = build_snapshot(session, now=now + timedelta(seconds=60))
        assert snapshot["counters"]["signals"] > 0
        assert snapshot["counters"]["proposals"] > 0
        assert snapshot["counters"]["proposals_evaluated"] > 0
        assert snapshot["equity"]["equity"] != 10_000.0
        assert snapshot["orders"]
        assert snapshot["latest_risk"] is not None
        assert any(line["logger"].startswith("axel.data") for line in snapshot["logs"])
