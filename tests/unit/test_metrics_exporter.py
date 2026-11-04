"""Tests for the read-only Prometheus exporter and DB-backed metric fallbacks."""

from __future__ import annotations

import threading
import urllib.request
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from axel.dashboard.metrics import collect_metrics
from axel.db.models import (
    ApprovalModel,
    IncidentModel,
    OrderModel,
    ReconciliationRunModel,
)
from axel.execution.alpaca import AlpacaAdapter
from axel.observability.exporter import MetricsExporter, make_handler
from axel.risk.halt import HaltController
from axel.risk.killswitch import KillSwitch


def _exporter(in_memory_db, tmp_path) -> MetricsExporter:
    factory = sessionmaker(bind=in_memory_db.get_bind())
    return MetricsExporter(
        session_factory=factory,
        broker=AlpacaAdapter(mock_mode=True),
        kill_switch=KillSwitch(state_file_path=tmp_path / "ks.json"),
        halt=HaltController(state_file_path=tmp_path / "halt.json"),
        environment="paper",
    )


def test_help_and_type_are_emitted_once_per_family(in_memory_db, tmp_path):
    exporter = _exporter(in_memory_db, tmp_path)
    text = exporter.render()
    assert text.count("# HELP axel_proposals_total ") == 1
    assert text.count("# TYPE axel_proposals_total ") == 1


def test_render_includes_exporter_self_metrics(in_memory_db, tmp_path):
    text = _exporter(in_memory_db, tmp_path).render()
    assert "axel_exporter_up 1" in text
    assert "axel_exporter_last_scrape_success 1" in text
    assert 'axel_environment_info{environment="paper"} 1' in text


def test_render_failure_is_fail_visible():
    def broken_factory():
        raise RuntimeError("database down")

    exporter = MetricsExporter(
        session_factory=broken_factory,
        broker=AlpacaAdapter(mock_mode=True),
        kill_switch=KillSwitch(state_file_path=Path("/tmp/axel-nonexistent-ks.json")),
        halt=HaltController(state_file_path=Path("/tmp/axel-nonexistent-halt.json")),
    )
    text = exporter.render()
    assert "axel_exporter_up 1" in text
    assert "axel_exporter_last_scrape_success 0" in text
    assert "axel_exporter_error 1" in text


def test_ready_reports_database_failure():
    def broken_factory():
        raise RuntimeError("database down")

    exporter = MetricsExporter(
        session_factory=broken_factory,
        broker=AlpacaAdapter(mock_mode=True),
        kill_switch=KillSwitch(state_file_path=Path("/tmp/axel-nonexistent-ks.json")),
        halt=HaltController(state_file_path=Path("/tmp/axel-nonexistent-halt.json")),
    )
    ready, detail = exporter.ready()
    assert ready is False
    assert "database down" in detail


def test_db_incident_fallback(in_memory_db, tmp_path):
    now = datetime.now(UTC)
    in_memory_db.add_all(
        [
            IncidentModel(
                id="inc-1",
                category="position_mismatch",
                severity="critical",
                reason_code="RECON_POSITION_MISMATCH",
                summary="drift",
                dedup_key="k1",
                status="open",
                occurrence_count=1,
                first_seen_at=now,
                last_seen_at=now,
            ),
            IncidentModel(
                id="inc-2",
                category="unknown",
                severity="warning",
                reason_code="UNKNOWN",
                summary="minor",
                dedup_key="k2",
                status="open",
                occurrence_count=1,
                first_seen_at=now,
                last_seen_at=now,
            ),
            IncidentModel(
                id="inc-3",
                category="unknown",
                severity="critical",
                reason_code="UNKNOWN",
                summary="resolved",
                dedup_key="k3",
                status="resolved",
                occurrence_count=1,
                first_seen_at=now,
                last_seen_at=now,
            ),
        ]
    )
    in_memory_db.commit()

    snap = collect_metrics(
        in_memory_db,
        AlpacaAdapter(mock_mode=True),
        KillSwitch(state_file_path=tmp_path / "ks.json"),
        None,
        halt=HaltController(state_file_path=tmp_path / "halt.json"),
    )
    assert snap.open_incidents_total == 2
    assert snap.critical_incidents_total == 1


def test_db_pending_approval_and_reconciliation_fallback(in_memory_db, tmp_path):
    now = datetime.now(UTC)
    in_memory_db.add(
        ApprovalModel(
            approval_id="appr-1",
            client_order_id="coid-1",
            order_hash="hash",
            config_version="v1",
            status="pending",
            requested_at=now,
        )
    )
    in_memory_db.add(
        ReconciliationRunModel(
            id="recon-1",
            started_at=now,
            completed_at=now,
            healthy=False,
            blocks_new_risk=True,
            mismatch_count=3,
            reason_codes=[],
        )
    )
    for index, state in enumerate(("submitted", "partially_filled", "filled")):
        in_memory_db.add(
            OrderModel(
                client_order_id=f"coid-{index}",
                proposal_id="prop-1",
                symbol="AAPL",
                side="buy",
                qty=1.0,
                state=state,
            )
        )
    in_memory_db.commit()

    snap = collect_metrics(
        in_memory_db,
        AlpacaAdapter(mock_mode=True),
        KillSwitch(state_file_path=tmp_path / "ks.json"),
        None,
        halt=HaltController(state_file_path=tmp_path / "halt.json"),
    )
    assert snap.pending_hitl_tickets == 1
    assert snap.unreconciled_orders == 2
    assert snap.reconciliation_mismatches == 3
    assert snap.last_reconciliation_age_seconds is not None


def test_http_handler_serves_metrics_and_health(in_memory_db, tmp_path):
    exporter = _exporter(in_memory_db, tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(exporter))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/metrics", timeout=5) as response:
            assert response.status == 200
            body = response.read().decode()
        assert "axel_exporter_up 1" in body
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=5) as response:
            assert response.read().decode().strip() == "ok"
        with urllib.request.urlopen(f"http://{host}:{port}/readyz", timeout=5) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_format_prometheus_keeps_samples_for_both_windows(in_memory_db, tmp_path):
    exporter = _exporter(in_memory_db, tmp_path)
    text = exporter.render()
    assert 'axel_proposals_total{window="1h"}' in text
    assert 'axel_proposals_total{window="24h"}' in text
