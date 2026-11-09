"""Storage-path tests: batching, idempotency, retention and continuity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import axel.db.models
import axel.portfolio.db_models  # noqa: F401 — register tables
from axel.db.base import Base
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    OhlcvBar,
    OperationalEventModel,
    SignalModel,
)
from axel.db.retention import RetentionPolicy, prune
from axel.runtime.heartbeat import continuity_report, covered_days, record_heartbeat
from axel.runtime.paper_feed import (
    latest_prices,
    next_bar,
    persist_bar,
    persist_bars,
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


NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)


def _bar(symbol: str, stamp: datetime, close: float = 100.0):
    bar = next_bar(None, symbol, stamp, interval_seconds=60, seed=7, previous_price=close)
    return bar


def test_persist_bars_writes_once_and_is_idempotent(session_factory):
    with session_factory() as session:
        bars = [_bar("AAPL", NOW), _bar("MSFT", NOW), _bar("NVDA", NOW)]
        assert persist_bars(session, bars, interval_seconds=60) == 3
        session.commit()

        # Same bucket again: ON CONFLICT DO NOTHING must leave three rows.
        assert persist_bars(session, bars, interval_seconds=60) == 3
        session.commit()

        count = session.execute(select(func.count()).select_from(OhlcvBar)).scalar_one()
        assert count == 3


def test_persist_bar_reports_whether_the_row_was_new(session_factory):
    with session_factory() as session:
        bar = _bar("AAPL", NOW)
        assert persist_bar(session, bar, interval_seconds=60) is True
        session.commit()
        assert persist_bar(session, bar, interval_seconds=60) is False


def test_latest_prices_returns_one_close_per_symbol(session_factory):
    with session_factory() as session:
        persist_bars(
            session,
            [
                _bar("AAPL", NOW - timedelta(minutes=2), close=100.0),
                _bar("AAPL", NOW, close=101.0),
                _bar("MSFT", NOW - timedelta(minutes=5), close=200.0),
            ],
            interval_seconds=60,
        )
        session.commit()

        prices = latest_prices(session, ("AAPL", "MSFT", "MISSING"))
        expected_aapl = session.execute(
            select(OhlcvBar.close)
            .where(OhlcvBar.symbol == "AAPL")
            .order_by(OhlcvBar.timestamp.desc())
            .limit(1)
        ).scalar_one()
        assert prices["AAPL"] == pytest.approx(float(expected_aapl))
        assert prices["MSFT"] == pytest.approx(
            float(
                session.execute(
                    select(OhlcvBar.close).where(OhlcvBar.symbol == "MSFT")
                ).scalar_one()
            )
        )
        assert "MISSING" not in prices


def test_retention_prunes_only_rows_older_than_the_policy(session_factory):
    old = NOW - timedelta(days=400)
    recent = NOW - timedelta(days=1)
    with session_factory() as session:
        session.add_all(
            [
                OhlcvBar(
                    id="bar_old",
                    symbol="AAPL",
                    timestamp=old,
                    ingestion_time=old,
                    timeframe="1m",
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                ),
                OhlcvBar(
                    id="bar_recent",
                    symbol="AAPL",
                    timestamp=recent,
                    ingestion_time=recent,
                    timeframe="1m",
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                ),
                SignalModel(
                    id="sig_old",
                    ts=old,
                    as_of=old,
                    section="STOCKS",
                    symbol="AAPL",
                    analyst="TECHNICAL",
                    direction="LONG",
                    strength=0.8,
                ),
                SignalModel(
                    id="sig_recent",
                    ts=recent,
                    as_of=recent,
                    section="STOCKS",
                    symbol="AAPL",
                    analyst="TECHNICAL",
                    direction="LONG",
                    strength=0.8,
                ),
                AuditLogModel(
                    id="audit_old",
                    timestamp=old,
                    event_type="market.ingest",
                    actor="test",
                    payload={},
                ),
                OperationalEventModel(
                    id="op_old",
                    category="api",
                    component="alpaca",
                    event="failure",
                    outcome="error",
                    severity="warning",
                    occurred_at=old,
                ),
                EquitySnapshotModel(
                    id="eq_old",
                    timestamp=old,
                    section="global",
                    nav=1.0,
                    cash=1.0,
                ),
            ]
        )
        session.commit()

        removed = prune(
            session,
            RetentionPolicy(
                operational_event_days=30,
                audit_log_days=90,
                signal_days=90,
                market_data_days=365,
                equity_snapshot_days=730,
            ),
            now=NOW,
        )

        assert removed == {
            "operational_events": 1,
            "audit_log": 1,
            "signals": 1,
            "ohlcv_bars": 1,
            "equity_snapshots": 0,
        }
        assert session.get(OhlcvBar, "bar_recent") is not None
        assert session.get(SignalModel, "sig_recent") is not None


def test_heartbeat_records_cycles_and_detects_a_gap(session_factory):
    with session_factory() as session:
        record_heartbeat(session, "core-worker", cycles=1, interval_seconds=60, now=NOW)
        record_heartbeat(
            session,
            "core-worker",
            cycles=2,
            interval_seconds=60,
            now=NOW + timedelta(seconds=60),
        )
        session.commit()

        report = continuity_report(session)[0]
        assert report.cycles == 2
        assert report.gap_count == 0
        assert report.downtime_seconds == 0.0

        # Ten minutes of silence: eight minutes beyond one expected cycle.
        record_heartbeat(
            session,
            "core-worker",
            cycles=3,
            interval_seconds=60,
            now=NOW + timedelta(seconds=660),
        )
        session.commit()

        report = continuity_report(session)[0]
        assert report.gap_count == 1
        # The gap is 600s from the last heartbeat; one cycle is credited back
        # because the resumed heartbeat covers the interval before it.
        assert report.downtime_seconds == pytest.approx(540.0)
        assert report.covered_seconds == pytest.approx(120.0)


def test_covered_days_uses_the_least_continuous_worker(session_factory):
    with session_factory() as session:
        record_heartbeat(session, "ingest-worker", cycles=1, interval_seconds=60, now=NOW)
        record_heartbeat(session, "agent-worker", cycles=1, interval_seconds=60, now=NOW)
        record_heartbeat(session, "core-worker", cycles=1, interval_seconds=60, now=NOW)
        record_heartbeat(
            session,
            "ingest-worker",
            cycles=2,
            interval_seconds=60,
            now=NOW + timedelta(days=2),
        )
        record_heartbeat(
            session,
            "agent-worker",
            cycles=2,
            interval_seconds=60,
            now=NOW + timedelta(days=2),
        )
        session.commit()

        # core-worker never advanced, so the honest coverage is zero.
        assert covered_days(session) == pytest.approx(0.0)
