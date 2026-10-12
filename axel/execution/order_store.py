"""
Durable, append-only order event store and projection (Phase 6).

The orders the live path places must survive a crash and a restart, must never
be submitted twice for the same intent, and must not be corrupted by late or
duplicate broker events. The design follows
`docs/AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md` §7.3/§7.4:

* orders are written as an **append-only event log**;
* a projection is *derived* by replaying that log, never mutated in place;
* submission uses an **outbox**: the ``SUBMITTED`` event is persisted *before*
  the broker call so a crash mid-submit leaves a recoverable marker;
* fills are de-duplicated by broker fill id, falling back to a
  (order, qty, price, time) identity;
* illegal/out-of-order events are recorded as anomalies without overwriting a
  more advanced state.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from axel.core.money import to_decimal
from axel.core.types import OrderSide, OrderState
from axel.execution.broker_base import BrokerFill
from axel.execution.order_fsm import OrderStateMachine


class OrderEventKind(str, Enum):
    """What caused an entry in the order log."""

    CREATED = "created"
    APPROVED = "approved"
    SUBMITTED = "submitted"          # outbox write, before the broker call
    BROKER_ACK = "broker_ack"        # broker accepted / acknowledged
    FILL = "fill"
    CANCEL_REQUESTED = "cancel_requested"
    STATE_CHANGE = "state_change"    # generic, e.g. expired/rejected
    ERROR = "error"
    RECONCILED = "reconciled"


@dataclass(frozen=True)
class OrderEvent:
    """A single immutable entry in an order's log."""

    sequence: int
    client_order_id: str
    kind: OrderEventKind
    state: OrderState
    occurred_at: datetime
    recorded_at: datetime
    reason_code: str | None = None
    message: str | None = None
    broker_order_id: str | None = None
    fill_id: str | None = None
    payload: dict | None = None
    metadata: dict | None = None
    anomaly: str | None = None


@dataclass(frozen=True)
class OrderRecord:
    """Projected current state of an order, derived from its event log."""

    client_order_id: str
    state: OrderState
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    qty: Decimal = Decimal(0)
    filled_qty: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    fees: Decimal = Decimal(0)
    broker_order_id: str | None = None
    order_hash: str | None = None
    config_version: str | None = None
    risk_decision_id: str | None = None
    approval_id: str | None = None
    strategy_id: str | None = None
    strategy_version: str | None = None
    correlation_id: str | None = None
    intent_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_reason_code: str | None = None
    anomaly_count: int = 0
    event_count: int = 0

    @property
    def is_terminal(self) -> bool:
        return OrderStateMachine.is_terminal(self.state)

    @property
    def remaining_qty(self) -> Decimal:
        return self.qty - self.filled_qty


def _fill_identity(fill: BrokerFill) -> str:
    if fill.broker_fill_id:
        return f"id|{fill.broker_fill_id}"
    event_time = fill.event_time.isoformat() if fill.event_time else ""
    return (
        f"tuple|{fill.broker_order_id}|{fill.symbol}|{fill.side.value}|"
        f"{fill.qty}|{fill.price}|{event_time}"
    )


class OrderEventStore:
    """
    In-memory append-only order store with a replayed projection.

    The store is thread-safe for the append/read pattern used by the execution
    loop. Persistence to the database is layered on top by the service; this
    class owns the canonical in-process semantics and supports
    :meth:`snapshot`/:meth:`restore` for restart reconstruction tests.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[OrderEvent]] = {}
        self._fill_keys: set[str] = set()
        self._lock = threading.RLock()

    # ── Append ──────────────────────────────────────────────────────────────
    def append(
        self,
        *,
        client_order_id: str,
        kind: OrderEventKind,
        state: OrderState,
        reason_code: str | None = None,
        message: str | None = None,
        broker_order_id: str | None = None,
        fill_id: str | None = None,
        payload: dict | None = None,
        metadata: dict | None = None,
        occurred_at: datetime | None = None,
        recorded_at: datetime | None = None,
    ) -> OrderEvent:
        with self._lock:
            log = self._events.setdefault(client_order_id, [])
            current = self._project(log)
            anomaly: str | None = None
            if (
                current is not None
                and current.state != state
                and not OrderStateMachine.can_transition(current.state, state)
            ):
                # Record the observation but never overwrite a more advanced or
                # terminal state with an illegal transition.
                anomaly = f"illegal transition {current.state.value} -> {state.value}"
            event = OrderEvent(
                sequence=len(log) + 1,
                client_order_id=client_order_id,
                kind=kind,
                state=state,
                occurred_at=occurred_at or datetime.now(UTC),
                recorded_at=recorded_at or datetime.now(UTC),
                reason_code=reason_code,
                message=message,
                broker_order_id=broker_order_id,
                fill_id=fill_id,
                payload=payload,
                metadata=metadata,
                anomaly=anomaly,
            )
            log.append(event)
            return event

    def append_fill(
        self,
        fill: BrokerFill,
        *,
        dedup: bool = True,
        occurred_at: datetime | None = None,
    ) -> OrderEvent | None:
        """
        Record a broker fill against its order.

        Returns ``None`` when the fill is a duplicate (already stored). The
        resulting state is ``FILLED`` once the cumulative filled quantity meets
        the order quantity, otherwise ``PARTIALLY_FILLED``.
        """
        key = _fill_identity(fill)
        client_order_id = fill.client_order_id or self._client_id_for(fill.broker_order_id)
        if client_order_id is None:
            return None

        with self._lock:
            if dedup and key in self._fill_keys:
                return None
            self._fill_keys.add(key)

            current = self._project(self._events.get(client_order_id, []))
            prior_filled = current.filled_qty if current else Decimal(0)
            total_qty = current.qty if current else fill.qty
            new_filled = prior_filled + fill.qty
            new_state = OrderState.FILLED if new_filled >= total_qty else OrderState.PARTIALLY_FILLED

            return self.append(
                client_order_id=client_order_id,
                kind=OrderEventKind.FILL,
                state=new_state,
                fill_id=fill.broker_fill_id,
                broker_order_id=fill.broker_order_id,
                payload={
                    "qty": str(fill.qty),
                    "price": str(fill.price),
                    "fee": str(fill.fee),
                    "side": fill.side.value,
                    "event_time": fill.event_time.isoformat() if fill.event_time else None,
                    "dedup_key": key,
                },
                occurred_at=occurred_at or fill.event_time,
                recorded_at=fill.received_at,
            )

    # ── Reads ───────────────────────────────────────────────────────────────
    def events(self, client_order_id: str) -> tuple[OrderEvent, ...]:
        with self._lock:
            return tuple(self._events.get(client_order_id, ()))

    def record(self, client_order_id: str) -> OrderRecord | None:
        with self._lock:
            return self._project(self._events.get(client_order_id, []))

    def records(self) -> list[OrderRecord]:
        with self._lock:
            return [self._project(log) for log in self._events.values() if log]

    def has_fill(self, fill: BrokerFill) -> bool:
        with self._lock:
            return _fill_identity(fill) in self._fill_keys

    # ── Restart reconstruction ──────────────────────────────────────────────
    def snapshot(self) -> list[OrderEvent]:
        """All events in sequence order, suitable for durable persistence."""
        with self._lock:
            all_events: list[OrderEvent] = []
            for log in self._events.values():
                all_events.extend(log)
        return sorted(all_events, key=lambda e: (e.client_order_id, e.sequence))

    def restore(self, events: Sequence[OrderEvent]) -> None:
        """Rebuild the store (and projections) from a persisted event log."""
        with self._lock:
            self._events = {}
            self._fill_keys = set()
            for event in sorted(events, key=lambda e: (e.client_order_id, e.sequence)):
                self._events.setdefault(event.client_order_id, []).append(event)
                if event.kind is OrderEventKind.FILL and event.payload:
                    dedup_key = event.payload.get("dedup_key")
                    if dedup_key:
                        self._fill_keys.add(dedup_key)

    # ── Internals ───────────────────────────────────────────────────────────
    def _client_id_for(self, broker_order_id: str) -> str | None:
        for client_order_id, log in self._events.items():
            record = self._project(log)
            if record and record.broker_order_id == broker_order_id:
                return client_order_id
        return None

    @staticmethod
    def _project(log: Sequence[OrderEvent]) -> OrderRecord | None:
        if not log:
            return None

        metadata: dict = {}
        for event in log:
            if event.kind is OrderEventKind.CREATED and event.metadata:
                metadata = dict(event.metadata)
                break

        state = log[0].state
        broker_order_id: str | None = metadata.get("broker_order_id")
        filled = Decimal(0)
        fees = Decimal(0)
        notional = Decimal(0)
        avg_price: Decimal | None = None
        updated_at: datetime | None = None
        last_reason: str | None = None
        anomalies = 0

        for event in log:
            if event.anomaly:
                anomalies += 1
            elif event.state != state:
                state = event.state
            if event.broker_order_id:
                broker_order_id = event.broker_order_id
            if event.reason_code:
                last_reason = event.reason_code
            updated_at = event.recorded_at
            if event.kind is OrderEventKind.FILL and event.payload:
                qty = to_decimal(event.payload.get("qty", "0"))
                price = to_decimal(event.payload.get("price", "0"))
                filled += qty
                fees += to_decimal(event.payload.get("fee", "0"))
                notional += qty * price
                if filled > 0:
                    avg_price = notional / filled

        return OrderRecord(
            client_order_id=log[0].client_order_id,
            state=state,
            symbol=metadata.get("symbol", ""),
            side=OrderSide(metadata["side"]) if metadata.get("side") else OrderSide.BUY,
            qty=to_decimal(metadata.get("qty", "0")),
            filled_qty=filled,
            avg_fill_price=avg_price,
            fees=fees,
            broker_order_id=broker_order_id,
            order_hash=metadata.get("order_hash"),
            config_version=metadata.get("config_version"),
            risk_decision_id=metadata.get("risk_decision_id"),
            approval_id=metadata.get("approval_id"),
            strategy_id=metadata.get("strategy_id"),
            strategy_version=metadata.get("strategy_version"),
            correlation_id=metadata.get("correlation_id"),
            intent_id=metadata.get("intent_id"),
            created_at=log[0].occurred_at,
            updated_at=updated_at,
            last_reason_code=last_reason,
            anomaly_count=anomalies,
            event_count=len(log),
        )
