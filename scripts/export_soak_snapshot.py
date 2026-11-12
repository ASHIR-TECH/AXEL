"""Export a small, public snapshot of the paper soak for a static dashboard.

Read-only: it never writes to the database and never submits an order. The
snapshot contains only paper-account numbers and trade records — no keys,
no account identifiers, no internal reasoning text.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    OrderModel,
    PositionModel,
    TradeProposalModel,
    WorkerHeartbeatModel,
)
from axel.db.session import SessionLocal

REQUIRED_WORKERS = ("ingest-worker", "agent-worker", "core-worker")
EQUITY_POINTS = 240
TRADE_LIMIT = 40
WINDOW_DAYS = 56


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value else None


def _soak_window(session: Session) -> tuple[datetime | None, datetime | None]:
    marker = session.execute(
        select(AuditLogModel)
        .where(AuditLogModel.event_type == "paper_soak.started")
        .order_by(AuditLogModel.timestamp.asc())
        .limit(1)
    ).scalar_one_or_none()
    if marker is None:
        return None, None
    payload = marker.payload if isinstance(marker.payload, dict) else {}
    end_raw = payload.get("window_end")
    end = None
    if isinstance(end_raw, str):
        try:
            end = datetime.fromisoformat(end_raw)
        except ValueError:
            end = None
    return marker.timestamp, end or marker.timestamp + timedelta(days=WINDOW_DAYS)


def _workers(session: Session, now: datetime) -> tuple[dict[str, dict], float]:
    rows = session.execute(select(WorkerHeartbeatModel)).scalars().all()
    workers: dict[str, dict] = {}
    coverage: list[float] = []
    for row in rows:
        first = _utc(row.first_seen)
        span = max(0.0, (now - first).total_seconds())
        covered = max(0.0, span - float(row.downtime_seconds or 0.0))
        ratio = covered / span if span > 0 else 0.0
        workers[row.worker] = {
            "cycles": int(row.cycles or 0),
            "gaps": int(row.gap_count or 0),
            "downtime_hours": round(float(row.downtime_seconds or 0.0) / 3600.0, 2),
            "covered_days": round(covered / 86_400.0, 2),
            "ratio": round(ratio, 4),
            "last_seen": _iso(row.last_seen),
        }
        if row.worker in REQUIRED_WORKERS:
            coverage.append(covered / 86_400.0)
    return workers, round(min(coverage), 2) if coverage else 0.0


def build_snapshot(session: Session, *, now: datetime | None = None) -> dict:
    stamp = now or datetime.now(UTC)
    started, window_end = _soak_window(session)
    days_elapsed = round((stamp - _utc(started)).total_seconds() / 86_400.0, 2) if started else 0.0
    days_remaining = (
        round((_utc(window_end) - stamp).total_seconds() / 86_400.0, 2) if window_end else WINDOW_DAYS
    )

    latest = session.execute(
        select(EquitySnapshotModel)
        .where(EquitySnapshotModel.section == "global")
        .order_by(desc(EquitySnapshotModel.timestamp))
        .limit(1)
    ).scalar_one_or_none()
    account = {
        "nav": round(_num(latest.nav if latest else 0.0), 2),
        "cash": round(_num(latest.cash if latest else 0.0), 2),
        "gross_exposure": round(_num(latest.gross_exposure if latest else 0.0), 2),
        "realized_pnl": round(_num(latest.realized_pnl if latest else 0.0), 2),
        "unrealized_pnl": round(_num(latest.unrealized_pnl if latest else 0.0), 2),
        "drawdown_pct": round(_num(latest.drawdown_pct if latest else 0.0) * 100.0, 3),
        "high_water_mark": round(_num(latest.high_water_mark if latest else 0.0), 2),
        "as_of": _iso(latest.timestamp if latest else None),
    }

    curve_stmt = select(EquitySnapshotModel).where(EquitySnapshotModel.section == "global")
    # Ignore snapshots written before the ledger was anchored to the broker's
    # cash, otherwise the curve jumps from the old synthetic size to the real
    # paper balance.
    start_equity = _num(settings.paper_starting_equity)
    if start_equity > 0:
        anchored = session.execute(
            select(EquitySnapshotModel.timestamp)
            .where(
                EquitySnapshotModel.section == "global",
                EquitySnapshotModel.nav >= start_equity * 0.5,
            )
            .order_by(EquitySnapshotModel.timestamp.asc())
            .limit(1)
        ).scalar_one_or_none()
        if anchored is not None:
            curve_stmt = curve_stmt.where(EquitySnapshotModel.timestamp >= _utc(anchored))
    curve_rows = (
        session.execute(
            curve_stmt.order_by(desc(EquitySnapshotModel.timestamp)).limit(EQUITY_POINTS)
        )
        .scalars()
        .all()
    )
    curve = [
        {"t": _iso(row.timestamp), "nav": round(_num(row.nav), 2)}
        for row in reversed(curve_rows)
    ]

    positions = [
        {
            "symbol": row.symbol,
            "section": str(row.section).upper(),
            "side": "LONG" if str(row.side).upper().startswith("L") else "SHORT",
            "qty": round(_num(row.qty), 6),
            "entry": round(_num(row.entry_price), 4),
            "price": round(_num(row.current_price), 4),
            "unrealized": round(_num(row.unrealized_pnl), 2),
            "opened_at": _iso(row.created_at),
        }
        for row in session.execute(
            select(PositionModel).where(PositionModel.qty > 0).order_by(desc(PositionModel.created_at))
        ).scalars()
    ]

    closed = session.execute(
        select(AuditLogModel).where(AuditLogModel.event_type == "trade.closed")
    ).scalars().all()
    trades = []
    for row in closed[-TRADE_LIMIT:][::-1]:
        payload = row.payload if isinstance(row.payload, dict) else {}
        trades.append(
            {
                "at": _iso(row.timestamp),
                "symbol": payload.get("symbol"),
                "section": payload.get("section"),
                "qty": _num(payload.get("qty")),
                "entry": _num(payload.get("entry")),
                "exit": _num(payload.get("exit")),
                "pnl": round(_num(payload.get("pnl")), 2),
                "win": bool(payload.get("win")),
                "reason": payload.get("reason"),
            }
        )

    fills = session.execute(
        select(FillModel).order_by(desc(FillModel.timestamp)).limit(500)
    ).scalars().all()
    slippages = [
        abs(_num(f.slippage)) / _num(f.price) * 10_000.0 for f in fills if _num(f.price) > 0
    ]
    total_pnl = sum(_num((row.payload or {}).get("pnl")) for row in closed)
    wins = sum(1 for row in closed if isinstance(row.payload, dict) and row.payload.get("win"))

    stats = {
        "proposals": session.execute(
            select(func.count()).select_from(TradeProposalModel)
        ).scalar_one(),
        "orders": session.execute(select(func.count()).select_from(OrderModel)).scalar_one(),
        "broker_orders": session.execute(
            select(func.count()).select_from(OrderModel).where(OrderModel.broker_order_id.is_not(None))
        ).scalar_one(),
        "fills": len(fills),
        "closed_trades": len(closed),
        "total_realized_pnl": round(total_pnl, 2),
        "win_rate": round(wins / len(closed), 4) if closed else None,
        "mean_slippage_bps": round(sum(slippages) / len(slippages), 2) if slippages else None,
    }

    workers, coverage_days = _workers(session, stamp)
    return {
        "meta": {
            "generated_at": _iso(stamp),
            "started_at": _iso(started),
            "window_end": _iso(window_end),
            "days_elapsed": days_elapsed,
            "days_remaining": max(0.0, days_remaining),
            "mode": settings.environment,
            "starting_equity": round(_num(settings.paper_starting_equity) or 10_000.0, 2),
            "feed": "alpaca-iex" if settings.paper_data_source == "alpaca" else "synthetic",
            "execution": settings.paper_execution,
            "sections": ["STOCKS"],
        },
        "account": account,
        "equity_curve": curve,
        "positions": positions,
        "trades": trades,
        "stats": stats,
        "workers": workers,
        "coverage_days": coverage_days,
    }


def write_snapshot(path: Path, *, now: datetime | None = None) -> dict:
    with SessionLocal() as session:
        data = build_snapshot(session, now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="snapshot.json")
    args = parser.parse_args()
    data = write_snapshot(Path(args.out))
    print(
        f"wrote {args.out}: nav={data['account']['nav']} "
        f"positions={len(data['positions'])} trades={data['stats']['closed_trades']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
