"""Phase 6 property-based tests (spec §15.1/§15.3 invariants)."""

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axel.core.ids import derive_client_order_id
from axel.core.types import OrderSide, OrderState
from axel.execution.broker_base import BrokerFill
from axel.execution.order_fsm import TERMINAL_STATES, VALID_TRANSITIONS, OrderStateMachine
from axel.execution.order_store import OrderEventKind, OrderEventStore

ALL_STATES = list(OrderState)


@given(current=st.sampled_from(ALL_STATES), target=st.sampled_from(ALL_STATES))
@settings(max_examples=400, deadline=None)
def test_transition_never_leaves_a_terminal_state(current, target):
    """No event may move an order out of a terminal state."""
    if current in TERMINAL_STATES and target != current:
        assert not OrderStateMachine.can_transition(current, target)
    # transition() must agree with can_transition().
    if OrderStateMachine.can_transition(current, target):
        result = OrderStateMachine.transition(current, target)
        assert result == target or target == current
    else:
        try:
            OrderStateMachine.transition(current, target)
        except Exception as exc:  # noqa: BLE001
            assert type(exc).__name__ == "InvalidOrderTransitionError"
        else:  # pragma: no cover - can_transition False must not silently pass
            raise AssertionError("illegal transition did not raise")


@given(state=st.sampled_from(ALL_STATES))
@settings(max_examples=50, deadline=None)
def test_self_transition_is_always_allowed(state):
    assert OrderStateMachine.can_transition(state, state)
    assert OrderStateMachine.transition(state, state) == state


def test_transition_table_covers_every_state():
    for state in ALL_STATES:
        assert state in VALID_TRANSITIONS
        if state in TERMINAL_STATES:
            assert VALID_TRANSITIONS[state] == set()


@st.composite
def _event_sequences(draw):
    client_order_id = "ord-prop-1"
    states = draw(st.lists(st.sampled_from(ALL_STATES), min_size=1, max_size=25))
    return client_order_id, states


@given(seq=_event_sequences())
@settings(max_examples=150, deadline=None)
def test_projection_survives_snapshot_restore(seq):
    client_order_id, states = seq
    store = OrderEventStore()
    store.append(
        client_order_id=client_order_id,
        kind=OrderEventKind.CREATED,
        state=OrderState.CREATED,
        metadata={"symbol": "SPY", "side": "buy", "qty": "10"},
    )
    for state in states:
        store.append(client_order_id=client_order_id, kind=OrderEventKind.STATE_CHANGE, state=state)

    record = store.record(client_order_id)
    restored = OrderEventStore()
    restored.restore(store.snapshot())
    assert restored.record(client_order_id) == record


@given(qty=st.integers(min_value=1, max_value=100), price=st.integers(min_value=1, max_value=1000))
@settings(max_examples=100, deadline=None)
def test_duplicate_fill_never_increases_filled_qty(qty, price):
    store = OrderEventStore()
    client_order_id = "ord-prop-fill"
    store.append(
        client_order_id=client_order_id,
        kind=OrderEventKind.CREATED,
        state=OrderState.CREATED,
        metadata={"symbol": "SPY", "side": "buy", "qty": "1000000"},
    )
    store.append(
        client_order_id=client_order_id, kind=OrderEventKind.BROKER_ACK, state=OrderState.ACKNOWLEDGED
    )
    fill = BrokerFill(
        broker_order_id="b-1",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=Decimal(qty),
        price=Decimal(price),
        broker_fill_id="fixed-fill-id",
        client_order_id=client_order_id,
        received_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    first = store.append_fill(fill)
    assert first is not None
    after_first = store.record(client_order_id).filled_qty
    second = store.append_fill(fill)
    assert second is None
    assert store.record(client_order_id).filled_qty == after_first


@given(hash_hex=st.text(alphabet="0123456789abcdef", min_size=8, max_size=64))
@settings(max_examples=200, deadline=None)
def test_client_order_id_is_deterministic_and_bounded(hash_hex):
    cid1 = derive_client_order_id(hash_hex)
    cid2 = derive_client_order_id(hash_hex)
    assert cid1 == cid2
    assert len(cid1) <= 128
    assert cid1.startswith("axl_")
