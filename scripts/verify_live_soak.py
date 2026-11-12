"""Read-only verification of the live Alpaca paper soak.

Prints one JSON block with the evidence the 56-day run depends on: fresh real
bars, proposals, risk decisions, broker-backed orders and fills, realized
slippage, open positions and NAV. It never writes to the database and never
submits an order.

Exit codes: 0 = healthy, 1 = live path not producing fills yet, 2 = error.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from axel.db.models import (
    EquitySnapshotModel,
    FillModel,
    OhlcvBar,
    OrderModel,
    PositionModel,
    RiskDecisionModel,
    TradeProposalModel,
)
from axel.db.session import SessionLocal
from axel.runtime.execution import market_is_open

WINDOW = timedelta(minutes=30)


def _slippage_bps(session: Session, fill: FillModel) -> float | None:
    """Realized slippage of a fill against its proposal's reference entry."""
    order = session.get(OrderModel, fill.client_order_id)
    if order is None:
        return None
    proposal = session.get(TradeProposalModel, order.proposal_id)
    if proposal is None or not proposal.entry_price:
        return None
    reference = float(proposal.entry_price)
    if reference <= 0:
        return None
    sign = 1.0 if str(fill.side).lower().startswith("buy") else -1.0
    return (float(fill.price) - reference) / reference * 10_000.0 * sign


def _as_utc(stamp: datetime) -> datetime:
    """SQLite returns naive datetimes; treat those as UTC."""
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp


def _latest_bar(session: Session) -> OhlcvBar | None:
    return session.execute(
        select(OhlcvBar).order_by(desc(OhlcvBar.timestamp)).limit(1)
    ).scalar_one_or_none()


def snapshot(session: Session, *, now: datetime) -> dict:
    since = now - WINDOW
    latest_bar = _latest_bar(session)
    bar_gap = (
        (now - _as_utc(latest_bar.timestamp)).total_seconds()
        if latest_bar is not None and latest_bar.timestamp is not None
        else None
    )

    recent_fills = (
        session.execute(
            select(FillModel).where(FillModel.timestamp >= since).order_by(desc(FillModel.timestamp))
        )
        .scalars()
        .all()
    )
    live_orders = session.execute(
        select(func.count()).select_from(OrderModel).where(OrderModel.broker_order_id.is_not(None))
    ).scalar_one()
    nav_row = session.execute(
        select(EquitySnapshotModel)
        .where(EquitySnapshotModel.section == "global")
        .order_by(desc(EquitySnapshotModel.timestamp))
        .limit(1)
    ).scalar_one_or_none()

    slippages = [s for s in (_slippage_bps(session, f) for f in recent_fills) if s is not None]
    return {
        "now": now.isoformat(),
        "market_open": market_is_open(now),
        "latest_bar": latest_bar.timestamp.isoformat() if latest_bar else None,
        "latest_bar_symbol": latest_bar.symbol if latest_bar else None,
        "bar_age_seconds": round(bar_gap, 1) if bar_gap is not None else None,
        "bars_30m": session.execute(
            select(func.count()).select_from(OhlcvBar).where(OhlcvBar.timestamp >= since)
        ).scalar_one(),
        "proposals_30m": session.execute(
            select(func.count())
            .select_from(TradeProposalModel)
            .where(TradeProposalModel.created_at >= since)
        ).scalar_one(),
        "decisions_30m": session.execute(
            select(func.count())
            .select_from(RiskDecisionModel)
            .where(RiskDecisionModel.created_at >= since)
        ).scalar_one(),
        "fills_30m": len(recent_fills),
        "orders_with_broker_id": live_orders,
        "mean_realized_slippage_bps": round(sum(slippages) / len(slippages), 2) if slippages else None,
        "open_positions": session.execute(
            select(func.count()).select_from(PositionModel)
        ).scalar_one(),
        "nav": float(nav_row.nav) if nav_row is not None else None,
        "last_fill": (
            {
                "client_order_id": recent_fills[0].client_order_id,
                "symbol": recent_fills[0].symbol,
                "side": recent_fills[0].side,
                "qty": float(recent_fills[0].qty),
                "price": float(recent_fills[0].price),
            }
            if recent_fills
            else None
        ),
    }


def main() -> int:
    now = datetime.now(UTC)
    try:
        with SessionLocal() as session:
            data = snapshot(session, now=now)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": repr(exc)}, indent=2))
        return 2

    print(json.dumps(data, indent=2))

    if not data["market_open"]:
        print("market closed: fills cannot happen yet", file=sys.stderr)
        return 1
    if data["bar_age_seconds"] is None or data["bar_age_seconds"] > 900:
        print("no fresh bars: ingest is not producing live data", file=sys.stderr)
        return 1
    if data["fills_30m"] == 0:
        print("no fills in the last 30m: execution path not exercised", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
