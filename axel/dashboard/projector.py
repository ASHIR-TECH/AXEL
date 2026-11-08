"""
Read-only projection layer for the live AXEL terminal.

The terminal was originally driven by ``axel/dashboard/ui/js/sim/``.  This
module produces the same payload shapes from durable database rows so the
browser can switch to a live server feed without any layout or component
changes.

Design rules
------------
- Every public function is SELECT-only.  No INSERT/UPDATE/DELETE path exists
  here; mutations belong to the runtime workers and the deterministic core.
- The projection is tolerant of an empty database: it returns a flat, honest
  baseline instead of inventing activity.
- All timestamps are normalised to UTC and serialised with a ``Z`` suffix so
  the browser can parse them consistently across SQLite and PostgreSQL.
- The UI event contract is intentionally preserved.  ``build_snapshot`` returns
  a full state; ``DashboardStreamState.poll`` returns the deltas the SSE
  endpoint should emit.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.db.models import (
    AgentRunModel,
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    IncidentModel,
    KillSwitchEventModel,
    NewsItem,
    OhlcvBar,
    OrderModel,
    PositionModel,
    RiskDecisionModel,
    SignalModel,
    TradeProposalModel,
)

# ── Terminal constants (mirror axel/dashboard/ui/js/config.js) ────────────────

INITIAL_EQUITY = 10_000.0

SECTION_STARTS: dict[str, float] = {
    "STOCKS": 4_500.0,
    "OPTIONS": 2_500.0,
    "CRYPTO": 1_500.0,
    "FOREX": 1_000.0,
    "PREDICTIONS": 500.0,
}
SECTION_ORDER: tuple[str, ...] = tuple(SECTION_STARTS)

ANALYSTS: tuple[str, ...] = (
    "TECHNICAL",
    "FUNDAMENTAL",
    "SENTIMENT",
    "MACRO/CARRY",
    "VOL/GREEKS",
    "ODDS/CALIB",
)
_ANALYST_ALIASES = {name: name for name in ANALYSTS}
_ANALYST_ALIASES.update(
    {
        "MACRO": "MACRO/CARRY",
        "CARRY": "MACRO/CARRY",
        "MACRO_CARRY": "MACRO/CARRY",
        "VOL": "VOL/GREEKS",
        "GREEKS": "VOL/GREEKS",
        "VOL_GREEKS": "VOL/GREEKS",
        "ODDS": "ODDS/CALIB",
        "CALIB": "ODDS/CALIB",
        "ODDS_CALIB": "ODDS/CALIB",
    }
)

_ORDER_STATE_MAP = {
    "pending_submit": "NEW",
    "created": "NEW",
    "approved": "NEW",
    "submitted": "SUBMITTED",
    "accepted": "SUBMITTED",
    "acknowledged": "SUBMITTED",
    "unknown": "SUBMITTED",
    "partially_filled": "PARTIAL",
    "partial": "PARTIAL",
    "filled": "FILLED",
    "canceled": "CANCELED",
    "cancelled": "CANCELED",
    "expired": "CANCELED",
    "rejected": "REJECTED",
    "error": "REJECTED",
}

_CHECK_DEFS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("kill", ("GLOBAL_KILL_SWITCH",), "drawdown vs hwm < 10%"),
    ("stop", ("SECTION_DAILY_STOP",), "section pnl day > -3%"),
    ("conf", ("PANEL_CONFIDENCE",), "panel confidence >= 0.30"),
    ("pos", ("CONCURRENT_POSITIONS", "MAX_CONCURRENT_POSITIONS"), "open positions < 10"),
    ("rr", ("RR_RATIO", "R_TARGET"), "reward:risk 1.5 - 4R"),
    ("corr", ("CORRELATION",), "max correlation < 0.85"),
    ("kelly", ("SIZING", "POSITION_SIZING"), "kelly f* > 0 and size > 0"),
)

# Human-readable hints used to attach a deterministic reason to a check code.
_CHECK_HINTS: dict[str, tuple[str, ...]] = {
    "kill": ("drawdown", "kill switch"),
    "stop": ("daily stop", "section"),
    "conf": ("confidence",),
    "pos": ("positions", "concurrent"),
    "rr": ("risk/reward", "reward/risk", "r_target", "reward"),
    "corr": ("correlation",),
    "kelly": ("sizing", "kelly", "quantity"),
}

_LOG_CHANNELS: tuple[tuple[str, str], ...] = (
    ("axel.risk", "RISK"),
    ("axel.execution", "EXEC"),
    ("axel.db", "DB"),
    ("axel.agents", "CHAT"),
    ("axel.data", "SYS"),
    ("axel.watchdog", "SYS"),
    ("axel.core", "SYS"),
    ("axel.main", "SYS"),
    ("axel.alerts", "SYS"),
)


# ── Small helpers ─────────────────────────────────────────────────────────────


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    value = _as_utc(value)
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _clock(value: datetime | None) -> str:
    value = _as_utc(value)
    return value.strftime("%H:%M:%S") if value else "--:--:--"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _section(value: Any) -> str:
    return str(value or "").upper()


def _is_long(side: Any) -> bool:
    return str(side or "").lower() in {"buy", "long", "l"}


def _db_datetime(session: Session, value: datetime) -> datetime:
    """Return a timezone-naive value for SQLite so comparisons are stable."""
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        return value.replace(tzinfo=None) if value.tzinfo else value
    return value


def _analyst(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if raw in _ANALYST_ALIASES:
        return _ANALYST_ALIASES[raw]
    normalised = raw.replace("-", "_").replace(" ", "_")
    return _ANALYST_ALIASES.get(normalised)


def _channel(logger: str) -> str:
    for prefix, channel in _LOG_CHANNELS:
        if logger.startswith(prefix):
            return channel
    return "SYS"


def _level(event_type: str, payload: dict[str, Any]) -> str:
    explicit = str(payload.get("level") or "").upper()
    if explicit:
        return explicit
    lowered = event_type.lower()
    if "kill" in lowered or "halt" in lowered or "breach" in lowered:
        return "CRITICAL"
    if "reject" in lowered or "veto" in lowered:
        return "VETO"
    if "fill" in lowered:
        return "FILLED"
    if "approve" in lowered:
        return "APPROVED"
    return "INFO"


def _log_line(row: AuditLogModel) -> dict[str, Any]:
    payload = row.payload if isinstance(row.payload, dict) else {}
    logger = str(payload.get("logger") or row.actor or "axel.core")
    message = payload.get("message") or payload.get("msg")
    if not message:
        message = f"{row.event_type} · {row.actor}"
    return {
        "id": row.id,
        "ts": _iso(row.timestamp) or "",
        "level": _level(row.event_type, payload),
        "logger": logger,
        "msg": str(message),
        "ch": str(payload.get("channel") or _channel(logger)),
    }


def _pearson(left: list[float], right: list[float]) -> float:
    """Pearson correlation of two equal-length series; 0.0 when undefined."""
    size = min(len(left), len(right))
    if size < 3:
        return 0.0
    left = left[-size:]
    right = right[-size:]
    mean_left = sum(left) / size
    mean_right = sum(right) / size
    cov = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    var_left = sum((a - mean_left) ** 2 for a in left)
    var_right = sum((b - mean_right) ** 2 for b in right)
    denominator = math.sqrt(var_left * var_right)
    if denominator <= 1e-18:
        return 0.0
    return max(-1.0, min(1.0, cov / denominator))


def _returns(values: list[float]) -> list[float]:
    return [(current / previous) - 1.0 for previous, current in pairwise(values) if previous]


# ── Equity and sections ───────────────────────────────────────────────────────


def _latest_snapshot(session: Session, section: str) -> EquitySnapshotModel | None:
    stmt = (
        select(EquitySnapshotModel)
        .where(func.upper(EquitySnapshotModel.section) == section.upper())
        .order_by(EquitySnapshotModel.timestamp.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _snapshot_series(session: Session, section: str, limit: int) -> list[EquitySnapshotModel]:
    stmt = (
        select(EquitySnapshotModel)
        .where(func.upper(EquitySnapshotModel.section) == section.upper())
        .order_by(EquitySnapshotModel.timestamp.desc())
        .limit(limit)
    )
    rows = list(session.execute(stmt).scalars())
    rows.reverse()
    return rows


def _aggregate_global_series(session: Session, limit: int) -> list[EquitySnapshotModel]:
    """Fallback: sum per-section snapshots by timestamp when no global rows exist."""
    stmt = select(EquitySnapshotModel).order_by(EquitySnapshotModel.timestamp.asc())
    rows = list(session.execute(stmt).scalars())
    if not rows:
        return []
    grouped: dict[datetime, list[EquitySnapshotModel]] = defaultdict(list)
    for row in rows:
        stamp = _as_utc(row.timestamp)
        if stamp is not None:
            grouped[stamp.replace(microsecond=0)].append(row)
    synthetic: list[EquitySnapshotModel] = []
    for stamp in sorted(grouped)[-limit:]:
        group = grouped[stamp]
        synthetic.append(
            EquitySnapshotModel(
                id=f"agg_{stamp.timestamp()}",
                timestamp=stamp,
                section="global",
                nav=sum(_f(row.nav) for row in group),
                cash=sum(_f(row.cash) for row in group),
                gross_exposure=sum(_f(row.gross_exposure) for row in group),
                net_exposure=sum(_f(row.net_exposure) for row in group),
                realized_pnl=sum(_f(row.realized_pnl) for row in group),
                unrealized_pnl=sum(_f(row.unrealized_pnl) for row in group),
                drawdown_pct=max((_f(row.drawdown_pct) for row in group), default=0.0),
                high_water_mark=max((_f(row.high_water_mark) for row in group), default=0.0),
            )
        )
    return synthetic


def _closed_trades(session: Session) -> list[dict[str, Any]]:
    stmt = select(AuditLogModel).where(AuditLogModel.event_type == "trade.closed")
    rows = session.execute(stmt).scalars().all()
    trades: list[dict[str, Any]] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        trades.append(payload)
    return trades


def _realized_pnl(session: Session, section: str | None = None) -> float:
    total = 0.0
    for payload in _closed_trades(session):
        if section is None or _section(payload.get("section")) == section.upper():
            total += _f(payload.get("pnl"))
    return total


def _day_start_nav(session: Session, section: str) -> float | None:
    now = datetime.now(UTC)
    day_start = _db_datetime(session, now.replace(hour=0, minute=0, second=0, microsecond=0))
    stmt = (
        select(EquitySnapshotModel)
        .where(
            func.upper(EquitySnapshotModel.section) == section.upper(),
            EquitySnapshotModel.timestamp >= day_start,
        )
        .order_by(EquitySnapshotModel.timestamp.asc())
        .limit(1)
    )
    row = session.execute(stmt).scalar_one_or_none()
    return _f(row.nav) if row else None


def _section_nav_series(session: Session, section: str, limit: int = 40) -> list[float]:
    rows = _snapshot_series(session, section, limit)
    return [_f(row.nav) for row in rows]


def _section_correlation(session: Session, section: str) -> float:
    target = _returns(_section_nav_series(session, section))
    if len(target) < 4:
        return 0.0
    correlations: list[float] = []
    for other in SECTION_ORDER:
        if other == section:
            continue
        other_returns = _returns(_section_nav_series(session, other))
        if len(other_returns) >= 4:
            correlations.append(abs(_pearson(target, other_returns)))
    return round(max(correlations), 3) if correlations else 0.0


def _position_count(session: Session, section: str) -> int:
    stmt = select(func.count()).select_from(PositionModel).where(
        func.upper(PositionModel.section) == section.upper(),
        PositionModel.qty > 0,
    )
    return int(session.execute(stmt).scalar_one())


def _equity_projection(session: Session) -> dict[str, Any]:
    rows = _snapshot_series(session, "global", limit=180)
    if not rows:
        rows = _aggregate_global_series(session, limit=180)
    if not rows:
        return {
            "equity": INITIAL_EQUITY,
            "hwm": INITIAL_EQUITY,
            "drawdown_pct": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "series": [INITIAL_EQUITY, INITIAL_EQUITY],
            "points": [],
        }

    navs = [_f(row.nav) for row in rows]
    latest = rows[-1]
    equity = navs[-1]
    stored_hwm = _f(latest.high_water_mark)
    hwm = max([INITIAL_EQUITY, stored_hwm, *navs])
    drawdown = (hwm - equity) / hwm if hwm > 0 else 0.0
    realized = _realized_pnl(session)
    if realized == 0.0:
        realized = _f(latest.realized_pnl)
    unrealized = _f(latest.unrealized_pnl)
    return {
        "equity": equity,
        "hwm": hwm,
        "drawdown_pct": drawdown,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "series": navs,
        "points": [{"ts": _iso(row.timestamp), "nav": _f(row.nav)} for row in rows],
    }


def _sections_projection(session: Session, equity: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    for name in SECTION_ORDER:
        start = SECTION_STARTS[name]
        latest = _latest_snapshot(session, name)
        nav = _f(latest.nav, start) if latest else start
        day_start = _day_start_nav(session, name) or start
        day_pnl = (nav / day_start - 1.0) if day_start > 0 else 0.0
        hist = _section_nav_series(session, name, 40)
        if len(hist) < 2:
            hist = [start, nav]
        sections[name] = {
            "nav": nav,
            "start": start,
            "dayPnl": day_pnl,
            "positions": _position_count(session, name),
            "corr": _section_correlation(session, name),
            "hist": hist,
            "blocked": day_pnl <= -settings.section_daily_stop_pct,
        }
    return sections


# ── Portfolio ─────────────────────────────────────────────────────────────────


def _positions(session: Session) -> list[dict[str, Any]]:
    stmt = select(PositionModel).order_by(PositionModel.created_at.asc())
    rows = session.execute(stmt).scalars().all()
    holdings: list[dict[str, Any]] = []
    for row in rows:
        qty = _f(row.qty)
        entry = _f(row.entry_price)
        last = _f(row.current_price, entry)
        sign = 1.0 if _is_long(row.side) else -1.0
        holdings.append(
            {
                "id": row.id,
                "sym": row.symbol,
                "sec": _section(row.section),
                "side": "L" if sign > 0 else "S",
                "qty": qty,
                "entry": entry,
                "last": last,
                "pnl": (last - entry) * qty * sign,
            }
        )
    return holdings


def _portfolio_projection(
    session: Session, equity: dict[str, Any], sections: dict[str, Any]
) -> dict[str, Any]:
    holdings = _positions(session)
    trades = _closed_trades(session)
    wins = sum(1 for trade in trades if _f(trade.get("pnl")) > 0.0)
    realized = sum(_f(trade.get("pnl")) for trade in trades)
    if realized == 0.0:
        realized = _f(equity.get("realized_pnl"))
    unrealized = sum(holding["pnl"] for holding in holdings)
    return {
        "equity": _f(equity.get("equity"), INITIAL_EQUITY),
        "realized": realized,
        "unrealized": unrealized,
        "trades": len(trades),
        "wins": wins,
        "holdings": holdings,
    }


# ── Orders ────────────────────────────────────────────────────────────────────


def _orders_projection(session: Session, limit: int = 14) -> list[dict[str, Any]]:
    stmt = (
        select(OrderModel, TradeProposalModel.section)
        .outerjoin(TradeProposalModel, TradeProposalModel.id == OrderModel.proposal_id)
        .order_by(OrderModel.created_at.desc())
        .limit(limit)
    )
    orders: list[dict[str, Any]] = []
    for order, section in session.execute(stmt).all():
        state = _ORDER_STATE_MAP.get(str(order.state or "").lower(), "SUBMITTED")
        created = _as_utc(order.created_at)
        orders.append(
            {
                "cid": order.client_order_id,
                "sym": order.symbol,
                "side": "BUY" if _is_long(order.side) else "SELL",
                "sec": _section(section) or "STOCKS",
                "qty": _f(order.qty),
                "filled": _f(order.filled_qty),
                "entry": _f(order.limit_price, _f(order.filled_avg_price)),
                "state": state,
                "at": int(created.timestamp() * 1000) if created else 0,
            }
        )
    return orders


# ── Signals ───────────────────────────────────────────────────────────────────


def _signals_projection(session: Session) -> dict[str, Any]:
    since = _db_datetime(session, datetime.now(UTC) - timedelta(hours=6))
    stmt = (
        select(SignalModel)
        .where(SignalModel.ts >= since)
        .order_by(SignalModel.ts.asc())
    )
    latest: dict[tuple[str, str], SignalModel] = {}
    for row in session.execute(stmt).scalars():
        analyst = _analyst(row.analyst)
        if analyst is None:
            continue
        latest[(analyst, _section(row.section))] = row

    signals: dict[str, Any] = {}
    for (analyst, section), row in latest.items():
        direction = str(row.direction or "").upper()
        if direction.startswith("L"):
            marker = "L"
        elif direction.startswith("S"):
            marker = "S"
        else:
            marker = "N"
        strength = max(0.0, min(1.0, _f(row.strength)))
        dots = 1 + min(2, int(strength * 2.999))
        signals[f"{analyst}|{section}"] = {
            "d": marker,
            "s": dots,
            "conf": round(strength, 4),
        }
    return signals


# ── Proposals and the risk gate ───────────────────────────────────────────────


def _rr_ratio(proposal: TradeProposalModel) -> float:
    entry = _f(proposal.entry_price)
    stop = _f(proposal.stop_loss)
    target = _f(proposal.take_profit)
    if _is_long(proposal.side):
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target
    return reward / risk if risk > 0 else 0.0


def _horizon(proposal: TradeProposalModel) -> str:
    created = _as_utc(proposal.created_at)
    expires = _as_utc(proposal.ttl_expires_at)
    if created is None or expires is None:
        return "1D"
    seconds = max(0.0, (expires - created).total_seconds())
    if seconds <= 600:
        return "5M"
    if seconds <= 3_600:
        return "1H"
    if seconds <= 86_400:
        return "1D"
    return "1W"


def _risk_checks(decision: RiskDecisionModel | None) -> list[dict[str, Any]]:
    if decision is None:
        return [
            {"k": key, "pass": True, "why": default, "pending": True}
            for key, _prefixes, default in _CHECK_DEFS
        ]
    failed = [str(item) for item in (decision.checks_failed or [])]
    passed = [str(item) for item in (decision.checks_passed or [])]
    reasons = [str(item) for item in (decision.reasons or [])]
    checks: list[dict[str, Any]] = []
    for key, prefixes, default in _CHECK_DEFS:
        failed_index = next(
            (
                index
                for index, item in enumerate(failed)
                if any(prefix in item for prefix in prefixes)
            ),
            None,
        )
        passed_match = next(
            (item for item in passed if any(prefix in item for prefix in prefixes)), None
        )
        if failed_index is not None:
            hints = _CHECK_HINTS.get(key, ())
            reason = next(
                (
                    item
                    for item in reasons
                    if any(hint in item.lower() for hint in hints)
                ),
                None,
            )
            if reason is None and failed_index < len(reasons):
                reason = reasons[failed_index]
            checks.append({"k": key, "pass": False, "why": reason or default})
        else:
            checks.append({"k": key, "pass": True, "why": passed_match or default})
    return checks


def _proposal_payload(
    proposal: TradeProposalModel, decision: RiskDecisionModel | None
) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "section": _section(proposal.section),
        "symbol": proposal.symbol,
        "side": "LONG" if _is_long(proposal.side) else "SHORT",
        "entry": _f(proposal.entry_price),
        "stop": _f(proposal.stop_loss),
        "target": _f(proposal.take_profit),
        "rr": round(_rr_ratio(proposal), 4),
        "conf": _f(proposal.confidence_score),
        "horizon": _horizon(proposal),
        "strategy": proposal.strategy_id,
        "corr": 0.0,
        "at": _iso(proposal.created_at),
        "status": proposal.status,
        "verdict": str(decision.verdict).upper() if decision is not None else "PENDING",
        "checks": _risk_checks(decision),
        "failed": list(decision.checks_failed or []) if decision is not None else [],
    }


def _latest_risk(session: Session) -> dict[str, Any] | None:
    stmt = (
        select(TradeProposalModel, RiskDecisionModel)
        .outerjoin(RiskDecisionModel, RiskDecisionModel.proposal_id == TradeProposalModel.id)
        .order_by(TradeProposalModel.created_at.desc())
        .limit(1)
    )
    row = session.execute(stmt).first()
    if row is None:
        return None
    proposal, decision = row
    payload = _proposal_payload(proposal, decision)
    return {
        "proposal": payload,
        "checks": payload["checks"],
        "verdict": payload["verdict"],
        "failed": payload["failed"],
    }


def _recent_proposals(session: Session, limit: int = 20) -> list[dict[str, Any]]:
    stmt = (
        select(TradeProposalModel, RiskDecisionModel)
        .outerjoin(RiskDecisionModel, RiskDecisionModel.proposal_id == TradeProposalModel.id)
        .order_by(TradeProposalModel.created_at.desc())
        .limit(limit)
    )
    return [_proposal_payload(proposal, decision) for proposal, decision in session.execute(stmt)]


# ── Alerts, ops, quotes, logs ─────────────────────────────────────────────────


def _alerts_projection(session: Session, limit: int = 7) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    incident_stmt = (
        select(IncidentModel)
        .where(IncidentModel.status == "open")
        .order_by(IncidentModel.last_seen_at.desc())
        .limit(limit)
    )
    for incident in session.execute(incident_stmt).scalars():
        severity = str(incident.severity or "warning").lower()
        level = "CRITICAL" if severity in {"critical", "error"} else "WARN"
        alerts.append(
            {
                "level": level,
                "text": incident.summary,
                "at": _clock(incident.last_seen_at),
            }
        )

    kill_stmt = (
        select(KillSwitchEventModel)
        .where(KillSwitchEventModel.is_active.is_(True))
        .order_by(KillSwitchEventModel.timestamp.desc())
        .limit(limit)
    )
    for event in session.execute(kill_stmt).scalars():
        alerts.append(
            {
                "level": "CRITICAL",
                "text": f"KILL-SWITCH ACTIVE — {event.reason}",
                "at": _clock(event.timestamp),
            }
        )

    alert_stmt = (
        select(AuditLogModel)
        .where(AuditLogModel.event_type == "alert")
        .order_by(AuditLogModel.timestamp.desc())
        .limit(limit)
    )
    for row in session.execute(alert_stmt).scalars():
        payload = row.payload if isinstance(row.payload, dict) else {}
        alerts.append(
            {
                "level": str(payload.get("level") or "WARN").upper(),
                "text": str(payload.get("message") or payload.get("text") or "alert"),
                "at": _clock(row.timestamp),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for alert in alerts:
        key = alert["text"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(alert)
    return deduped[:limit]


def _lag_seconds(value: datetime | None, now: datetime) -> float:
    stamp = _as_utc(value)
    if stamp is None:
        return 9_999.0
    return max(0.0, (now - stamp).total_seconds())


def _latest_bar_time(session: Session, sections: tuple[str, ...]) -> datetime | None:
    symbols: list[str] = []
    for section in sections:
        symbols.extend(_SECTION_SYMBOLS.get(section, ()))
    if symbols:
        stmt = select(func.max(OhlcvBar.ingestion_time)).where(OhlcvBar.symbol.in_(symbols))
        value = session.execute(stmt).scalar_one_or_none()
        if value is not None:
            return value
    stmt = select(func.max(OhlcvBar.ingestion_time))
    return session.execute(stmt).scalar_one_or_none()


def _latest_news_time(session: Session, sources: tuple[str, ...]) -> datetime | None:
    conditions = [
        func.lower(NewsItem.source).like(f"%{source.lower()}%") for source in sources
    ]
    stmt = select(func.max(NewsItem.ingested_at)).where(or_(*conditions))
    return session.execute(stmt).scalar_one_or_none()


def _ops_projection(session: Session) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = _db_datetime(session, now - timedelta(days=30))
    spend_stmt = select(func.coalesce(func.sum(AgentRunModel.cost_usd), 0.0)).where(
        AgentRunModel.timestamp >= since
    )
    spend = _f(session.execute(spend_stmt).scalar_one())
    budget = float(getattr(settings, "llm_monthly_budget_usd", 50.0))
    return {
        "llmSpend": spend,
        "llmBudget": budget,
        "ingest": [
            {
                "src": "ALPACA · bars",
                "lag": _lag_seconds(_latest_bar_time(session, ("STOCKS", "OPTIONS")), now),
            },
            {
                "src": "FRED · macro",
                "lag": _lag_seconds(_latest_news_time(session, ("fred",)), now),
            },
            {
                "src": "EDGAR · filings",
                "lag": _lag_seconds(_latest_news_time(session, ("sec", "edgar")), now),
            },
            {
                "src": "NEWS · gdelt",
                "lag": _lag_seconds(_latest_news_time(session, ("gdelt", "news")), now),
            },
            {
                "src": "KALSHI · odds",
                "lag": _lag_seconds(_latest_bar_time(session, ("PREDICTIONS",)), now),
            },
        ],
    }


def _quotes_projection(session: Session, limit_per_symbol: int = 2) -> dict[str, Any]:
    since = _db_datetime(session, datetime.now(UTC) - timedelta(days=7))
    stmt = (
        select(OhlcvBar)
        .where(OhlcvBar.timestamp >= since)
        .order_by(OhlcvBar.timestamp.desc())
        .limit(8_000)
    )
    closes: dict[str, list[float]] = defaultdict(list)
    for bar in session.execute(stmt).scalars():
        bucket = closes[bar.symbol]
        if len(bucket) < limit_per_symbol:
            bucket.append(_f(bar.close))
    quotes: dict[str, Any] = {}
    for symbol, values in closes.items():
        if not values:
            continue
        price = values[0]
        previous = values[1] if len(values) > 1 else price
        change = ((price / previous) - 1.0) * 100.0 if previous else 0.0
        quotes[symbol] = {
            "price": price,
            "chg": round(change, 3),
            "sec": _symbol_section(symbol),
        }
    return dict(sorted(quotes.items())[:24])


def _logs_projection(session: Session, limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(AuditLogModel).order_by(AuditLogModel.timestamp.desc()).limit(limit)
    rows = list(session.execute(stmt).scalars())
    rows.reverse()
    return [_log_line(row) for row in rows]


def _halt_projection(session: Session) -> tuple[bool, list[dict[str, Any]]]:
    stmt = (
        select(KillSwitchEventModel)
        .order_by(KillSwitchEventModel.timestamp.desc())
        .limit(4)
    )
    rows = list(session.execute(stmt).scalars())
    halted = bool(rows and rows[0].is_active)
    return halted, [{"at": _iso(row.timestamp), "reason": row.reason} for row in rows]


def _counters_projection(session: Session) -> dict[str, int]:
    proposals = int(
        session.execute(select(func.count()).select_from(TradeProposalModel)).scalar_one()
    )
    evaluated = int(
        session.execute(select(func.count()).select_from(RiskDecisionModel)).scalar_one()
    )
    signals = int(session.execute(select(func.count()).select_from(SignalModel)).scalar_one())
    orders = int(session.execute(select(func.count()).select_from(OrderModel)).scalar_one())
    fills = int(session.execute(select(func.count()).select_from(FillModel)).scalar_one())
    return {
        "proposals": proposals,
        "proposals_evaluated": evaluated,
        "signals": signals,
        "orders": orders,
        "fills": fills,
    }


def _started_at(session: Session) -> datetime | None:
    stmt = (
        select(AuditLogModel.timestamp)
        .where(AuditLogModel.event_type == "runtime.started")
        .order_by(AuditLogModel.timestamp.asc())
        .limit(1)
    )
    value = session.execute(stmt).scalar_one_or_none()
    if value is not None:
        return value
    stmt = select(func.min(EquitySnapshotModel.timestamp))
    return session.execute(stmt).scalar_one_or_none()


# ── Symbol universe (mirrors the terminal config) ─────────────────────────────

_SECTION_SYMBOLS: dict[str, tuple[str, ...]] = {
    "STOCKS": (
        "AAPL",
        "NVDA",
        "MSFT",
        "TSLA",
        "XOM",
        "JPM",
        "META",
        "AVGO",
        "LLY",
        "CAT",
    ),
    "OPTIONS": (
        "SPY 26SEP550C",
        "QQQ 02OCT480P",
        "SPY 26SEP600C",
        "IWM 2200C",
        "NVDA 1200P",
    ),
    "CRYPTO": ("BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD"),
    "FOREX": ("EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD"),
    "PREDICTIONS": ("KAL:FED-CUT", "KAL:NBA-BOS", "KAL:BTC>150K", "KAL:CPI-OCT", "KAL:SFR-DEC"),
}

_SYMBOL_SECTION: dict[str, str] = {
    symbol: section
    for section, symbols in _SECTION_SYMBOLS.items()
    for symbol in symbols
}


def _symbol_section(symbol: str) -> str:
    if symbol in _SYMBOL_SECTION:
        return _SYMBOL_SECTION[symbol]
    upper = symbol.upper()
    if upper.startswith(("KAL:", "KALSHI")):
        return "PREDICTIONS"
    if upper.endswith("-USD"):
        return "CRYPTO"
    if len(upper) == 6 and upper.isalpha():
        return "FOREX"
    if " " in upper:
        return "OPTIONS"
    return "STOCKS"


# ── Public API ────────────────────────────────────────────────────────────────


def build_snapshot(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Build the complete live-dashboard state from durable records."""
    stamp = _as_utc(now) or datetime.now(UTC)
    equity = _equity_projection(session)
    sections = _sections_projection(session, equity)
    portfolio = _portfolio_projection(session, equity, sections)
    halted, kill_events = _halt_projection(session)
    started = _started_at(session)
    return {
        "mode": settings.environment,
        "source": "server",
        "generated_at": _iso(stamp),
        "started_at": _iso(started),
        "halted": halted,
        "equity": equity,
        "sections": sections,
        "portfolio": portfolio,
        "orders": _orders_projection(session),
        "signals": _signals_projection(session),
        "proposals": _recent_proposals(session),
        "latest_risk": _latest_risk(session),
        "alerts": _alerts_projection(session),
        "ops": _ops_projection(session),
        "quotes": _quotes_projection(session),
        "kill_events": kill_events,
        "logs": _logs_projection(session),
        "counters": _counters_projection(session),
    }


class DashboardStreamState:
    """Track one SSE client's cursor and produce incremental UI events.

    The database is the single source of truth.  Each poll rebuilds the
    snapshot and diffs it against the previous one; no cross-process event bus
    is required, so the API can run in a different container from the workers.
    """

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.log_ids = {line["id"] for line in snapshot.get("logs", [])}
        self.polls = 0

    def poll(self, snapshot: dict[str, Any]) -> list[tuple[str, Any]]:
        previous = self.snapshot
        events: list[tuple[str, Any]] = []

        # New audit-log lines (dedupe by durable id).
        for line in snapshot.get("logs", []):
            if line["id"] not in self.log_ids:
                self.log_ids.add(line["id"])
                events.append(("log", line))
        if len(self.log_ids) > 2_000:
            self.log_ids = {line["id"] for line in snapshot.get("logs", [])}

        # Risk-gate transitions.
        previous_risk = previous.get("latest_risk")
        current_risk = snapshot.get("latest_risk")
        if current_risk is not None:
            previous_id = (
                previous_risk["proposal"]["id"] if previous_risk is not None else None
            )
            current_id = current_risk["proposal"]["id"]
            if current_id != previous_id:
                events.append(("risk:start", current_risk["proposal"]))
            previous_verdict = (
                previous_risk.get("verdict") if previous_risk is not None else None
            )
            if current_id != previous_id or current_risk.get("verdict") != previous_verdict:
                events.append(
                    (
                        "risk:done",
                        {
                            "p": current_risk["proposal"],
                            "verdict": current_risk["verdict"],
                            "failed": current_risk["failed"],
                            "checks": current_risk["checks"],
                        },
                    )
                )

        # Domain changes.
        for key, event in (
            ("sections", "sections"),
            ("portfolio", "portfolio"),
            ("orders", "orders"),
            ("signals", "signals"),
            ("quotes", "quotes"),
            ("alerts", "alerts"),
            ("kill_events", "killEvents"),
        ):
            if snapshot.get(key) != previous.get(key):
                events.append((event, snapshot.get(key)))

        if snapshot.get("equity") != previous.get("equity"):
            equity = snapshot["equity"]
            events.append(
                (
                    "equity",
                    {
                        "equity": equity["equity"],
                        "hwm": equity["hwm"],
                        "dd": equity["drawdown_pct"],
                    },
                )
            )

        if snapshot.get("halted") != previous.get("halted"):
            events.append(("halt", snapshot.get("halted")))

        # Ops freshness is noisy; emit every fifth poll or when materially changed.
        self.polls += 1
        if self.polls % 5 == 0 or snapshot.get("ops") != previous.get("ops"):
            events.append(("ops", snapshot.get("ops")))

        self.snapshot = snapshot
        return events


__all__ = [
    "ANALYSTS",
    "INITIAL_EQUITY",
    "SECTION_ORDER",
    "SECTION_STARTS",
    "DashboardStreamState",
    "build_snapshot",
]
