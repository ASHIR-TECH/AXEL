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

from sqlalchemy.orm import Session

from axel.db.models import PositionModel, TradeProposalModel
from axel.execution.approval import ApprovalService
from axel.execution.broker_base import BrokerAdapter
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


# ── Collection ──────────────────────────────────────────────────────────────

def collect_metrics(
    session: Session,
    broker: BrokerAdapter,
    kill_switch: KillSwitch,
    approval_service: ApprovalService,
) -> MetricsSnapshot:
    """
    Aggregates live state from broker, kill switch, approval queue, and DB.
    Pure aggregation — no mutations.
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
    snap.pending_hitl_tickets = len(approval_service.get_pending())

    # ── Proposal activity from DB ───────────────────────────────────────
    now = datetime.now(UTC)
    from datetime import timedelta
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)

    from sqlalchemy import func, select

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
}


def format_prometheus(snapshot: MetricsSnapshot) -> str:
    """
    Serialises a MetricsSnapshot to Prometheus text exposition format.
    Each metric is prefixed with HELP and TYPE lines.
    """
    ts_ms = int(snapshot.collected_at.timestamp() * 1000)
    lines: list[str] = []

    def _gauge(name: str, value: float, labels: dict[str, str] | None = None) -> None:
        help_text = _HELP.get(name, name)
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        label_str = ""
        if labels:
            label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
        lines.append(f"{name}{label_str} {value} {ts_ms}")

    def _counter(name: str, value: float, labels: dict[str, str] | None = None) -> None:
        help_text = _HELP.get(name, name)
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        label_str = ""
        if labels:
            label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
        lines.append(f"{name}{label_str} {value} {ts_ms}")

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

    lines.append("")  # trailing newline required by the spec
    return "\n".join(lines)
