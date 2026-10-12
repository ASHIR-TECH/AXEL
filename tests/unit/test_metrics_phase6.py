"""Phase 6 operational dashboard field tests (spec §11/§13)."""

from axel.dashboard.metrics import MetricsSnapshot, collect_metrics, format_prometheus
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.approval import ApprovalService
from axel.execution.incidents import IncidentStore
from axel.execution.reason_codes import HaltReason, IncidentCategory
from axel.risk.halt import HaltController
from axel.risk.killswitch import KillSwitch


def _collecting(in_memory_db, tmp_path, *, halt=None, incidents=None, **kwargs) -> MetricsSnapshot:
    return collect_metrics(
        session=in_memory_db,
        broker=AlpacaAdapter(mock_mode=True),
        kill_switch=KillSwitch(state_file_path=tmp_path / "ks.json"),
        approval_service=ApprovalService(),
        halt=halt,
        incident_store=incidents,
        **kwargs,
    )


def test_halt_fields_reflected(in_memory_db, tmp_path):
    halt = HaltController(state_file_path=tmp_path / "halt.json")
    halt.trip(HaltReason.BROKER_OUTAGE, detail="down", source="watchdog")
    snap = _collecting(in_memory_db, tmp_path, halt=halt)
    assert snap.halt_active is True
    assert snap.halt_scope == "new_risk_only"


def test_incident_counts_reflected(in_memory_db, tmp_path):
    incidents = IncidentStore()
    incidents.open_or_update(
        category=IncidentCategory.POSITION_MISMATCH,
        reason_code="RECON_POSITION_MISMATCH",
        summary="drift",
        dedup_key="k1",
    )
    snap = _collecting(in_memory_db, tmp_path, incidents=incidents)
    assert snap.open_incidents_total == 1
    assert snap.critical_incidents_total == 1


def test_operational_kwargs_reflected(in_memory_db, tmp_path):
    snap = _collecting(
        in_memory_db,
        tmp_path,
        live_enabled=True,
        unreconciled_orders=3,
        last_reconciliation_age_seconds=12.5,
        reconciliation_mismatches=2,
    )
    assert snap.live_enabled is True
    assert snap.unreconciled_orders == 3
    assert snap.last_reconciliation_age_seconds == 12.5
    assert snap.reconciliation_mismatches == 2


def test_prometheus_exposes_phase6_fields(in_memory_db, tmp_path):
    halt = HaltController(state_file_path=tmp_path / "halt.json")
    halt.trip(HaltReason.GLOBAL_DRAWDOWN, detail="dd", source="killswitch")
    incidents = IncidentStore()
    incidents.open_or_update(
        category=IncidentCategory.UNKNOWN_BROKER_ORDER,
        reason_code="RECON_BROKER_ORDER_WITHOUT_INTENT",
        summary="ghost",
        dedup_key="k2",
    )
    snap = _collecting(
        in_memory_db,
        tmp_path,
        halt=halt,
        incidents=incidents,
        live_enabled=True,
        unreconciled_orders=1,
        reconciliation_mismatches=1,
    )
    output = format_prometheus(snap)
    assert "axel_live_enabled 1" in output
    assert "axel_halt_active 1" in output
    assert 'axel_halt_scope{scope="global"} 1' in output
    assert "axel_open_incidents_total 1" in output
    assert "axel_critical_incidents_total 1" in output
    assert "axel_unreconciled_orders_total 1" in output
    assert "axel_reconciliation_mismatches_total 1" in output


def test_no_halt_scope_label_when_clear(in_memory_db, tmp_path):
    snap = _collecting(in_memory_db, tmp_path)
    output = format_prometheus(snap)
    assert "axel_halt_active 0" in output
    assert "axel_halt_scope" not in output
