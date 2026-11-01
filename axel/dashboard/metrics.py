"""
Prometheus-compatible metrics publisher for AXEL.

collect_metrics() aggregates live system state into a MetricsSnapshot.
format_prometheus() serialises it to the Prometheus text exposition format
(https://prometheus.io/docs/instrumenting/exposition_formats/) so it can be
scraped by any Prometheus-compatible endpoint or file exporter — no HTTP server
is bundled here, keeping this module pure and testable.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from axel.core.logging import logger
from axel.db.models import (
    ApprovalModel,
    IncidentModel,
    OperationalEventModel,
    OrderModel,
    PositionModel,
    ReconciliationRunModel,
    TradeProposalModel,
)
from axel.execution.approval import ApprovalService
from axel.execution.broker_base import BrokerAdapter
from axel.execution.incidents import IncidentStore
from axel.risk.halt import HaltController
from axel.risk.killswitch import KillSwitch

# ── Snapshot dataclass ──────────────────────────────────────────────────────

@dataclass
class MetricsSnapshot:
    """Point-in-time snapshot of all key AXEL operational metrics."""
    collected_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Equity & drawdown
    total_equity_usd: float = 0.0
    cash_usd: float = 0.0
    drawdown_pct: float = 0.0
    high_water_mark_usd: float = 0.0

    # Exposure
    gross_exposure_usd: float = 0.0
    net_exposure_usd: float = 0.0
    open_positions_count: int = 0

    # Kill switch
    kill_switch_active: bool = False

    # HITL approvals
    pending_hitl_tickets: int = 0

    # Proposal activity (last 1h / 24h)
    proposals_last_1h: int = 0
    proposals_last_24h: int = 0
    approved_proposals_last_24h: int = 0
    rejected_proposals_last_24h: int = 0

    # Phase 6 operational fields
    live_enabled: bool = False
    halt_active: bool = False
    halt_scope: str | None = None
    open_incidents_total: int = 0
    critical_incidents_total: int = 0
    unreconciled_orders: int = 0
    last_reconciliation_age_seconds: float | None = None
    reconciliation_mismatches: int = 0

    # External API and transaction health
    api_provider_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    api_failures_24h: dict[tuple[str, str], int] = field(default_factory=dict)
    order_failures_24h: dict[str, int] = field(default_factory=dict)
    operational_failures_24h: dict[tuple[str, str, str], int] = field(default_factory=dict)


# ── Collection ──────────────────────────────────────────────────────────────

_OPEN_ORDER_STATES = (
    "created",
    "approved",
    "submitted",
    "acknowledged",
    "partially_filled",
    "cancel_pending",
    "unknown",
    "accepted",
)


def _count_pending_approvals(session: Session) -> int:
    """Durable approval requests that have not been decided yet."""
    stmt = (
        select(func.count())
        .select_from(ApprovalModel)
        .where(ApprovalModel.status == "pending")
    )
    return session.execute(stmt).scalar_one() or 0


def _db_incident_counts(session: Session) -> tuple[int, int]:
    """(open incidents, open critical incidents) from the durable table."""
    open_stmt = (
        select(func.count())
        .select_from(IncidentModel)
        .where(IncidentModel.status == "open")
    )
    critical_stmt = (
        select(func.count())
        .select_from(IncidentModel)
        .where(
            IncidentModel.status == "open",
            IncidentModel.severity == "critical",
        )
    )
    return (
        session.execute(open_stmt).scalar_one() or 0,
        session.execute(critical_stmt).scalar_one() or 0,
    )


def _db_reconciliation_state(session: Session, now: datetime) -> tuple[int, float | None, int]:
    """(unreconciled orders, age of last recon run, last mismatch count)."""
    order_stmt = (
        select(func.count())
        .select_from(OrderModel)
        .where(OrderModel.state.in_(_OPEN_ORDER_STATES))
    )
    unreconciled = session.execute(order_stmt).scalar_one() or 0

    latest = session.execute(
        select(ReconciliationRunModel)
        .order_by(ReconciliationRunModel.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return unreconciled, None, 0

    age: float | None = None
    if latest.completed_at is not None:
        completed = latest.completed_at
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=UTC)
        age = max(0.0, (now - completed).total_seconds())
    return unreconciled, age, int(latest.mismatch_count or 0)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _db_api_provider_states(session: Session, now: datetime) -> dict[str, dict[str, Any]]:
    """Latest provider health, shaped for labelled Prometheus gauges."""
    from axel.observability.api_health import load_provider_states

    states: dict[str, dict[str, Any]] = {}
    for row in load_provider_states(session):
        last_success = _as_utc(row.last_success_at)
        states[row.provider] = {
            "last_success_age_seconds": (
                max(0.0, (now - last_success).total_seconds()) if last_success else None
            ),
            "last_outcome": row.last_outcome,
            "last_status_code": row.last_status_code,
            "consecutive_failures": int(row.consecutive_failures or 0),
            "quota_exhausted": bool(row.quota_exhausted),
            "retry_after_seconds": row.retry_after_seconds,
            "rate_limit_remaining": row.rate_limit_remaining,
            "prompt_tokens_total": int(row.prompt_tokens_total or 0),
            "completion_tokens_total": int(row.completion_tokens_total or 0),
            "estimated_cost_usd_total": float(row.estimated_cost_usd_total or 0.0),
        }
    return states


def _db_failure_counts(
    session: Session, since: datetime
) -> tuple[
    dict[tuple[str, str], int],
    dict[str, int],
    dict[tuple[str, str, str], int],
]:
    """(API failures, order failures, all operational failures) in the window."""
    stmt = (
        select(
            OperationalEventModel.category,
            OperationalEventModel.component,
            OperationalEventModel.event,
            OperationalEventModel.outcome,
            func.count(),
        )
        .where(OperationalEventModel.occurred_at >= since)
        .group_by(
            OperationalEventModel.category,
            OperationalEventModel.component,
            OperationalEventModel.event,
            OperationalEventModel.outcome,
        )
    )
    api_failures: dict[tuple[str, str], int] = {}
    order_failures: dict[str, int] = {}
    operational_failures: dict[tuple[str, str, str], int] = {}
    for category, component, event, outcome, count in session.execute(stmt).all():
        category = str(category)
        operational_key = (category, str(event), str(outcome))
        operational_failures[operational_key] = (
            operational_failures.get(operational_key, 0) + int(count)
        )
        if category == "api":
            key = (str(component), str(outcome))
            api_failures[key] = api_failures.get(key, 0) + int(count)
        elif category == "order":
            order_failures[str(event)] = order_failures.get(str(event), 0) + int(count)
    return api_failures, order_failures, operational_failures


def collect_metrics(
    session: Session,
    broker: BrokerAdapter,
    kill_switch: KillSwitch,
    approval_service: ApprovalService | None = None,
    *,
    halt: HaltController | None = None,
    incident_store: IncidentStore | None = None,
    live_enabled: bool = False,
    unreconciled_orders: int | None = None,
    last_reconciliation_age_seconds: float | None = None,
    reconciliation_mismatches: int | None = None,
) -> MetricsSnapshot:
    """
    Aggregates live state from broker, kill switch, approval queue, and DB.
    Pure aggregation — no mutations.

    When the in-memory services are omitted (the normal case for a standalone
    Prometheus exporter, which cannot see another process's memory), the
    equivalent durable state is read from the database. Explicit arguments
    always win over the DB fallback.
    """
    snap = MetricsSnapshot()

    # ── Equity from broker ──────────────────────────────────────────────
    try:
        account = broker.get_account_summary()
        snap.total_equity_usd = account.get("total_equity", 0.0)
        snap.cash_usd = account.get("cash", 0.0)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("axel").warning(
            "MetricsSnapshot: broker unreachable, equity left at zero.", extra={"error": str(exc)}
        )

    # ── Kill switch state ───────────────────────────────────────────────
    snap.kill_switch_active = kill_switch.is_halted
    snap.high_water_mark_usd = kill_switch.high_water_mark
    if snap.high_water_mark_usd > 0 and snap.total_equity_usd > 0:
        snap.drawdown_pct = max(
            0.0,
            (snap.high_water_mark_usd - snap.total_equity_usd) / snap.high_water_mark_usd,
        )

    # ── Open positions from broker ──────────────────────────────────────
    try:
        positions = broker.get_positions()
        snap.open_positions_count = len(positions)
        snap.gross_exposure_usd = sum(
            abs(p.qty * p.current_price) for p in positions
        )
        # Net exposure: long market value − short market value
        snap.net_exposure_usd = sum(
            (p.qty if p.side.value == "buy" else -p.qty) * p.current_price
            for p in positions
        )
    except Exception:  # noqa: BLE001
        # Fall back to DB snapshot for positions
        db_positions = session.query(PositionModel).all()
        snap.open_positions_count = len(db_positions)
        snap.gross_exposure_usd = sum(
            abs(p.qty * p.current_price) for p in db_positions
        )
        snap.net_exposure_usd = sum(
            (p.qty if p.side == "LONG" else -p.qty) * p.current_price
            for p in db_positions
        )

    # ── Pending HITL tickets ────────────────────────────────────────────
    if approval_service is not None:
        snap.pending_hitl_tickets = len(approval_service.get_pending())
    else:
        snap.pending_hitl_tickets = _count_pending_approvals(session)

    # ── Proposal activity from DB ───────────────────────────────────────
    now = datetime.now(UTC)
    from datetime import timedelta
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)

    def _count_proposals(since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(TradeProposalModel)
            .where(TradeProposalModel.created_at >= since)
        )
        return session.execute(stmt).scalar_one() or 0

    def _count_proposals_by_status(status: str, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(TradeProposalModel)
            .where(
                TradeProposalModel.created_at >= since,
                TradeProposalModel.status == status,
            )
        )
        return session.execute(stmt).scalar_one() or 0

    snap.proposals_last_1h = _count_proposals(cutoff_1h)
    snap.proposals_last_24h = _count_proposals(cutoff_24h)
    snap.approved_proposals_last_24h = _count_proposals_by_status("approved", cutoff_24h)
    snap.rejected_proposals_last_24h = _count_proposals_by_status("rejected", cutoff_24h)

    # ── Phase 6 operational fields ──────────────────────────────────────
    snap.live_enabled = live_enabled
    snap.halt_active = halt.is_halted if halt is not None else kill_switch.is_halted
    snap.halt_scope = halt.scope.value if (halt is not None and halt.scope is not None) else None

    if incident_store is not None:
        snap.open_incidents_total = len(incident_store.open_incidents())
        snap.critical_incidents_total = incident_store.critical_open_count()
    else:
        (
            snap.open_incidents_total,
            snap.critical_incidents_total,
        ) = _db_incident_counts(session)

    db_unreconciled, db_recon_age, db_mismatches = _db_reconciliation_state(session, now)
    snap.unreconciled_orders = (
        db_unreconciled if unreconciled_orders is None else unreconciled_orders
    )
    snap.last_reconciliation_age_seconds = (
        db_recon_age if last_reconciliation_age_seconds is None else last_reconciliation_age_seconds
    )
    snap.reconciliation_mismatches = (
        db_mismatches if reconciliation_mismatches is None else reconciliation_mismatches
    )

    # ── External API / transaction health ───────────────────────────────
    try:
        snap.api_provider_states = _db_api_provider_states(session, now)
        (
            snap.api_failures_24h,
            snap.order_failures_24h,
            snap.operational_failures_24h,
        ) = _db_failure_counts(session, cutoff_24h)
    except Exception as exc:  # noqa: BLE001 - older DBs may not have the tables yet
        logger.warning(
            "MetricsSnapshot: operational health unavailable.",
            extra={"error": str(exc)},
        )

    return snap


# ── Prometheus exposition formatter ────────────────────────────────────────

_HELP: dict[str, str] = {
    "axel_equity_usd": "Total account equity in USD",
    "axel_cash_usd": "Available cash in USD",
    "axel_drawdown_pct": "Current peak-to-trough drawdown (0–1)",
    "axel_high_water_mark_usd": "All-time equity high-water mark in USD",
    "axel_gross_exposure_usd": "Sum of absolute position market values in USD",
    "axel_net_exposure_usd": "Long market value minus short market value in USD",
    "axel_open_positions_total": "Number of currently open positions",
    "axel_kill_switch_active": "1 if the global kill switch is tripped, 0 otherwise",
    "axel_pending_hitl_tickets": "Number of proposals awaiting human approval",
    "axel_proposals_total": "Number of trade proposals created",
    "axel_proposals_approved_total": "Number of approved proposals",
    "axel_proposals_rejected_total": "Number of rejected proposals",
    "axel_live_enabled": "1 if live trading is currently activated, 0 otherwise",
    "axel_halt_active": "1 if any halt scope is active, 0 otherwise",
    "axel_halt_scope": "Active halt scope as a label (global/section/new_risk_only)",
    "axel_open_incidents_total": "Number of currently open incidents",
    "axel_critical_incidents_total": "Number of open critical (risk-blocking) incidents",
    "axel_unreconciled_orders_total": "Number of orders awaiting reconciliation",
    "axel_last_reconciliation_age_seconds": "Age of the most recent reconciliation snapshot",
    "axel_reconciliation_mismatches_total": "Mismatches found in the last reconciliation run",
    "axel_api_provider_healthy": "1 if the provider has no consecutive failures and no quota exhaustion",
    "axel_api_quota_exhausted": "1 if the provider reported exhausted quota/credit",
    "axel_api_consecutive_failures": "Consecutive failed calls for the provider",
    "axel_api_last_success_age_seconds": "Seconds since the provider last answered successfully",
    "axel_api_rate_limit_remaining": "Provider-reported remaining request budget",
    "axel_api_retry_after_seconds": "Provider-requested retry delay after a rate limit",
    "axel_api_last_status_code": "HTTP status of the provider's most recent call",
    "axel_api_failures_total": "Failed API calls by provider and outcome",
    "axel_order_failures_total": "Failed order transitions by reason",
    "axel_operational_failures_total": "Operational failures by category, event and outcome",
    "axel_llm_tokens_total": "LLM tokens consumed by kind",
    "axel_llm_cost_usd_total": "Estimated LLM spend in USD",
}


def format_prometheus(snapshot: MetricsSnapshot) -> str:
    """
    Serialises a MetricsSnapshot to Prometheus text exposition format.

    HELP and TYPE lines are emitted once per metric family. Emitting them per
    sample (as an earlier version did for the two ``axel_proposals_total``
    windows) makes Prometheus reject the whole scrape with a duplicate-HELP
    error, so samples are grouped by family before rendering.
    """
    ts_ms = int(snapshot.collected_at.timestamp() * 1000)
    families: dict[str, tuple[str, list[tuple[dict[str, str] | None, float]]]] = {}

    def _add(
        name: str,
        value: float,
        metric_type: str = "gauge",
        labels: dict[str, str] | None = None,
    ) -> None:
        family = families.setdefault(name, (metric_type, []))
        family[1].append((labels, value))

    def _gauge(name: str, value: float, labels: dict[str, str] | None = None) -> None:
        _add(name, value, "gauge", labels)

    def _counter(name: str, value: float, labels: dict[str, str] | None = None) -> None:
        _add(name, value, "counter", labels)

    _gauge("axel_equity_usd", snapshot.total_equity_usd)
    _gauge("axel_cash_usd", snapshot.cash_usd)
    _gauge("axel_drawdown_pct", round(snapshot.drawdown_pct, 6))
    _gauge("axel_high_water_mark_usd", snapshot.high_water_mark_usd)
    _gauge("axel_gross_exposure_usd", snapshot.gross_exposure_usd)
    _gauge("axel_net_exposure_usd", snapshot.net_exposure_usd)
    _gauge("axel_open_positions_total", snapshot.open_positions_count)
    _gauge("axel_kill_switch_active", int(snapshot.kill_switch_active))
    _gauge("axel_pending_hitl_tickets", snapshot.pending_hitl_tickets)
    _counter("axel_proposals_total", snapshot.proposals_last_24h, {"window": "24h"})
    _counter("axel_proposals_total", snapshot.proposals_last_1h, {"window": "1h"})
    _counter("axel_proposals_approved_total", snapshot.approved_proposals_last_24h, {"window": "24h"})
    _counter("axel_proposals_rejected_total", snapshot.rejected_proposals_last_24h, {"window": "24h"})
    _gauge("axel_live_enabled", int(snapshot.live_enabled))
    _gauge("axel_halt_active", int(snapshot.halt_active))
    if snapshot.halt_scope is not None:
        _gauge("axel_halt_scope", 1, {"scope": snapshot.halt_scope})
    _gauge("axel_open_incidents_total", snapshot.open_incidents_total)
    _gauge("axel_critical_incidents_total", snapshot.critical_incidents_total)
    _gauge("axel_unreconciled_orders_total", snapshot.unreconciled_orders)
    if snapshot.last_reconciliation_age_seconds is not None:
        _gauge("axel_last_reconciliation_age_seconds", snapshot.last_reconciliation_age_seconds)
    _gauge("axel_reconciliation_mismatches_total", snapshot.reconciliation_mismatches)

    # ── External API / transaction health ───────────────────────────────
    for provider, state in sorted(snapshot.api_provider_states.items()):
        labels = {"provider": provider}
        healthy = (
            1
            if state.get("consecutive_failures", 0) == 0 and not state.get("quota_exhausted")
            else 0
        )
        _gauge("axel_api_provider_healthy", healthy, labels)
        _gauge("axel_api_quota_exhausted", int(bool(state.get("quota_exhausted"))), labels)
        _gauge("axel_api_consecutive_failures", state.get("consecutive_failures", 0), labels)
        if state.get("last_success_age_seconds") is not None:
            _gauge(
                "axel_api_last_success_age_seconds",
                state["last_success_age_seconds"],
                labels,
            )
        if state.get("rate_limit_remaining") is not None:
            _gauge("axel_api_rate_limit_remaining", state["rate_limit_remaining"], labels)
        if state.get("retry_after_seconds") is not None:
            _gauge("axel_api_retry_after_seconds", state["retry_after_seconds"], labels)
        if state.get("last_status_code") is not None:
            _gauge("axel_api_last_status_code", state["last_status_code"], labels)

        prompt_tokens = state.get("prompt_tokens_total", 0)
        completion_tokens = state.get("completion_tokens_total", 0)
        cost = state.get("estimated_cost_usd_total", 0.0)
        if prompt_tokens or completion_tokens or cost:
            _counter("axel_llm_tokens_total", prompt_tokens, {**labels, "kind": "prompt"})
            _counter(
                "axel_llm_tokens_total",
                completion_tokens,
                {**labels, "kind": "completion"},
            )
            _counter("axel_llm_cost_usd_total", cost, labels)

    for (provider, outcome), count in sorted(snapshot.api_failures_24h.items()):
        _counter(
            "axel_api_failures_total",
            count,
            {"provider": provider, "outcome": outcome, "window": "24h"},
        )
    for reason, count in sorted(snapshot.order_failures_24h.items()):
        _counter(
            "axel_order_failures_total",
            count,
            {"reason": reason, "window": "24h"},
        )
    for (category, event, outcome), count in sorted(snapshot.operational_failures_24h.items()):
        _counter(
            "axel_operational_failures_total",
            count,
            {
                "category": category,
                "event": event,
                "outcome": outcome,
                "window": "24h",
            },
        )

    lines: list[str] = []
    for name, (metric_type, samples) in families.items():
        lines.append(f"# HELP {name} {_HELP.get(name, name)}")
        lines.append(f"# TYPE {name} {metric_type}")
        for labels, value in samples:
            label_str = ""
            if labels:
                rendered = ",".join(f'{key}="{val}"' for key, val in labels.items())
                label_str = "{" + rendered + "}"
            lines.append(f"{name}{label_str} {value} {ts_ms}")

    lines.append("")  # trailing newline required by the spec
    return "\n".join(lines)
