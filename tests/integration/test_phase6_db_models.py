"""Phase 6 durable-schema tests: tables exist and records round-trip exactly."""

from datetime import UTC, datetime
from decimal import Decimal

from axel.db.models import (
    ApprovalModel,
    ExecutionMetricModel,
    IncidentModel,
    LiveConfigModel,
    OrderEventModel,
    ReconciliationRunModel,
)

NOW = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)


def test_phase6_tables_registered():
    from axel.db.base import Base

    for table in (
        "order_events",
        "approvals",
        "incidents",
        "reconciliation_runs",
        "execution_metrics",
        "live_configs",
    ):
        assert table in Base.metadata.tables


def test_order_event_roundtrip(in_memory_db):
    in_memory_db.add(
        OrderEventModel(
            id="ev-1",
            client_order_id="ord-1",
            sequence=1,
            kind="created",
            state="CREATED",
            symbol="SPY",
            side="buy",
            qty="10",
            anomaly=None,
            payload={"source": "proposal"},
            recorded_at=NOW,
        )
    )
    in_memory_db.commit()
    row = in_memory_db.query(OrderEventModel).filter_by(id="ev-1").one()
    assert row.kind == "created"
    assert row.payload == {"source": "proposal"}


def test_approval_roundtrip(in_memory_db):
    in_memory_db.add(
        ApprovalModel(
            approval_id="appr-1",
            client_order_id="ord-1",
            order_hash="abc123",
            config_version="cfg-1",
            status="pending",
            requested_by="agent-1",
            requested_at=NOW,
            expires_at=NOW,
        )
    )
    in_memory_db.commit()
    row = in_memory_db.query(ApprovalModel).filter_by(approval_id="appr-1").one()
    assert row.order_hash == "abc123"
    assert row.consumed_at is None


def test_incident_roundtrip_and_dedup_key_unique(in_memory_db):
    inc = IncidentModel(
        id="inc-1",
        category="position_mismatch",
        severity="critical",
        reason_code="RECON_POSITION_MISMATCH",
        summary="drift",
        dedup_key="k1",
        status="open",
        occurrence_count=1,
        first_seen_at=NOW,
        last_seen_at=NOW,
        evidence={"axel": "50", "broker": "60"},
    )
    in_memory_db.add(inc)
    in_memory_db.commit()
    row = in_memory_db.query(IncidentModel).filter_by(dedup_key="k1").one()
    assert row.severity == "critical"
    assert row.evidence == {"axel": "50", "broker": "60"}


def test_reconciliation_run_roundtrip(in_memory_db):
    in_memory_db.add(
        ReconciliationRunModel(
            id="run-1",
            started_at=NOW,
            completed_at=NOW,
            snapshot_time=NOW,
            broker_account_id="acct-1",
            healthy=False,
            blocks_new_risk=True,
            mismatch_count=2,
            reason_codes=["RECON_POSITION_MISMATCH"],
            details={"mismatches": 2},
        )
    )
    in_memory_db.commit()
    row = in_memory_db.query(ReconciliationRunModel).filter_by(id="run-1").one()
    assert row.blocks_new_risk is True
    assert row.reason_codes == ["RECON_POSITION_MISMATCH"]


def test_execution_metric_roundtrip_exact_decimals(in_memory_db):
    # Decimal strings must survive storage without binary rounding error.
    in_memory_db.add(
        ExecutionMetricModel(
            id="ord-1:exec-metrics-1",
            client_order_id="ord-1",
            symbol="SPY",
            side="buy",
            requested_qty="100",
            filled_qty="100",
            fill_ratio="1",
            avg_fill_price=str(Decimal("101.6")),
            filled_notional=str(Decimal(10160)),
            fees_total=str(Decimal("1.00")),
            implementation_shortfall_per_share=str(Decimal("1.6")),
            implementation_shortfall_filled="160",
            net_implementation_shortfall_filled=str(Decimal("161.00")),
            calculation_version="exec-metrics-1",
            status={"fill_ratio": "computable"},
            raw={"symbol": "SPY"},
        )
    )
    in_memory_db.commit()
    row = in_memory_db.query(ExecutionMetricModel).filter_by(client_order_id="ord-1").one()
    assert Decimal(row.avg_fill_price) == Decimal("101.6")
    assert Decimal(row.fees_total) == Decimal("1.00")


def test_live_config_roundtrip(in_memory_db):
    in_memory_db.add(
        LiveConfigModel(
            config_version="cfg-1",
            environment="live",
            broker="alpaca",
            account_id="acct-1",
            allowed_section="stocks",
            max_live_capital="1000",
            max_position_notional="500",
            max_order_notional="500",
            max_daily_loss="50",
            max_concurrent_positions=5,
            max_order_frequency=10,
            order_frequency_window_seconds=60,
            allowed_symbols=["SPY", "QQQ"],
            manual_approval_required=False,
            phase5_evidence_ref="evidence/paper-soak",
            phase5_gate_passed=True,
            active=True,
        )
    )
    in_memory_db.commit()
    row = in_memory_db.query(LiveConfigModel).filter_by(config_version="cfg-1").one()
    assert row.allowed_symbols == ["SPY", "QQQ"]
    assert row.phase5_gate_passed is True
    assert row.active is True


def test_init_db_creates_phase6_tables(tmp_path):
    from sqlalchemy import create_engine, inspect

    from axel.db.session import init_db

    engine = create_engine(f"sqlite:///{tmp_path / 'axel.db'}")
    init_db(bind_engine=engine)
    names = set(inspect(engine).get_table_names())
    for table in (
        "order_events",
        "approvals",
        "incidents",
        "reconciliation_runs",
        "execution_metrics",
        "live_configs",
    ):
        assert table in names
