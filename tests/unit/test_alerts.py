"""
Tests for axel/comms/alerts.py — alert taxonomy and factory functions.
"""

from datetime import UTC, datetime

import pytest

from axel.comms.alerts import (
    KIND_HALT,
    KIND_HEARTBEAT_SILENCE,
    KIND_LLM_BUDGET_BREACH,
    KIND_RECONCILIATION_DRIFT,
    KIND_STALE_DATA,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    Alert,
    budget_alert,
    halt_alert,
    heartbeat_alert,
    reconciliation_alert,
    stale_data_alert,
)

# ── Alert dataclass ─────────────────────────────────────────────────────────

class TestAlertDataclass:
    def test_is_frozen(self):
        alert = Alert(kind=KIND_HALT, message="test", created_at=datetime.now(UTC))
        with pytest.raises((AttributeError, TypeError)):
            alert.kind = "other"  # type: ignore[misc]

    def test_default_severity_is_warning(self):
        alert = Alert(kind=KIND_STALE_DATA, message="x", created_at=datetime.now(UTC))
        assert alert.severity == SEVERITY_WARNING

    def test_detail_defaults_to_none(self):
        alert = Alert(kind=KIND_HALT, message="x", created_at=datetime.now(UTC))
        assert alert.detail is None


# ── halt_alert ──────────────────────────────────────────────────────────────

class TestHaltAlert:
    def test_kind_is_halt(self):
        a = halt_alert("10% drawdown")
        assert a.kind == KIND_HALT

    def test_severity_is_critical(self):
        a = halt_alert("drawdown")
        assert a.severity == SEVERITY_CRITICAL

    def test_message_contains_reason(self):
        reason = "10% peak-to-trough drawdown"
        a = halt_alert(reason)
        assert reason in a.message

    def test_detail_contains_reason_key(self):
        a = halt_alert("reason text")
        assert a.detail is not None
        assert "reason" in a.detail

    def test_created_at_is_utc(self):
        a = halt_alert("x")
        assert a.created_at.tzinfo is not None


# ── stale_data_alert ────────────────────────────────────────────────────────

class TestStaleDataAlert:
    def test_kind(self):
        a = stale_data_alert("broker_equity", 400.0, 300.0)
        assert a.kind == KIND_STALE_DATA

    def test_severity_is_warning(self):
        a = stale_data_alert("feed", 600.0, 300.0)
        assert a.severity == SEVERITY_WARNING

    def test_detail_has_source_and_thresholds(self):
        a = stale_data_alert("myFeed", 120.0, 60.0)
        assert a.detail["source"] == "myFeed"
        assert a.detail["silence_seconds"] == 120.0
        assert a.detail["threshold_seconds"] == 60.0

    def test_message_mentions_source(self):
        a = stale_data_alert("ohlcv_feed", 90.0, 60.0)
        assert "ohlcv_feed" in a.message


# ── heartbeat_alert ─────────────────────────────────────────────────────────

class TestHeartbeatAlert:
    def test_kind(self):
        a = heartbeat_alert(90.0, 60.0)
        assert a.kind == KIND_HEARTBEAT_SILENCE

    def test_severity_is_critical(self):
        a = heartbeat_alert(90.0, 60.0)
        assert a.severity == SEVERITY_CRITICAL

    def test_detail_fields(self):
        a = heartbeat_alert(120.0, 60.0)
        assert a.detail["silence_seconds"] == 120.0
        assert a.detail["max_silence_seconds"] == 60.0

    def test_message_contains_values(self):
        a = heartbeat_alert(90.0, 60.0)
        assert "90" in a.message
        assert "60" in a.message


# ── reconciliation_alert ────────────────────────────────────────────────────

class TestReconciliationAlert:
    def test_kind(self):
        a = reconciliation_alert(["ghost-1"], [])
        assert a.kind == KIND_RECONCILIATION_DRIFT

    def test_severity_is_critical(self):
        a = reconciliation_alert([], ["unmatched-1"])
        assert a.severity == SEVERITY_CRITICAL

    def test_detail_lists(self):
        a = reconciliation_alert(["g1", "g2"], ["u1"])
        assert a.detail["ghost_broker_orders"] == ["g1", "g2"]
        assert a.detail["unmatched_db_orders"] == ["u1"]

    def test_ghost_count_in_message(self):
        a = reconciliation_alert(["g1", "g2", "g3"], [])
        assert "3" in a.message

    def test_unmatched_count_in_message(self):
        a = reconciliation_alert([], ["u1", "u2"])
        assert "2" in a.message

    def test_both_lists_in_message(self):
        a = reconciliation_alert(["g1"], ["u1"])
        assert "ghost" in a.message.lower() or "drift" in a.message.lower()


# ── budget_alert ─────────────────────────────────────────────────────────────

class TestBudgetAlert:
    def test_kind(self):
        a = budget_alert("groq", "hourly", 0.30, 0.25)
        assert a.kind == KIND_LLM_BUDGET_BREACH

    def test_severity_is_warning(self):
        a = budget_alert("groq", "daily", 1.10, 1.00)
        assert a.severity == SEVERITY_WARNING

    def test_detail_fields(self):
        a = budget_alert("qwen", "hourly", 0.27, 0.25)
        assert a.detail["provider"] == "qwen"
        assert a.detail["window"] == "hourly"
        assert a.detail["spent_usd"] == 0.27
        assert a.detail["cap_usd"] == 0.25

    def test_message_mentions_provider_and_window(self):
        a = budget_alert("groq", "daily", 1.05, 1.00)
        assert "groq" in a.message
        assert "daily" in a.message
