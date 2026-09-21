import pytest

from axel.core.types import OrderState
from axel.execution.order_fsm import (
    InvalidOrderTransitionError,
    OrderStateMachine,
)


def test_valid_order_lifecycle():
    state = OrderState.PENDING_SUBMIT
    state = OrderStateMachine.transition(state, OrderState.SUBMITTED)
    assert state == OrderState.SUBMITTED

    state = OrderStateMachine.transition(state, OrderState.ACCEPTED)
    assert state == OrderState.ACCEPTED

    state = OrderStateMachine.transition(state, OrderState.PARTIALLY_FILLED)
    assert state == OrderState.PARTIALLY_FILLED

    state = OrderStateMachine.transition(state, OrderState.FILLED)
    assert state == OrderState.FILLED
    assert OrderStateMachine.is_terminal(state)


def test_invalid_terminal_transition():
    # FILLED is terminal, cannot transition to CANCELED
    with pytest.raises(InvalidOrderTransitionError):
        OrderStateMachine.transition(OrderState.FILLED, OrderState.CANCELED)

    # REJECTED is terminal, cannot transition to FILLED
    with pytest.raises(InvalidOrderTransitionError):
        OrderStateMachine.transition(OrderState.REJECTED, OrderState.FILLED)


def test_idempotent_self_transition():
    state = OrderStateMachine.transition(OrderState.ACCEPTED, OrderState.ACCEPTED)
    assert state == OrderState.ACCEPTED
