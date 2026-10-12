"""Phase 6 scoped halt controller tests (spec §13.2/§13.3)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from axel.core.config import settings
from axel.core.types import Section
from axel.execution.reason_codes import HaltReason, HaltScope
from axel.risk.halt import CancelOutcome, HaltController

NOW = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)
TOKEN = settings.operator_rearm_secret.get_secret_value()


def _controller(tmp_path: Path, **kwargs) -> HaltController:
    return HaltController(state_file_path=tmp_path / "halt.json", **kwargs)


def test_starts_clear(tmp_path):
    halt = _controller(tmp_path)
    assert not halt.is_halted
    assert not halt.blocks_new_risk(Section.STOCKS)
    assert halt.allows_read_only()


def test_new_risk_halt_blocks_new_risk_but_not_read_only(tmp_path):
    halt = _controller(tmp_path)
    halt.trip(HaltReason.BROKER_OUTAGE, detail="down", source="watchdog")
    assert halt.is_halted
    assert halt.scope is HaltScope.NEW_RISK_ONLY
    assert halt.blocks_new_risk(Section.STOCKS)
    assert halt.allows_read_only()


def test_global_halt_blocks_every_section(tmp_path):
    halt = _controller(tmp_path)
    halt.trip(HaltReason.GLOBAL_DRAWDOWN, detail="dd", source="killswitch")
    assert halt.scope is HaltScope.GLOBAL
    assert halt.blocks_new_risk(Section.STOCKS)
    assert halt.blocks_new_risk(Section.CRYPTO)


def test_section_halt_only_blocks_that_section(tmp_path):
    halt = _controller(tmp_path)
    halt.trip(
        HaltReason.SECTION_LOSS,
        detail="loss",
        source="loss_manager",
        section=Section.STOCKS,
    )
    assert halt.blocks_new_risk(Section.STOCKS)
    assert not halt.blocks_new_risk(Section.CRYPTO)


def test_section_halt_requires_section(tmp_path):
    halt = _controller(tmp_path)
    with pytest.raises(ValueError):
        halt.trip(HaltReason.SECTION_LOSS, detail="loss", source="x", scope=HaltScope.SECTION)


def test_widest_scope_wins(tmp_path):
    halt = _controller(tmp_path)
    halt.trip(HaltReason.BROKER_OUTAGE, detail="down", source="watchdog")
    halt.trip(HaltReason.GLOBAL_DRAWDOWN, detail="dd", source="killswitch")
    assert halt.scope is HaltScope.GLOBAL


def test_halt_persists_across_instances(tmp_path):
    path = tmp_path / "halt.json"
    first = HaltController(state_file_path=path)
    first.trip(HaltReason.WATCHDOG_FAILURE, detail="missed heartbeat", source="watchdog")
    first.record_cancel_outcome("ord-1", CancelOutcome.SUCCEEDED)

    second = HaltController(state_file_path=path)
    assert second.is_halted
    assert second.scope is HaltScope.NEW_RISK_ONLY
    assert second.cancel_outcomes() == {"ord-1": CancelOutcome.SUCCEEDED}
    assert second.state().reason_codes == (HaltReason.WATCHDOG_FAILURE.value,)


def test_evaluate_helpers_trip_expected_reasons(tmp_path):
    halt = _controller(tmp_path)
    assert halt.evaluate_incidents(0, source="recon") is None
    trigger = halt.evaluate_incidents(2, source="recon")
    assert trigger is not None and trigger.reason is HaltReason.RECONCILIATION_DRIFT

    assert halt.evaluate_broker_health(True, source="health") is None
    assert halt.evaluate_broker_health(False, source="health").reason is HaltReason.BROKER_OUTAGE

    assert halt.evaluate_data_freshness(10, 60, source="md") is None
    assert halt.evaluate_data_freshness(120, 60, source="md").reason is HaltReason.STALE_MARKET_DATA

    assert halt.evaluate_repeated_rejections(1, 3, source="order") is None
    assert halt.evaluate_repeated_rejections(3, 3, source="order").reason is HaltReason.REPEATED_REJECTIONS


def test_default_policy_cancels_nothing(tmp_path):
    halt = _controller(tmp_path)
    halt.trip(HaltReason.BROKER_OUTAGE, detail="down", source="watchdog")
    assert halt.plan_cancellations(["ord-1", "ord-2"]) == []


def test_global_cancel_policy_plans_all_working_orders(tmp_path):
    halt = _controller(tmp_path, halt_cancel_policy=HaltScope.GLOBAL)
    halt.trip(HaltReason.GLOBAL_DRAWDOWN, detail="dd", source="killswitch")
    assert halt.plan_cancellations(["ord-1", "ord-2"]) == ["ord-1", "ord-2"]


def test_recovery_requires_authorization_evidence_and_clear_incidents(tmp_path):
    halt = _controller(tmp_path)
    halt.trip(HaltReason.BROKER_OUTAGE, detail="down", source="watchdog")

    with pytest.raises(PermissionError):
        halt.recover(
            operator_token="wrong",
            written_rationale="a long enough rationale",
            reconciliation_verified=True,
            blocking_incident_count=0,
        )
    with pytest.raises(ValueError):
        halt.recover(
            operator_token=TOKEN,
            written_rationale="short",
            reconciliation_verified=True,
            blocking_incident_count=0,
        )
    with pytest.raises(PermissionError):
        halt.recover(
            operator_token=TOKEN,
            written_rationale="a long enough rationale",
            reconciliation_verified=False,
            blocking_incident_count=0,
        )
    with pytest.raises(PermissionError):
        halt.recover(
            operator_token=TOKEN,
            written_rationale="a long enough rationale",
            reconciliation_verified=True,
            blocking_incident_count=1,
        )

    halt.recover(
        operator_token=TOKEN,
        written_rationale="broker connectivity verified and books reconciled",
        reconciliation_verified=True,
        blocking_incident_count=0,
    )
    assert not halt.is_halted
    assert halt.state().reason_codes == ()


def test_cancel_outcomes_recorded(tmp_path):
    halt = _controller(tmp_path)
    halt.trip(HaltReason.MANUAL_OPERATOR, detail="drill", source="operator")
    halt.record_cancel_outcome("ord-1", CancelOutcome.SUCCEEDED)
    halt.record_cancel_outcome("ord-2", CancelOutcome.FAILED)
    halt.record_cancel_outcome("ord-3", CancelOutcome.UNKNOWN)
    outcomes = halt.cancel_outcomes()
    assert outcomes["ord-1"] is CancelOutcome.SUCCEEDED
    assert outcomes["ord-2"] is CancelOutcome.FAILED
    assert outcomes["ord-3"] is CancelOutcome.UNKNOWN
