"""
Side-effect-free alert payload construction for Phase 4 dashboard & comms.

Alert kinds cover all required triggers:
  HALT               — global kill switch tripped
  STALE_DATA         — no new market data / equity snapshot within threshold
  HEARTBEAT_SILENCE  — primary service liveness check failed
  RECONCILIATION_DRIFT — DB vs broker state mismatch detected
  LLM_BUDGET_BREACH  — hourly or daily LLM spend limit exceeded

Transport is configured outside AXEL core; see axel/comms/telegram.py.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

# ── Alert kind constants ────────────────────────────────────────────────────

KIND_HALT = "halt"
KIND_STALE_DATA = "stale_data"
KIND_HEARTBEAT_SILENCE = "heartbeat_silence"
KIND_RECONCILIATION_DRIFT = "reconciliation_drift"
KIND_LLM_BUDGET_BREACH = "llm_budget_breach"

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


# ── Alert dataclass ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Alert:
    """Immutable alert payload. Has no transport or side effects."""
    kind: str
    message: str
    created_at: datetime
    severity: str = SEVERITY_WARNING
    detail: dict | None = None


# ── Factory functions ───────────────────────────────────────────────────────

def halt_alert(reason: str) -> Alert:
    """Fired when the global kill switch trips."""
    return Alert(
        kind=KIND_HALT,
        severity=SEVERITY_CRITICAL,
        message=f"AXEL HALT: {reason}",
        created_at=datetime.now(UTC),
        detail={"reason": reason},
    )


def stale_data_alert(source: str, silence_seconds: float, threshold_seconds: float) -> Alert:
    """Fired when a data source has not produced a new record within the threshold."""
    return Alert(
        kind=KIND_STALE_DATA,
        severity=SEVERITY_WARNING,
        message=(
            f"STALE DATA: '{source}' has been silent for {silence_seconds:.0f}s "
            f"(threshold: {threshold_seconds:.0f}s)."
        ),
        created_at=datetime.now(UTC),
        detail={
            "source": source,
            "silence_seconds": silence_seconds,
            "threshold_seconds": threshold_seconds,
        },
    )


def heartbeat_alert(silence_seconds: float, max_silence_seconds: float) -> Alert:
    """Fired when the primary service heartbeat has been silent too long."""
    return Alert(
        kind=KIND_HEARTBEAT_SILENCE,
        severity=SEVERITY_CRITICAL,
        message=(
            f"HEARTBEAT SILENCE: Core service unresponsive for {silence_seconds:.0f}s "
            f"(max allowed: {max_silence_seconds:.0f}s)."
        ),
        created_at=datetime.now(UTC),
        detail={
            "silence_seconds": silence_seconds,
            "max_silence_seconds": max_silence_seconds,
        },
    )


def reconciliation_alert(ghost_orders: list[str], unmatched_db_orders: list[str]) -> Alert:
    """Fired when reconciliation detects a DB vs broker state mismatch."""
    parts = []
    if ghost_orders:
        parts.append(f"{len(ghost_orders)} ghost broker order(s)")
    if unmatched_db_orders:
        parts.append(f"{len(unmatched_db_orders)} unmatched DB order(s)")
    summary = " and ".join(parts) if parts else "unknown drift"
    return Alert(
        kind=KIND_RECONCILIATION_DRIFT,
        severity=SEVERITY_CRITICAL,
        message=f"RECONCILIATION DRIFT: {summary} detected.",
        created_at=datetime.now(UTC),
        detail={
            "ghost_broker_orders": ghost_orders,
            "unmatched_db_orders": unmatched_db_orders,
        },
    )


def budget_alert(provider: str, window: str, spent_usd: float, cap_usd: float) -> Alert:
    """Fired when LLM spend approaches or breaches the hourly / daily cap."""
    return Alert(
        kind=KIND_LLM_BUDGET_BREACH,
        severity=SEVERITY_WARNING,
        message=(
            f"LLM BUDGET: {provider} {window} spend ${spent_usd:.4f} "
            f"has breached cap ${cap_usd:.4f}."
        ),
        created_at=datetime.now(UTC),
        detail={
            "provider": provider,
            "window": window,
            "spent_usd": spent_usd,
            "cap_usd": cap_usd,
        },
    )
