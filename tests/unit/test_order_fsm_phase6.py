"""Phase 6 canonical order state machine tests (spec §7)."""

import pytest

from axel.core.types import OrderState
from axel.execution.order_fsm import (
    TERMINAL_STATES,
    InvalidOrderTransitionError,
    OrderStateMachine,
)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (OrderState.CREATED, OrderState.APPROVED),
        (OrderState.APPROVED, OrderState.SUBMITTED),
        (OrderState.SUBMITTED, OrderState.ACKNOWLEDGED),
        (OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED),
        (OrderState.PARTIALLY_FILLED, OrderState.FILLED),
        (OrderState.ACKNOWLEDGED, OrderState.CANCEL_PENDING),
        (OrderState.CANCEL_PENDING, OrderState.CANCELED),
        (OrderState.SUBMITTED, OrderState.UNKNOWN),
        (OrderState.UNKNOWN, OrderState.FILLED),
        (OrderState.UNKNOWN, OrderState.CANCELED),
        (OrderState.ERROR, OrderState.ACKNOWLEDGED),
    ],
)
def test_valid_phase6_transitions(current, new):
    assert OrderStateMachine.can_transition(current, new)
    assert OrderStateMachine.transition(current, new) == new


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (OrderState.FILLED, OrderState.CANCELED),
        (OrderState.CANCELED, OrderState.FILLED),
        (OrderState.REJECTED, OrderState.FILLED),
        (OrderState.EXPIRED, OrderState.ACKNOWLEDGED),
        (OrderState.ERROR, OrderState.SUBMITTED),  # never blind-resubmit an error
        (OrderState.CREATED, OrderState.FILLED),   # skips approval + submission
        (OrderState.APPROVED, OrderState.FILLED),  # cannot skip submission
    ],
)
def test_invalid_phase6_transitions(current, new):
    assert not OrderStateMachine.can_transition(current, new)
    with pytest.raises(InvalidOrderTransitionError):
        OrderStateMachine.transition(current, new)


def test_self_transition_is_idempotent():
    for state in (
        OrderState.SUBMITTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN,
    ):
        assert OrderStateMachine.transition(state, state) == state


def test_terminal_states_have_no_outward_transitions():
    for state in TERMINAL_STATES:
        assert OrderStateMachine.is_terminal(state)
        for candidate in OrderState:
            if candidate is state:
                continue
            assert not OrderStateMachine.can_transition(state, candidate)


def test_error_is_recoverable_but_not_terminal():
    """ERROR may be repaired by reconciliation but is never a normal terminal state."""
    assert not OrderStateMachine.is_terminal(OrderState.ERROR)
    assert OrderStateMachine.can_transition(OrderState.ERROR, OrderState.ACKNOWLEDGED)
    assert not OrderStateMachine.can_transition(OrderState.ERROR, OrderState.SUBMITTED)
