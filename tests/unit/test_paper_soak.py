from datetime import UTC, datetime, timedelta

from axel.operations.paper_soak import (
    PaperTradeObservation,
    evaluate_promotion_gate,
    record_incident,
)


def test_promotion_gate_fails_closed_for_new_soak() -> None:
    report = evaluate_promotion_gate(
        started_at=datetime.now(UTC), observations=[], unresolved_reconciliation_breaks=0,
        killswitch_drill_passed_at=None, recovery_drill_passed=False,
    )
    assert not report.eligible
    assert "SOAK_DURATION_INCOMPLETE" in report.reasons
    assert "INSUFFICIENT_PAPER_TRADES" in report.reasons


def test_promotion_gate_accepts_complete_evidence() -> None:
    observations = [PaperTradeObservation(0.5, True, 1.0, 2.0) for _ in range(200)]
    now = datetime.now(UTC)
    report = evaluate_promotion_gate(
        started_at=now - timedelta(days=56), observations=observations,
        unresolved_reconciliation_breaks=0, killswitch_drill_passed_at=now,
        recovery_drill_passed=True, now=now,
    )
    assert report.eligible


def test_incident_is_written_to_audit_log(in_memory_db) -> None:
    incident = record_incident(in_memory_db, severity="warning", summary="Test incident", details={"source": "test"})
    assert incident.event_type == "paper_soak_incident"
