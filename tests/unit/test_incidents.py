"""Phase 6 incident management tests (spec §13.1)."""

import pytest

from axel.execution.incidents import INCIDENT_POLICY, IncidentStore
from axel.execution.reason_codes import IncidentCategory, IncidentSeverity


def test_open_or_update_deduplicates_open_incidents():
    store = IncidentStore()
    first = store.open_or_update(
        category=IncidentCategory.POSITION_MISMATCH,
        reason_code="RECON_POSITION_MISMATCH",
        summary="drift",
        dedup_key="k1",
    )
    second = store.open_or_update(
        category=IncidentCategory.POSITION_MISMATCH,
        reason_code="RECON_POSITION_MISMATCH",
        summary="drift",
        dedup_key="k1",
    )
    assert first.incident_id == second.incident_id
    assert second.occurrence_count == 2
    assert len(store.all()) == 1


def test_critical_incident_blocks_new_risk_until_resolved():
    store = IncidentStore()
    incident = store.open_or_update(
        category=IncidentCategory.UNKNOWN_BROKER_ORDER,
        reason_code="RECON_BROKER_ORDER_WITHOUT_INTENT",
        summary="ghost",
        dedup_key="k2",
    )
    assert incident.severity is IncidentSeverity.CRITICAL
    assert "BLOCK_NEW_RISK" in incident.actions
    assert store.critical_open_count() == 1

    store.resolve(incident.incident_id, resolved_by="ops", note="verified with broker")
    assert store.critical_open_count() == 0
    assert store.get(incident.incident_id).resolved_at is not None


def test_resolution_requires_named_authority():
    store = IncidentStore()
    incident = store.open_or_update(
        category=IncidentCategory.CASH_MISMATCH,
        reason_code="RECON_CASH_MISMATCH",
        summary="cash",
        dedup_key="k3",
    )
    with pytest.raises(ValueError):
        store.resolve(incident.incident_id, resolved_by="  ", note="x")


def test_resolved_incident_is_not_reopened_by_occurrence():
    store = IncidentStore()
    incident = store.open_or_update(
        category=IncidentCategory.UNKNOWN,
        reason_code="RECON_UNEXPLAINED_DRIFT",
        summary="x",
        dedup_key="k4",
    )
    store.resolve(incident.incident_id, resolved_by="ops", note="ok")
    again = store.open_or_update(
        category=IncidentCategory.UNKNOWN,
        reason_code="RECON_UNEXPLAINED_DRIFT",
        summary="x",
        dedup_key="k4",
    )
    assert again.incident_id != incident.incident_id
    assert again.is_open


def test_every_category_has_a_deterministic_policy():
    for category in IncidentCategory:
        assert category in INCIDENT_POLICY
        severity, actions = INCIDENT_POLICY[category]
        assert isinstance(severity, IncidentSeverity)
        assert actions and all(isinstance(a, str) for a in actions)


def test_warning_incident_does_not_block_new_risk():
    store = IncidentStore()
    incident = store.open_or_update(
        category=IncidentCategory.REPEATED_REJECTIONS,
        reason_code="HALT_REPEATED_REJECTIONS",
        summary="many rejects",
        dedup_key="k5",
    )
    assert incident.severity is IncidentSeverity.WARNING
    assert not incident.blocks_new_risk
    assert store.critical_open_count() == 0


def test_highest_severity_reported():
    store = IncidentStore()
    store.open_or_update(
        category=IncidentCategory.REPEATED_REJECTIONS,
        reason_code="r",
        summary="warn",
        dedup_key="w",
    )
    store.open_or_update(
        category=IncidentCategory.POSITION_MISMATCH,
        reason_code="r2",
        summary="crit",
        dedup_key="c",
    )
    assert store.highest_severity() is IncidentSeverity.CRITICAL
