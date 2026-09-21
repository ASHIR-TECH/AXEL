"""
Broker-to-database reconciliation engine.
Detects position quantity drift, state discrepancies, and unrecorded broker orders.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from axel.core.contracts import Order
from axel.core.logging import logger
from axel.core.types import OrderState
from axel.execution.broker_base import BrokerAdapter, PositionInfo


@dataclass(frozen=True)
class PositionDrift:
    symbol: str
    db_qty: float
    broker_qty: float
    difference: float


@dataclass(frozen=True)
class ReconciliationReport:
    timestamp: str
    is_healthy: bool
    position_drifts: List[PositionDrift] = field(default_factory=list)
    ghost_broker_orders: List[str] = field(default_factory=list)
    unmatched_db_orders: List[str] = field(default_factory=list)
    details: List[str] = field(default_factory=list)


class ReconciliationEngine:
    """
    Periodic job that verifies synchronization between internal DB state and broker reality.
    """

    def __init__(self, broker: BrokerAdapter):
        self.broker = broker

    def reconcile_positions(
        self,
        db_positions: Dict[str, float],  # symbol -> qty
    ) -> List[PositionDrift]:
        """Compares DB position quantities against live broker positions."""
        broker_positions = {p.symbol: p.qty for p in self.broker.get_positions()}
        drifts: List[PositionDrift] = []

        all_symbols = set(db_positions.keys()).union(set(broker_positions.keys()))
        for sym in all_symbols:
            db_qty = db_positions.get(sym, 0.0)
            broker_qty = broker_positions.get(sym, 0.0)
            diff = abs(db_qty - broker_qty)
            if diff > 1e-4:
                drifts.append(
                    PositionDrift(
                        symbol=sym,
                        db_qty=db_qty,
                        broker_qty=broker_qty,
                        difference=round(diff, 4),
                    )
                )

        return drifts

    def reconcile_orders(
        self,
        active_db_orders: List[Order],
        broker_open_order_ids: List[str],
    ) -> ReconciliationReport:
        """Compares open order states between DB and broker."""
        db_client_ids = {o.client_order_id for o in active_db_orders if o.state not in (
            OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED, OrderState.REJECTED
        )}
        broker_id_set = set(broker_open_order_ids)

        ghost_orders = list(broker_id_set - db_client_ids)
        unmatched_db = list(db_client_ids - broker_id_set)

        is_healthy = len(ghost_orders) == 0 and len(unmatched_db) == 0
        details = []
        if ghost_orders:
            details.append(f"Detected {len(ghost_orders)} orders on broker not tracked in DB (Ghost Orders)!")
        if unmatched_db:
            details.append(f"Detected {len(unmatched_db)} active DB orders missing on broker!")

        from datetime import datetime, timezone
        report = ReconciliationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_healthy=is_healthy,
            ghost_broker_orders=ghost_orders,
            unmatched_db_orders=unmatched_db,
            details=details,
        )

        if not is_healthy:
            logger.warning(
                "RECONCILIATION BREAK DETECTED between internal state and broker.",
                extra={"report": details},
            )

        return report
