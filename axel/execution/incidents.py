"""
Incident model and store (Phase 6, spec §13.1).

Incidents are the durable, auditable record of an operational anomaly. Each
carries a stable ``reason_code``, a severity, and a **deterministic action set**
--- operational logic is never encoded only in free text. Unresolved critical
incidents block new risk-increasing orders.

This module has zero LLM imports.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from axel.core.ids import generate_id
from axel.execution.reason_codes import IncidentCategory, IncidentSeverity

# Deterministic action set per category. Values are stable tokens operators and
# automation can key on, not prose.
INCIDENT_POLICY: dict[IncidentCategory, tuple[IncidentSeverity, tuple[str, ...]]] = {
    IncidentCategory.UNEXPECTED_FILL: (
        IncidentSeverity.CRITICAL,
        ("BLOCK_NEW_RISK", "ALERT_OPERATOR", "RECONCILE"),
    ),
    IncidentCategory.UNKNOWN_BROKER_ORDER: (
        IncidentSeverity.CRITICAL,
        ("BLOCK_NEW_RISK", "ALERT_OPERATOR", "RECONCILE"),
    ),
    IncidentCategory.POSITION_MISMATCH: (
        IncidentSeverity.CRITICAL,
        ("BLOCK_NEW_RISK", "ALERT_OPERATOR", "RECONCILE"),
    ),
    IncidentCategory.CASH_MISMATCH: (
        IncidentSeverity.CRITICAL,
        ("BLOCK_NEW_RISK", "ALERT_OPERATOR", "RECONCILE"),
    ),
    IncidentCategory.STALE_MARKET_DATA: (
        IncidentSeverity.CRITICAL,
        ("BLOCK_NEW_RISK", "ALERT_OPERATOR"),
    ),
    IncidentCategory.BROKER_OUTAGE: (
        IncidentSeverity.CRITICAL,
        ("HALT_NEW_RISK", "ALERT_OPERATOR", "RECONCILE"),
    ),
    IncidentCategory.EXCESSIVE_LOSS: (
        IncidentSeverity.CRITICAL,
        ("HALT_ALL", "ALERT_OPERATOR"),
    ),
    IncidentCategory.WATCHDOG_FAILURE: (
        IncidentSeverity.CRITICAL,
        ("HALT_NEW_RISK", "ALERT_OPERATOR"),
    ),
    IncidentCategory.DATABASE_FAILURE: (
        IncidentSeverity.CRITICAL,
        ("HALT_NEW_RISK", "ALERT_OPERATOR"),
    ),
    IncidentCategory.AUTH_PERMISSION_FAILURE: (
        IncidentSeverity.CRITICAL,
        ("BLOCK_NEW_RISK", "ALERT_OPERATOR"),
    ),
    IncidentCategory.DUPLICATE_SUBMISSION: (
        IncidentSeverity.WARNING,
        ("ALERT_OPERATOR", "RECONCILE"),
    ),
    IncidentCategory.REPEATED_REJECTIONS: (
        IncidentSeverity.WARNING,
        ("ALERT_OPERATOR",),
    ),
    IncidentCategory.UNKNOWN: (
        IncidentSeverity.WARNING,
        ("ALERT_OPERATOR", "RECONCILE"),
    ),
}

_SEVERITY_RANK = {
    IncidentSeverity.INFO: 0,
    IncidentSeverity.WARNING: 1,
    IncidentSeverity.CRITICAL: 2,
}


@dataclass
class Incident:
    """A single operational incident, open until explicitly resolved."""

    incident_id: str
    category: IncidentCategory
    severity: IncidentSeverity
    reason_code: str
    actions: tuple[str, ...]
    summary: str
    detected_at: datetime
    dedup_key: str
    correlation_id: str | None = None
    symbol: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    evidence: dict = field(default_factory=dict)
    occurrence_count: int = 1
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None
    resolution_evidence: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    @property
    def blocks_new_risk(self) -> bool:
        return self.is_open and self.severity is IncidentSeverity.CRITICAL


class IncidentStore:
    """In-memory incident registry with de-duplication and resolution."""

    def __init__(self) -> None:
        self._by_id: dict[str, Incident] = {}
        self._by_dedup: dict[str, str] = {}
        self._lock = threading.RLock()

    def open_or_update(
        self,
        *,
        category: IncidentCategory,
        reason_code: str,
        summary: str,
        dedup_key: str,
        severity: IncidentSeverity | None = None,
        correlation_id: str | None = None,
        symbol: str | None = None,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
        evidence: dict | None = None,
        detected_at: datetime | None = None,
    ) -> Incident:
        """Create a new incident or bump an existing open one with the same key."""
        with self._lock:
            existing_id = self._by_dedup.get(dedup_key)
            if existing_id is not None:
                existing = self._by_id[existing_id]
                if existing.is_open:
                    existing.occurrence_count += 1
                    if evidence:
                        existing.evidence.update(evidence)
                    return existing

            policy_severity, actions = INCIDENT_POLICY.get(
                category, (IncidentSeverity.WARNING, ("ALERT_OPERATOR",))
            )
            incident = Incident(
                incident_id=generate_id("inc"),
                category=category,
                severity=severity or policy_severity,
                reason_code=reason_code,
                actions=actions,
                summary=summary,
                detected_at=detected_at or datetime.now(UTC),
                dedup_key=dedup_key,
                correlation_id=correlation_id,
                symbol=symbol,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                evidence=dict(evidence or {}),
            )
            self._by_id[incident.incident_id] = incident
            self._by_dedup[dedup_key] = incident.incident_id
            return incident

    def resolve(
        self,
        incident_id: str,
        *,
        resolved_by: str,
        note: str,
        evidence: dict | None = None,
        resolved_at: datetime | None = None,
    ) -> Incident:
        with self._lock:
            incident = self._by_id.get(incident_id)
            if incident is None:
                raise KeyError(f"Unknown incident {incident_id!r}.")
            if not resolved_by.strip():
                raise ValueError("Incident resolution requires a named authority.")
            incident.resolved_at = resolved_at or datetime.now(UTC)
            incident.resolved_by = resolved_by
            incident.resolution_note = note
            incident.resolution_evidence = dict(evidence or {})
            return incident

    def get(self, incident_id: str) -> Incident | None:
        with self._lock:
            return self._by_id.get(incident_id)

    def all(self) -> list[Incident]:
        with self._lock:
            return list(self._by_id.values())

    def open_incidents(self) -> list[Incident]:
        return [i for i in self.all() if i.is_open]

    def blocking_incidents(self) -> list[Incident]:
        """Open critical incidents that must block new risk-increasing orders."""
        return [i for i in self.all() if i.blocks_new_risk]

    def critical_open_count(self) -> int:
        return len(self.blocking_incidents())

    def highest_severity(self) -> IncidentSeverity | None:
        open_incidents = self.open_incidents()
        if not open_incidents:
            return None
        return max(open_incidents, key=lambda i: _SEVERITY_RANK[i.severity]).severity
