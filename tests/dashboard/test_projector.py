"""Unit tests for the read-only live-dashboard projection layer."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import axel.db.models
import axel.portfolio.db_models  # noqa: F401 — register tables
from axel.dashboard.projector import (
    INITIAL_EQUITY,
    DashboardStreamState,
    build_snapshot,
)
from axel.db.base import Base
from axel.db.models import (
    AgentRunModel,
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    IncidentModel,
    KillSwitchEventModel,
    OhlcvBar,
    OrderModel,
    PositionModel,
    RiskDecisionModel,
    SignalModel,
    TradeProposalModel,
)


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed(session: Session, now: datetime) -> None:
    # Price history: a clean upward trend for AAPL.
    for index in range(30):
        session.add(
            OhlcvBar(
                id=f"bar_aapl_{index}",
                symbol="AAPL",
                timestamp=now - timedelta(minutes=30 - index),
                ingestion_time=now - timedelta(seconds=5),
                timeframe="tick",
                open=100.0 + index,
                high=101.0 + index,
                low=99.0 + index,
                close=100.0 + index,
                volume=10_000.0,
            )
        )

    # One analyst signal per section/analyst is enough to prove the mapping.
    session.add(
        SignalModel(
            id="sig_technical_stocks",
            ts=now,
            as_of=now,
            section="STOCKS",
            symbol="AAPL",
            analyst="TECHNICAL",
            direction="LONG",
            strength=0.82,
            horizon="1d",
        )
    )
    session.add(
        SignalModel(
            id="sig_macro_forex",
            ts=now,
            as_of=now,
            section="FOREX",
            symbol="EURUSD",
            analyst="MACRO_CARRY",
            direction="SHORT",
            strength=0.55,
            horizon="1d",
        )
    )

    # Proposal + deterministic decision.
    session.add(
        TradeProposalModel(
            id="prop_1",
            section="STOCKS",
            symbol="AAPL",
            side="buy",
            entry_price=129.0,
            stop_loss=125.0,
            take_profit=141.0,
            r_multiple_target=3.0,
            position_size_usd=1_000.0,
            confidence_score=0.82,
            strategy_id="MOM_12-1",
            mode="paper",
            status="approved",
            ttl_expires_at=now + timedelta(minutes=5),
            created_at=now - timedelta(seconds=10),
            agent_reasoning="momentum",
        )
    )
    session.add(
        RiskDecisionModel(
            id="risk_1",
            proposal_id="prop_1",
            approved=True,
            verdict="approved",
            approved_qty=7.0,
            approved_notional_usd=903.0,
            binding_limit="NOTIONAL_CAP_5PCT",
            reasons=["All deterministic risk and sizing checks passed"],
            checks_passed=[
                "GLOBAL_KILL_SWITCH_CLEAR",
                "SECTION_DAILY_STOP_CLEAR",
                "PANEL_CONFIDENCE_SUFFICIENT",
                "CONCURRENT_POSITIONS_CAP_CLEAR",
                "RR_RATIO_VALID",
                "R_TARGET_SANITY_CLEAR",
                "CORRELATION_CHECK_CLEAR",
                "POSITION_SIZING_APPROVED",
            ],
            checks_failed=[],
            created_at=now - timedelta(seconds=9),
        )
    )

    # Order and fill.
    session.add(
        OrderModel(
            client_order_id="ord_1",
            proposal_id="prop_1",
            symbol="AAPL",
            side="buy",
            qty=7.0,
            type="limit",
            limit_price=129.0,
            state="filled",
            filled_qty=7.0,
            filled_avg_price=129.05,
            created_at=now - timedelta(seconds=8),
        )
    )
    session.add(
        FillModel(
            id="fill_1",
            client_order_id="ord_1",
            symbol="AAPL",
            side="buy",
            qty=7.0,
            price=129.05,
            fee=0.0,
            slippage=0.05,
            timestamp=now - timedelta(seconds=7),
        )
    )

    # Open position marked to the latest close.
    session.add(
        PositionModel(
            id="pos_1",
            section="STOCKS",
            symbol="AAPL",
            side="LONG",
            qty=7.0,
            entry_price=129.05,
            current_price=129.0,
            unrealized_pnl=(129.0 - 129.05) * 7.0,
            created_at=now - timedelta(seconds=7),
        )
    )

    # Equity history: flat then a small gain.
    for index, nav in enumerate((10_000.0, 10_010.0, 10_020.0)):
        stamp = now - timedelta(minutes=3 - index)
        session.add(
            EquitySnapshotModel(
                id=f"eq_global_{index}",
                timestamp=stamp,
                section="global",
                nav=nav,
                cash=10_000.0,
                gross_exposure=0.0,
                net_exposure=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                drawdown_pct=0.0,
                high_water_mark=nav,
            )
        )
        session.add(
            EquitySnapshotModel(
                id=f"eq_stocks_{index}",
                timestamp=stamp,
                section="STOCKS",
                nav=4_500.0 + (nav - 10_000.0),
                cash=4_500.0,
                gross_exposure=0.0,
                net_exposure=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                drawdown_pct=0.0,
                high_water_mark=4_500.0,
            )
        )

    # Closed trade for realized P&L, trade count and win rate.
    session.add(
        AuditLogModel(
            id="audit_closed",
            timestamp=now - timedelta(seconds=20),
            event_type="trade.closed",
            actor="axel.core",
            payload={"section": "STOCKS", "symbol": "MSFT", "pnl": 42.5, "win": True},
        )
    )
    session.add(
        AuditLogModel(
            id="audit_started",
            timestamp=now - timedelta(hours=2),
            event_type="runtime.started",
            actor="axel.core",
            payload={"message": "runtime started", "logger": "axel.core", "channel": "SYS"},
        )
    )
    session.add(
        AuditLogModel(
            id="audit_alert",
            timestamp=now - timedelta(seconds=5),
            event_type="alert",
            actor="axel.alerts",
            payload={"level": "WARN", "message": "feed lag high"},
        )
    )

    session.add(
        AgentRunModel(
            id="run_1",
            timestamp=now,
            agent_name="panel",
            model="deterministic-paper",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.12,
            latency_ms=4,
        )
    )
    session.add(
        IncidentModel(
            id="inc_1",
            category="data",
            severity="warning",
            reason_code="FEED_LAG",
            summary="FRED feed lag 120s",
            dedup_key="feed-lag",
            status="open",
            occurrence_count=1,
            first_seen_at=now - timedelta(minutes=2),
            last_seen_at=now,
        )
    )
    session.add(
        KillSwitchEventModel(
            id="kill_1",
            timestamp=now,
            reason="peak-to-trough drawdown breach",
            action_taken="HALT_NEW_ORDERS",
            is_active=True,
        )
    )
    session.commit()


def test_empty_database_projects_a_flat_honest_baseline():
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = build_snapshot(session)

    assert snapshot["equity"]["equity"] == INITIAL_EQUITY
    assert snapshot["equity"]["hwm"] == INITIAL_EQUITY
    assert snapshot["equity"]["drawdown_pct"] == 0.0
    assert snapshot["equity"]["series"] == [INITIAL_EQUITY, INITIAL_EQUITY]
    assert snapshot["sections"]["STOCKS"]["nav"] == 4_500.0
    assert snapshot["portfolio"]["holdings"] == []
    assert snapshot["orders"] == []
    assert snapshot["latest_risk"] is None
    assert snapshot["halted"] is False


def test_snapshot_maps_durable_rows_to_terminal_payloads():
    engine = _engine()
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        _seed(session, now)
        snapshot = build_snapshot(session, now=now)

    assert snapshot["equity"]["equity"] == 10_020.0
    assert snapshot["equity"]["hwm"] == 10_020.0
    assert snapshot["equity"]["realized_pnl"] == 42.5
    assert snapshot["sections"]["STOCKS"]["nav"] == 4_520.0
    assert snapshot["sections"]["STOCKS"]["positions"] == 1
    assert snapshot["portfolio"]["trades"] == 1
    assert snapshot["portfolio"]["wins"] == 1
    assert snapshot["portfolio"]["holdings"][0]["sym"] == "AAPL"
    assert snapshot["portfolio"]["holdings"][0]["side"] == "L"
    assert snapshot["orders"][0]["state"] == "FILLED"
    assert snapshot["orders"][0]["side"] == "BUY"
    assert snapshot["signals"]["TECHNICAL|STOCKS"]["d"] == "L"
    assert snapshot["signals"]["TECHNICAL|STOCKS"]["conf"] == 0.82
    assert snapshot["signals"]["MACRO/CARRY|FOREX"]["d"] == "S"
    assert snapshot["latest_risk"]["verdict"] == "APPROVED"
    assert all(check["pass"] for check in snapshot["latest_risk"]["checks"])
    assert snapshot["halted"] is True
    assert snapshot["kill_events"][0]["reason"] == "peak-to-trough drawdown breach"
    assert snapshot["ops"]["llmSpend"] == 0.12
    assert snapshot["quotes"]["AAPL"]["price"] == 129.0
    assert snapshot["counters"]["proposals_evaluated"] == 1
    assert snapshot["counters"]["fills"] == 1
    assert snapshot["started_at"] is not None
    assert any(line["id"] == "audit_started" for line in snapshot["logs"])


def test_risk_checks_map_deterministic_failures_to_ui_keys():
    engine = _engine()
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        _seed(session, now)
        decision = session.get(RiskDecisionModel, "risk_1")
        decision.approved = False
        decision.verdict = "rejected"
        decision.checks_failed = ["RR_RATIO_TOO_LOW", "CORRELATION_LIMIT_BREACH"]
        decision.reasons = [
            "Risk/Reward ratio 1.20 < minimum required 1.50",
            "Correlation with active position 'MSFT' is 0.91 >= threshold 0.85",
        ]
        session.commit()
        snapshot = build_snapshot(session, now=now)

    checks = {check["k"]: check for check in snapshot["latest_risk"]["checks"]}
    assert checks["rr"]["pass"] is False
    assert "1.20" in checks["rr"]["why"]
    assert checks["corr"]["pass"] is False
    assert "0.91" in checks["corr"]["why"]
    assert checks["kill"]["pass"] is True


def test_stream_state_emits_only_new_logs_and_changed_domains():
    engine = _engine()
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        _seed(session, now)
        first = build_snapshot(session, now=now)

    state = DashboardStreamState(first)

    with Session(engine) as session:
        session.add(
            AuditLogModel(
                id="audit_new",
                timestamp=now + timedelta(seconds=1),
                event_type="order.filled",
                actor="axel.execution",
                payload={"message": "fill ord_2 AAPL BUY 3@130.00", "logger": "axel.execution"},
            )
        )
        session.add(
            EquitySnapshotModel(
                id="eq_global_new",
                timestamp=now + timedelta(seconds=1),
                section="global",
                nav=10_030.0,
                cash=10_000.0,
                gross_exposure=0.0,
                net_exposure=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                drawdown_pct=0.0,
                high_water_mark=10_030.0,
            )
        )
        session.commit()
        second = build_snapshot(session, now=now + timedelta(seconds=1))

    events = state.poll(second)
    names = [name for name, _payload in events]
    assert "log" in names
    assert "equity" in names
    log_events = [payload for name, payload in events if name == "log"]
    assert log_events[-1]["id"] == "audit_new"
    equity_event = next(payload for name, payload in events if name == "equity")
    assert equity_event["equity"] == 10_030.0
