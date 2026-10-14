"""Phase 6 live configuration gate tests (spec §4.4 / §8.1)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from axel.core.types import Section, TradingMode
from axel.execution.live_gate import (
    ApprovalMetadata,
    LiveConfig,
    LiveGate,
    validate_live_config,
)
from axel.execution.reason_codes import LiveGateReason


def _config(**overrides) -> LiveConfig:
    base = {
        "config_version": "live-2026.06.01",
        "environment": TradingMode.LIVE,
        "broker": "alpaca",
        "account_id": "acct-tiny-1",
        "allowed_section": Section.STOCKS,
        "max_live_capital": Decimal(1000),
        "max_position_notional": Decimal(200),
        "max_order_notional": Decimal(100),
        "max_daily_loss": Decimal(50),
        "max_concurrent_positions": 3,
        "max_order_frequency": 5,
        "order_frequency_window_seconds": 60,
        "allowed_symbols": frozenset({"SPY", "AAPL"}),
        "manual_approval_required": True,
        "phase5_evidence_ref": "docs/phase-5-evidence.md#gate",
        "phase5_gate_passed": True,
        "approval_metadata": ApprovalMetadata(
            approved_by="risk-ops",
            approved_at=datetime(2026, 6, 1, tzinfo=UTC),
            reference="CAB-1234",
        ),
    }
    base.update(overrides)
    return LiveConfig(**base)


def test_live_disabled_by_default():
    gate = LiveGate()
    decision = gate.validate()
    assert not decision.enabled
    assert LiveGateReason.NO_CONFIGURATION in decision.reasons
    # Even with healthy runtime facts it stays disabled.
    runtime = gate.evaluate(kill_switch_active=False, broker_connected=True)
    assert not runtime.enabled


def test_valid_configuration_enables_live_when_runtime_ok():
    gate = LiveGate(_config())
    decision = gate.evaluate(
        kill_switch_active=False,
        watchdog_healthy=True,
        broker_connected=True,
        observed_account_id="acct-tiny-1",
    )
    assert decision.enabled
    assert decision.config_version == "live-2026.06.01"
    assert decision.reasons == ()


def test_missing_broker_connectivity_fails_closed():
    gate = LiveGate(_config())
    decision = gate.evaluate(broker_connected=False)
    assert not decision.enabled
    assert LiveGateReason.BROKER_ACCOUNT_MISMATCH in decision.reasons


def test_account_mismatch_fails_closed():
    gate = LiveGate(_config())
    decision = gate.evaluate(broker_connected=True, observed_account_id="someone-else")
    assert not decision.enabled
    assert LiveGateReason.BROKER_ACCOUNT_MISMATCH in decision.reasons


def test_kill_switch_active_fails_closed():
    gate = LiveGate(_config())
    decision = gate.evaluate(
        kill_switch_active=True, broker_connected=True, observed_account_id="acct-tiny-1"
    )
    assert not decision.enabled
    assert LiveGateReason.KILL_SWITCH_ACTIVE in decision.reasons


def test_watchdog_unhealthy_fails_closed():
    gate = LiveGate(_config())
    decision = gate.evaluate(watchdog_healthy=False, broker_connected=True)
    assert not decision.enabled
    assert LiveGateReason.WATCHDOG_UNHEALTHY in decision.reasons


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"config_version": ""}, LiveGateReason.VERSION_MISSING),
        ({"environment": TradingMode.PAPER}, LiveGateReason.ENVIRONMENT_NOT_LIVE),
        ({"allowed_section": Section.CRYPTO}, LiveGateReason.SECTION_NOT_STOCKS),
        ({"max_live_capital": Decimal(0)}, LiveGateReason.CAPITAL_INVALID),
        ({"max_position_notional": Decimal(0)}, LiveGateReason.POSITION_LIMIT_INVALID),
        ({"max_order_notional": Decimal(0)}, LiveGateReason.ORDER_LIMIT_INVALID),
        ({"max_daily_loss": Decimal(0)}, LiveGateReason.DAILY_LOSS_INVALID),
        ({"max_concurrent_positions": 0}, LiveGateReason.CONCURRENCY_INVALID),
        ({"max_order_frequency": 0}, LiveGateReason.ORDER_FREQUENCY_INVALID),
        ({"allowed_symbols": frozenset()}, LiveGateReason.UNIVERSE_EMPTY),
        ({"manual_approval_required": False}, LiveGateReason.MANUAL_APPROVAL_DISABLED),
        ({"phase5_gate_passed": False}, LiveGateReason.PHASE5_EVIDENCE_MISSING),
        ({"phase5_evidence_ref": ""}, LiveGateReason.PHASE5_EVIDENCE_MISSING),
        ({"approval_metadata": None}, LiveGateReason.APPROVAL_METADATA_MISSING),
    ],
)
def test_invalid_fields_fail_closed(overrides, reason):
    decision = validate_live_config(_config(**overrides))
    assert not decision.enabled
    assert reason in decision.reasons


def test_inconsistent_limits_fail_closed():
    # Order cap larger than position cap.
    decision = validate_live_config(
        _config(max_order_notional=Decimal(500), max_position_notional=Decimal(200))
    )
    assert not decision.enabled
    assert LiveGateReason.INCONSISTENT_LIMITS in decision.reasons

    # Position cap larger than section capital.
    decision = validate_live_config(
        _config(max_position_notional=Decimal(5000), max_live_capital=Decimal(1000))
    )
    assert not decision.enabled
    assert LiveGateReason.INCONSISTENT_LIMITS in decision.reasons


def test_stale_configuration_fails_closed_when_age_bound_supplied():
    stale = _config(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    gate = LiveGate(stale, max_config_age_seconds=3600)
    decision = gate.evaluate(
        broker_connected=True,
        observed_account_id="acct-tiny-1",
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert not decision.enabled


def test_config_age_not_enforced_without_explicit_bound():
    old = _config(created_at=datetime(2000, 1, 1, tzinfo=UTC))
    gate = LiveGate(old)  # no max_config_age_seconds
    decision = gate.evaluate(
        broker_connected=True, observed_account_id="acct-tiny-1"
    )
    assert decision.enabled


def test_live_config_coerces_floats_to_decimal():
    cfg = _config(max_live_capital=1000.0, max_order_notional="100")
    assert isinstance(cfg.max_live_capital, Decimal)
    assert cfg.max_order_notional == Decimal(100)
