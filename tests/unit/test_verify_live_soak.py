"""Tests for the read-only live-soak verification snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import axel.db.models  # noqa: F401 — register tables
from axel.db.base import Base
from axel.db.models import EquitySnapshotModel, FillModel, OhlcvBar, OrderModel, TradeProposalModel
from scripts.verify_live_soak import snapshot

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _proposal(session, entry: float = 100.0) -> TradeProposalModel:
    row = TradeProposalModel(
        id="prop_1",
        section="STOCKS",
        symbol="AAPL",
        side="buy",
        entry_price=entry,
        stop_loss=98.0,
        take_profit=104.0,
        r_multiple_target=2.0,
        position_size_usd=0.0,
        confidence_score=0.8,
        strategy_id="TEST",
        mode="paper",
        status="filled",
        ttl_expires_at=NOW,
        agent_reasoning="test",
        created_at=NOW,
    )
    session.add(row)
    return row


def test_empty_snapshot_is_safe() -> None:
    with _session() as session:
        data = snapshot(session, now=NOW)

    assert data["fills_30m"] == 0
    assert data["latest_bar"] is None
    assert data["bar_age_seconds"] is None
    assert data["nav"] is None
    assert data["mean_realized_slippage_bps"] is None
    assert data["market_open"] is True


def test_snapshot_reports_bars_fills_and_slippage() -> None:
    with _session() as session:
        session.add(
            OhlcvBar(
                id="bar_1",
                symbol="AAPL",
                timestamp=NOW - timedelta(minutes=1),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000.0,
            )
        )
        _proposal(session, entry=100.0)
        session.add(
            OrderModel(
                client_order_id="ord_1",
                proposal_id="prop_1",
                broker_order_id="broker-1",
                symbol="AAPL",
                side="buy",
                qty=1.0,
                state="filled",
                filled_qty=1.0,
                filled_avg_price=100.5,
                created_at=NOW,
            )
        )
        session.add(
            FillModel(
                id="fill_1",
                client_order_id="ord_1",
                symbol="AAPL",
                side="buy",
                qty=1.0,
                price=100.5,
                fee=0.0,
                slippage=0.5,
                timestamp=NOW,
            )
        )
        session.add(
            EquitySnapshotModel(
                id="eq_1",
                timestamp=NOW,
                section="global",
                nav=10_000.0,
                cash=9_000.0,
            )
        )
        session.commit()

        data = snapshot(session, now=NOW)

    assert data["fills_30m"] == 1
    assert data["orders_with_broker_id"] == 1
    assert data["latest_bar_symbol"] == "AAPL"
    assert data["bar_age_seconds"] is not None
    assert data["nav"] == 10_000.0
    # Bought 0.5% above the reference entry.
    assert data["mean_realized_slippage_bps"] == 50.0
    assert data["last_fill"]["price"] == 100.5
