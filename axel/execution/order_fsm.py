"""
Order Finite State Machine (FSM) enforcing valid state transitions.
Prevents illegal state shifts (e.g. FILLED -> CANCELED or REJECTED -> FILLED).
"""

from dataclasses import dataclass
from typing import Dict, Set

from axel.core.types import OrderState


class InvalidOrderTransitionError(Exception):
    """Raised when an illegal order state transition is attempted."""
    pass


VALID_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.PENDING_SUBMIT: {
        OrderState.SUBMITTED,
        OrderState.REJECTED,
        OrderState.CANCELED,
    },
    OrderState.SUBMITTED: {
        OrderState.ACCEPTED,
        OrderState.REJECTED,
        OrderState.CANCELED,
    },
    OrderState.ACCEPTED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
    },
    # Terminal states have NO outward transitions
    OrderState.FILLED: set(),
    OrderState.CANCELED: set(),
    OrderState.EXPIRED: set(),
    OrderState.REJECTED: set(),
}


@dataclass(frozen=True)
class OrderStateMachine:
    """Deterministic order state machine evaluator."""

    @staticmethod
    def is_terminal(state: OrderState) -> bool:
        """Returns True if the state is terminal (cannot change)."""
        return state in {
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.REJECTED,
        }

    @staticmethod
    def can_transition(current_state: OrderState, new_state: OrderState) -> bool:
        """Checks if a transition from current_state to new_state is allowed."""
        if current_state == new_state:
            return True  # Idempotent re-affirmation
        allowed = VALID_TRANSITIONS.get(current_state, set())
        return new_state in allowed

    @classmethod
    def transition(cls, current_state: OrderState, new_state: OrderState) -> OrderState:
        """
        Transitions to new_state or raises InvalidOrderTransitionError.
        Self-transitions are idempotent no-ops.
        """
        if current_state == new_state:
            return current_state

        if not cls.can_transition(current_state, new_state):
            raise InvalidOrderTransitionError(
                f"Illegal order state transition: cannot transition from "
                f"'{current_state.value}' to '{new_state.value}'."
            )
        return new_state
