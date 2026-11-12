"""Tests for the public paper-soak snapshot exporter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import axel.db.models  # noqa: F401 — register tables
from axel.db.base import Base
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    PositionModel,
    WorkerHeartbeatModel,
)
from scripts.export_soak_snapshot import build_snapshot

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
STARTED = NOW - timedelta(days=2)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(session) -> None:
    session.add(
        AuditLogModel(
            id="soak",
            timestamp=STARTED,
            event_type="paper_soak.started",
            actor="test",
            payload={
                "message": "started",
                "window_end": (STARTED + timedelta(days=56)).isoformat(),
            },
        )
    )
    session.add(
        EquitySnapshotModel(
            id="eq_1",
            timestamp=NOW,
            section="global",
            nav=101_250.0,
            cash=80_000.0,
            gross_exposure=21_250.0,
            realized_pnl=250.0,
            unrealized_pnl=1_000.0,
            drawdown_pct=0.01,
            high_water_mark=101_250.0,
        )
    )
    session.add(
        PositionModel(
            id="pos_1",
            section="STOCKS",
            symbol="AAPL",
            side="LONG",
            qty=10.0,
            entry_price=100.0,
            current_price=110.0,
            unrealized_pnl=100.0,
            created_at=NOW,
        )
    )
    session.add(
        FillModel(
            id="fill_1",
            client_order_id="ord_1",
            symbol="AAPL",
            side="buy",
            qty=10.0,
            price=100.0,
            fee=0.0,
            slippage=0.05,
            timestamp=NOW,
        )
    )
    session.add(
        AuditLogModel(
            id="closed_1",
            timestamp=NOW,
            event_type="trade.closed",
            actor="test",
            payload={
                "message": "closed",
                "symbol": "AAPL",
                "section": "STOCKS",
                "qty": 10.0,
                "entry": 100.0,
                "exit": 110.0,
                "pnl": 100.0,
                "win": True,
                "reason": "take_profit",
            },
        )
    )
    for name in ("ingest-worker", "agent-worker", "core-worker"):
        session.add(
            WorkerHeartbeatModel(
                worker=name,
                cycles=100,
                interval_seconds=60.0,
                first_seen=STARTED,
                last_seen=NOW,
                downtime_seconds=0.0,
                gap_count=0,
            )
        )


def test_snapshot_carries_the_paper_evidence(monkeypatch) -> None:
    monkeypatch.setattr("axel.core.config.settings.paper_data_source", "alpaca")
    with _session() as session:
        _seed(session)
        session.commit()
        data = build_snapshot(session, now=NOW)

    assert data["meta"]["feed"] == "alpaca-iex"
    assert data["meta"]["days_elapsed"] == 2.0
    assert data["meta"]["days_remaining"] == 54.0
    assert data["account"]["nav"] == 101_250.0
    assert data["account"]["cash"] == 80_000.0
    assert data["account"]["drawdown_pct"] == 1.0
    assert data["positions"][0]["symbol"] == "AAPL"
    assert data["positions"][0]["unrealized"] == 100.0
    assert data["trades"][0]["pnl"] == 100.0
    assert data["stats"]["closed_trades"] == 1
    assert data["stats"]["win_rate"] == 1.0
    assert data["stats"]["total_realized_pnl"] == 100.0
    assert data["coverage_days"] == 2.0
    assert data["workers"]["core-worker"]["ratio"] == 1.0


def test_snapshot_never_leaks_secret_material() -> None:
    with _session() as session:
        _seed(session)
        session.commit()
        data = build_snapshot(session, now=NOW)

    blob = repr(data).upper()
    for forbidden in ("SECRET", "API_KEY", "APCA", "PRIVATE"):
        assert forbidden not in blob


def test_snapshot_reads_legacy_marker_without_window_end() -> None:
    with _session() as session:
        session.add(
            AuditLogModel(
                id="soak",
                timestamp=STARTED,
                event_type="paper_soak.started",
                actor="test",
                payload={"message": "started"},
            )
        )
        session.commit()
        data = build_snapshot(session, now=NOW)

    assert data["meta"]["window_end"] == (STARTED + timedelta(days=56)).isoformat()
