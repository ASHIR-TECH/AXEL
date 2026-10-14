"""Phase 6 durable order store tests (spec §7.3/§7.4)."""

from decimal import Decimal

from axel.core.types import OrderSide, OrderState
from axel.execution.broker_base import BrokerFill
from axel.execution.order_store import OrderEventKind, OrderEventStore


def _fill(qty: str = "10", price: str = "100", fill_id: str | None = "fill-1") -> BrokerFill:
    return BrokerFill(
        broker_order_id="broker-1",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=Decimal(qty),
        price=Decimal(price),
        broker_fill_id=fill_id,
        client_order_id="ord-1",
    )


def _seed(store: OrderEventStore, client_order_id: str = "ord-1") -> None:
    store.append(
        client_order_id=client_order_id,
        kind=OrderEventKind.CREATED,
        state=OrderState.CREATED,
        metadata={
            "symbol": "SPY",
            "side": "buy",
            "qty": "10",
            "order_hash": "hash-1",
            "config_version": "cfg-1",
        },
    )


def _to_acknowledged(store: OrderEventStore, client_order_id: str = "ord-1") -> None:
    """Drive an order through the valid lifecycle to ACKNOWLEDGED."""
    store.append(client_order_id=client_order_id, kind=OrderEventKind.APPROVED, state=OrderState.APPROVED)
    store.append(client_order_id=client_order_id, kind=OrderEventKind.SUBMITTED, state=OrderState.SUBMITTED)
    store.append(
        client_order_id=client_order_id,
        kind=OrderEventKind.BROKER_ACK,
        state=OrderState.ACKNOWLEDGED,
        broker_order_id="broker-1",
    )


def test_append_and_project_lifecycle():
    store = OrderEventStore()
    _seed(store)
    store.append(client_order_id="ord-1", kind=OrderEventKind.APPROVED, state=OrderState.APPROVED)
    store.append(client_order_id="ord-1", kind=OrderEventKind.SUBMITTED, state=OrderState.SUBMITTED)
    store.append(
        client_order_id="ord-1",
        kind=OrderEventKind.BROKER_ACK,
        state=OrderState.ACKNOWLEDGED,
        broker_order_id="broker-1",
    )

    record = store.record("ord-1")
    assert record is not None
    assert record.state is OrderState.ACKNOWLEDGED
    assert record.broker_order_id == "broker-1"
    assert record.qty == Decimal(10)
    assert record.order_hash == "hash-1"
    assert record.event_count == 4


def test_partial_then_full_fill_projection():
    store = OrderEventStore()
    _seed(store)
    _to_acknowledged(store)

    first = store.append_fill(_fill(qty="4", price="100", fill_id="f-1"))
    assert first is not None and first.state is OrderState.PARTIALLY_FILLED

    second = store.append_fill(_fill(qty="6", price="102", fill_id="f-2"))
    assert second is not None and second.state is OrderState.FILLED

    record = store.record("ord-1")
    assert record.filled_qty == Decimal(10)
    assert record.remaining_qty == Decimal(0)
    # Weighted average: (4*100 + 6*102) / 10 = 101.2
    assert record.avg_fill_price == Decimal("101.2")


def test_duplicate_fill_is_ignored():
    store = OrderEventStore()
    _seed(store)
    _to_acknowledged(store)

    assert store.append_fill(_fill(fill_id="f-1")) is not None
    assert store.append_fill(_fill(fill_id="f-1")) is None  # duplicate id
    record = store.record("ord-1")
    assert record.filled_qty == Decimal(10)


def test_fill_identity_falls_back_to_fields_when_no_id():
    store = OrderEventStore()
    _seed(store)
    _to_acknowledged(store)
    assert store.append_fill(_fill(qty="3", price="50", fill_id=None)) is not None
    assert store.append_fill(_fill(qty="3", price="50", fill_id=None)) is None


def test_illegal_transition_recorded_as_anomaly_without_corrupting_state():
    store = OrderEventStore()
    _seed(store)
    _to_acknowledged(store)
    store.append(client_order_id="ord-1", kind=OrderEventKind.STATE_CHANGE, state=OrderState.FILLED)
    # A late CANCELED event is impossible after FILLED.
    event = store.append(client_order_id="ord-1", kind=OrderEventKind.STATE_CHANGE, state=OrderState.CANCELED)
    assert event.anomaly is not None
    record = store.record("ord-1")
    assert record.state is OrderState.FILLED
    assert record.anomaly_count == 1


def test_snapshot_and_restore_reconstructs_projection_and_dedup():
    store = OrderEventStore()
    _seed(store)
    _to_acknowledged(store)
    store.append_fill(_fill(qty="5", price="100", fill_id="f-1"))

    snapshot = store.snapshot()
    restored = OrderEventStore()
    restored.restore(snapshot)

    record = restored.record("ord-1")
    assert record.state is OrderState.PARTIALLY_FILLED
    assert record.filled_qty == Decimal(5)
    # Duplicate fill after restart is still de-duplicated.
    assert restored.append_fill(_fill(qty="5", price="100", fill_id="f-1")) is None
