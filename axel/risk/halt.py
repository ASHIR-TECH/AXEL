"""
Independent, scoped halt controller (Phase 6, spec §13.2/§13.3).

The existing :class:`~axel.risk.killswitch.KillSwitch` trips a single global,
all-or-nothing breaker. Phase 6 needs more:

* **scopes** --- a halt may be ``GLOBAL``, ``SECTION`` or ``NEW_RISK_ONLY``, so a
  risk event must never remove the ability to reconcile or to cancel/reduce
  exposure (spec §4.8);
* **stable reason codes** on every trip, never free text only;
* **persistence independent of the database** --- the file is written directly so
  a halt survives a database outage (spec §13.2);
* **recorded cancellation outcomes** (succeeded / failed / unknown);
* **explicit, authorized recovery** --- re-enabling live orders needs an operator
  token, a written rationale, a verified reconciliation and no blocking
  incidents.

This module has zero LLM imports and no dependency on the database or the broker
SDK; the execution layer plans cancellations from it and reports outcomes back.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from axel.core.clock import Clock, RealClock
from axel.core.config import settings
from axel.core.logging import logger
from axel.core.types import Section
from axel.execution.reason_codes import HaltReason, HaltScope

# Deterministic default scope per trigger reason. A review may override the scope
# at the call site, but the default is never "silently allow new risk".
DEFAULT_HALT_SCOPE: dict[HaltReason, HaltScope] = {
    HaltReason.GLOBAL_DRAWDOWN: HaltScope.GLOBAL,
    HaltReason.SECTION_LOSS: HaltScope.SECTION,
    HaltReason.BROKER_OUTAGE: HaltScope.NEW_RISK_ONLY,
    HaltReason.STALE_MARKET_DATA: HaltScope.NEW_RISK_ONLY,
    HaltReason.WATCHDOG_FAILURE: HaltScope.NEW_RISK_ONLY,
    HaltReason.RECONCILIATION_DRIFT: HaltScope.NEW_RISK_ONLY,
    HaltReason.DATABASE_FAILURE: HaltScope.NEW_RISK_ONLY,
    HaltReason.REPEATED_REJECTIONS: HaltScope.NEW_RISK_ONLY,
    HaltReason.MANUAL_OPERATOR: HaltScope.GLOBAL,
}


class CancelOutcome(str, Enum):
    """Recorded result of a post-halt cancellation attempt (spec §13.2 step 6)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HaltTrigger:
    """An immutable record of what tripped a halt and how widely."""

    reason: HaltReason
    detail: str
    scope: HaltScope
    occurred_at: datetime
    source: str
    section: Section | None = None


@dataclass(frozen=True)
class HaltState:
    """Snapshot of the current halt, for gates, dashboards and audits."""

    halted: bool
    scope: HaltScope | None = None
    section: Section | None = None
    reason_codes: tuple[str, ...] = ()
    triggered_at: datetime | None = None
    triggers: tuple[HaltTrigger, ...] = ()


class HaltController:
    """Scoped, persistent, database-independent halt controller."""

    def __init__(
        self,
        state_file_path: Path | None = None,
        clock: Clock | None = None,
        *,
        halt_cancel_policy: HaltScope | None = None,
    ):
        self.clock = clock or RealClock()
        self.state_file = state_file_path or Path(".axel_halt.json")
        self._halted: bool = False
        self._scope: HaltScope | None = None
        self._section: Section | None = None
        self._triggers: list[HaltTrigger] = []
        self._cancel_outcomes: dict[str, CancelOutcome] = {}
        # Reviewed halt policy: which working orders may be auto-cancelled. The
        # conservative default is to cancel nothing automatically.
        self._halt_cancel_policy = halt_cancel_policy
        self._load_state()

    # ── Persistence (DB-independent) ────────────────────────────────────────
    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load halt state file", extra={"error": str(exc)})
            return
        self._halted = bool(data.get("halted", False))
        scope = data.get("scope")
        self._scope = HaltScope(scope) if scope else None
        section = data.get("section")
        self._section = Section(section) if section else None
        self._triggers = [
            HaltTrigger(
                reason=HaltReason(t["reason"]),
                detail=t.get("detail", ""),
                scope=HaltScope(t["scope"]),
                occurred_at=datetime.fromisoformat(t["occurred_at"]),
                source=t.get("source", "unknown"),
                section=Section(t["section"]) if t.get("section") else None,
            )
            for t in data.get("triggers", [])
        ]
        self._cancel_outcomes = {
            k: CancelOutcome(v) for k, v in data.get("cancel_outcomes", {}).items()
        }

    def _save_state(self) -> None:
        data = {
            "halted": self._halted,
            "scope": self._scope.value if self._scope else None,
            "section": self._section.value if self._section else None,
            "triggers": [
                {
                    "reason": t.reason.value,
                    "detail": t.detail,
                    "scope": t.scope.value,
                    "occurred_at": t.occurred_at.isoformat(),
                    "source": t.source,
                    "section": t.section.value if t.section else None,
                }
                for t in self._triggers
            ],
            "cancel_outcomes": {k: v.value for k, v in self._cancel_outcomes.items()},
            "updated_at": self.clock.now().isoformat(),
        }
        try:
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            # A DB outage must not block halting; but a disk failure is serious.
            logger.critical("Failed to persist halt state", extra={"error": str(exc)})

    # ── State ───────────────────────────────────────────────────────────────
    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def scope(self) -> HaltScope | None:
        return self._scope

    def state(self) -> HaltState:
        return HaltState(
            halted=self._halted,
            scope=self._scope,
            section=self._section,
            reason_codes=tuple(t.reason.value for t in self._triggers),
            triggered_at=self._triggers[0].occurred_at if self._triggers else None,
            triggers=tuple(self._triggers),
        )

    def blocks_new_risk(self, section: Section | None = None) -> bool:
        """Whether new *risk-increasing* orders are blocked for this section."""
        if not self._halted or self._scope is None:
            return False
        if self._scope is HaltScope.GLOBAL or self._scope is HaltScope.NEW_RISK_ONLY:
            return True
        return section is not None and section == self._section

    def allows_read_only(self) -> bool:
        """Read-only queries and reconciliation are always permitted."""
        return True

    # ── Trips ───────────────────────────────────────────────────────────────
    def trip(
        self,
        reason: HaltReason,
        *,
        detail: str,
        source: str,
        scope: HaltScope | None = None,
        section: Section | None = None,
    ) -> HaltTrigger:
        resolved_scope = scope or DEFAULT_HALT_SCOPE.get(reason, HaltScope.NEW_RISK_ONLY)
        if resolved_scope is HaltScope.SECTION and section is None:
            raise ValueError("A SECTION halt requires the affected section.")
        trigger = HaltTrigger(
            reason=reason,
            detail=detail,
            scope=resolved_scope,
            occurred_at=self.clock.now(),
            source=source,
            section=section,
        )
        self._triggers.append(trigger)
        self._halted = True
        # A wider scope subsumes a narrower one; keep the widest.
        if self._scope is None or _scope_rank(resolved_scope) > _scope_rank(self._scope):
            self._scope = resolved_scope
            self._section = section if resolved_scope is HaltScope.SECTION else None
        self._save_state()
        logger.critical(
            "HALT TRIPPED",
            extra={
                "reason": reason.value,
                "scope": resolved_scope.value,
                "source": source,
                "detail": detail,
            },
        )
        return trigger

    # ── Trigger evaluation helpers ──────────────────────────────────────────
    def evaluate_incidents(self, blocking_incident_count: int, *, source: str) -> HaltTrigger | None:
        if blocking_incident_count > 0:
            return self.trip(
                HaltReason.RECONCILIATION_DRIFT,
                detail=f"{blocking_incident_count} unresolved critical incident(s).",
                source=source,
                scope=HaltScope.NEW_RISK_ONLY,
            )
        return None

    def evaluate_broker_health(self, healthy: bool, *, source: str) -> HaltTrigger | None:
        if not healthy:
            return self.trip(
                HaltReason.BROKER_OUTAGE,
                detail="Broker health check failed.",
                source=source,
            )
        return None

    def evaluate_data_freshness(
        self, age_seconds: float, max_age_seconds: int, *, source: str
    ) -> HaltTrigger | None:
        if age_seconds > max_age_seconds:
            return self.trip(
                HaltReason.STALE_MARKET_DATA,
                detail=f"Market data age {age_seconds:.0f}s exceeds {max_age_seconds}s.",
                source=source,
            )
        return None

    def evaluate_repeated_rejections(
        self, rejection_count: int, threshold: int, *, source: str
    ) -> HaltTrigger | None:
        if rejection_count >= threshold:
            return self.trip(
                HaltReason.REPEATED_REJECTIONS,
                detail=f"{rejection_count} rejections at/over threshold {threshold}.",
                source=source,
            )
        return None

    # ── Cancellation planning/recording (execution applies them) ─────────────
    def plan_cancellations(self, open_client_order_ids: list[str]) -> list[str]:
        """
        Which working orders the reviewed halt policy says may be cancelled.

        Default policy cancels nothing automatically; a GLOBAL policy cancels all
        listed working orders. The execution layer performs the calls and reports
        outcomes back via :meth:`record_cancel_outcome`.
        """
        if not self._halted or self._halt_cancel_policy is None:
            return []
        if self._halt_cancel_policy is HaltScope.GLOBAL:
            return list(open_client_order_ids)
        return []

    def record_cancel_outcome(self, client_order_id: str, outcome: CancelOutcome) -> None:
        self._cancel_outcomes[client_order_id] = outcome
        self._save_state()
        logger.warning(
            "Halt cancellation outcome recorded",
            extra={"client_order_id": client_order_id, "outcome": outcome.value},
        )

    def cancel_outcomes(self) -> dict[str, CancelOutcome]:
        return dict(self._cancel_outcomes)

    # ── Recovery ────────────────────────────────────────────────────────────
    def recover(
        self,
        *,
        operator_token: str,
        written_rationale: str,
        reconciliation_verified: bool,
        blocking_incident_count: int,
    ) -> None:
        """
        Explicitly re-enable ordering after a halt (spec §13.3).

        Requires valid operator authorization, a written rationale, a verified
        reconciliation and no remaining blocking incidents. Live activation still
        has to be completed again through the fail-closed live gate.
        """
        expected_secret = settings.operator_rearm_secret.get_secret_value()
        provided = hashlib.sha256(operator_token.encode("utf-8")).hexdigest()
        expected = hashlib.sha256(expected_secret.encode("utf-8")).hexdigest()
        if provided != expected:
            logger.warning("Failed halt recovery attempt: invalid operator token.")
            raise PermissionError("Access denied: invalid operator token.")

        if not written_rationale or len(written_rationale.strip()) < 10:
            raise ValueError("Recovery requires an auditable written rationale (minimum 10 characters).")
        if not reconciliation_verified:
            raise PermissionError("Recovery requires a verified reconciliation.")
        if blocking_incident_count > 0:
            raise PermissionError(
                f"Recovery blocked: {blocking_incident_count} unresolved critical incident(s)."
            )

        self._halted = False
        self._scope = None
        self._section = None
        self._triggers = []
        self._save_state()
        logger.info("HALT CLEARED by authorized operator.", extra={"rationale": written_rationale})


def _scope_rank(scope: HaltScope) -> int:
    return {
        HaltScope.NEW_RISK_ONLY: 0,
        HaltScope.SECTION: 1,
        HaltScope.GLOBAL: 2,
    }[scope]
