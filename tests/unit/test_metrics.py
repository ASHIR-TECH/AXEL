"""
Tests for axel/dashboard/metrics.py — MetricsSnapshot collection and Prometheus formatting.
"""

from datetime import UTC, datetime

from axel.dashboard.metrics import MetricsSnapshot, collect_metrics, format_prometheus
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.approval import ApprovalService
from axel.risk.killswitch import KillSwitch

# ── format_prometheus ─────────────────────────────────────────────────────────

class TestFormatPrometheus:
    def _snapshot(self, **kwargs) -> MetricsSnapshot:
        defaults = {
            "total_equity_usd": 100_000.0,
            "cash_usd": 50_000.0,
            "drawdown_pct": 0.05,
            "high_water_mark_usd": 105_263.0,
            "gross_exposure_usd": 50_000.0,
            "net_exposure_usd": 30_000.0,
            "open_positions_count": 3,
            "kill_switch_active": False,
            "pending_hitl_tickets": 2,
            "proposals_last_1h": 5,
            "proposals_last_24h": 20,
            "approved_proposals_last_24h": 15,
            "rejected_proposals_last_24h": 5,
        }
        defaults.update(kwargs)
        return MetricsSnapshot(**defaults)

    def test_output_is_string(self):
        output = format_prometheus(self._snapshot())
        assert isinstance(output, str)

    def test_contains_all_expected_metric_names(self):
        output = format_prometheus(self._snapshot())
        expected_names = [
            "axel_equity_usd",
            "axel_cash_usd",
            "axel_drawdown_pct",
            "axel_high_water_mark_usd",
            "axel_gross_exposure_usd",
            "axel_net_exposure_usd",
            "axel_open_positions_total",
            "axel_kill_switch_active",
            "axel_pending_hitl_tickets",
            "axel_proposals_total",
            "axel_proposals_approved_total",
            "axel_proposals_rejected_total",
        ]
        for name in expected_names:
            assert name in output, f"Expected metric '{name}' not found in output"

    def test_each_metric_has_help_and_type_lines(self):
        output = format_prometheus(self._snapshot())
        assert "# HELP axel_equity_usd" in output
        assert "# TYPE axel_equity_usd gauge" in output

    def test_kill_switch_active_is_1_when_true(self):
        output = format_prometheus(self._snapshot(kill_switch_active=True))
        lines = [ln for ln in output.splitlines() if ln.startswith("axel_kill_switch_active ")]
        assert any(ln.split()[1] == "1" for ln in lines)

    def test_kill_switch_active_is_0_when_false(self):
        output = format_prometheus(self._snapshot(kill_switch_active=False))
        lines = [ln for ln in output.splitlines() if ln.startswith("axel_kill_switch_active ")]
        assert any(ln.split()[1] == "0" for ln in lines)

    def test_ends_with_newline(self):
        output = format_prometheus(self._snapshot())
        assert output.endswith("\n")

    def test_labels_present_for_windowed_counters(self):
        output = format_prometheus(self._snapshot())
        assert 'window="24h"' in output
        assert 'window="1h"' in output

    def test_timestamp_is_integer_ms(self):
        snap = self._snapshot()
        output = format_prometheus(snap)
        # Every data line ends with the timestamp (integer ms)
        data_lines = [ln for ln in output.splitlines() if ln and not ln.startswith("#")]
        for line in data_lines:
            parts = line.rsplit(" ", 1)
            assert parts[-1].isdigit(), f"Expected integer ms timestamp in: {line}"

    def test_zero_equity_renders_correctly(self):
        output = format_prometheus(self._snapshot(total_equity_usd=0.0))
        lines = [l for l in output.splitlines() if l.startswith("axel_equity_usd ")]
        assert any("0.0" in l or l.split()[1] == "0" for l in lines)


# ── collect_metrics ───────────────────────────────────────────────────────────

class TestCollectMetrics:
    """Integration-style tests using mock broker, real kill switch (tmp file), and in-memory DB."""

    def test_returns_metrics_snapshot(self, in_memory_db, tmp_path):
        broker = AlpacaAdapter(mock_mode=True)
        ks = KillSwitch(state_file_path=tmp_path / "ks.json")
        approval = ApprovalService()
        snap = collect_metrics(in_memory_db, broker, ks, approval)
        assert isinstance(snap, MetricsSnapshot)

    def test_equity_from_mock_broker(self, in_memory_db, tmp_path):
        broker = AlpacaAdapter(mock_mode=True)
        broker._mock_balance["total_equity"] = 123_456.0
        ks = KillSwitch(state_file_path=tmp_path / "ks.json")
        approval = ApprovalService()
        snap = collect_metrics(in_memory_db, broker, ks, approval)
        assert snap.total_equity_usd == 123_456.0

    def test_kill_switch_reflected(self, in_memory_db, tmp_path):
        broker = AlpacaAdapter(mock_mode=True)
        ks = KillSwitch(state_file_path=tmp_path / "ks.json")
        ks.trip(reason="test halt")
        approval = ApprovalService()
        snap = collect_metrics(in_memory_db, broker, ks, approval)
        assert snap.kill_switch_active is True

    def test_pending_hitl_tickets(self, in_memory_db, tmp_path):
        broker = AlpacaAdapter(mock_mode=True)
        ks = KillSwitch(state_file_path=tmp_path / "ks.json")
        approval = ApprovalService()
        # No tickets — should be 0
        snap = collect_metrics(in_memory_db, broker, ks, approval)
        assert snap.pending_hitl_tickets == 0

    def test_drawdown_calculated_from_hwm(self, in_memory_db, tmp_path):
        broker = AlpacaAdapter(mock_mode=True)
        broker._mock_balance["total_equity"] = 90_000.0
        ks = KillSwitch(state_file_path=tmp_path / "ks.json")
        ks.set_high_water_mark(100_000.0)
        approval = ApprovalService()
        snap = collect_metrics(in_memory_db, broker, ks, approval)
        assert abs(snap.drawdown_pct - 0.10) < 1e-4

    def test_collected_at_is_recent(self, in_memory_db, tmp_path):
        broker = AlpacaAdapter(mock_mode=True)
        ks = KillSwitch(state_file_path=tmp_path / "ks.json")
        approval = ApprovalService()
        before = datetime.now(UTC)
        snap = collect_metrics(in_memory_db, broker, ks, approval)
        after = datetime.now(UTC)
        assert before <= snap.collected_at <= after
