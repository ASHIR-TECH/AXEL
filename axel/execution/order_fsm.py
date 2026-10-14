"""
Order Finite State Machine (FSM) enforcing valid state transitions.

Implements the canonical Phase 6 lifecycle from
`docs/AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md` §7.2:

    CREATED -> APPROVED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED

with explicit handling for ambiguous submission outcomes (`UNKNOWN`), requested
but unconfirmed cancellations (`CANCEL_PENDING`) and non-recoverable internal or
provider failures (`ERROR`). Legacy paper-era states (`PENDING_SUBMIT`,
`ACCEPTED`, `CANCELED`) are preserved so earlier phase code and tests keep
working.

Every transition must be validated against this table; broker events can arrive
late or out of order, so the table is deliberately permissive about re-affirming
a state but strict about impossible moves (e.g. `FILLED -> CANCELED`).
"""

from __future__ import annotations

from axel.core.types import OrderState

# States from which no further normal transition is possible. `ERROR` is excluded
# on purpose: an error can still be repaired by reconciliation, but never by a
# blind resubmit.
TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.REJECTED,
    }
)


class InvalidOrderTransitionError(Exception):
    """Raised when an illegal order state transition is attempted."""


# Canonical Phase 6 transition table. Legacy PENDING_SUBMIT/ACCEPTED/CANCELED
# rows are retained so Phase 1-5 flows are not broken.
VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    # ── Canonical Phase 6 lifecycle ────────────────────────────────────────
    OrderState.CREATED: {
        OrderState.APPROVED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    },
    OrderState.APPROVED: {
        OrderState.SUBMITTED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    },
    OrderState.SUBMITTED: {
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
        OrderState.ERROR,
        # Legacy aliases for Phase 1-5 flows.
        OrderState.ACCEPTED,
        OrderState.CANCELED,
    },
    OrderState.UNKNOWN: {
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.ERROR,
    },
    OrderState.ACKNOWLEDGED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    },
    OrderState.CANCEL_PENDING: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.ERROR: {
        # Reconciliation may repair an error to the broker-authoritative state,
        # but a new SUBMITTED transition is never permitted from ERROR.
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    },
    # ── Legacy paper-era lifecycle ─────────────────────────────────────────
    OrderState.PENDING_SUBMIT: {
        OrderState.SUBMITTED,
        OrderState.REJECTED,
        OrderState.CANCELED,
    },
    OrderState.ACCEPTED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN,
    },
    # Terminal states have NO outward transitions.
    OrderState.FILLED: set(),
    OrderState.CANCELED: set(),
    OrderState.EXPIRED: set(),
    OrderState.REJECTED: set(),
}


class OrderStateMachine:
    """Deterministic order state machine evaluator (pure, side-effect free)."""

    @staticmethod
    def is_terminal(state: OrderState) -> bool:
        """True if the state is terminal and cannot change without a new intent."""
        return state in TERMINAL_STATES

    @staticmethod
    def can_transition(current_state: OrderState, new_state: OrderState) -> bool:
        """Check whether a transition from ``current_state`` to ``new_state`` is allowed."""
        if current_state == new_state:
            return True  # Idempotent re-affirmation (late/duplicate events).
        allowed = VALID_TRANSITIONS.get(current_state, set())
        return new_state in allowed

    @classmethod
    def transition(cls, current_state: OrderState, new_state: OrderState) -> OrderState:
        """
        Transition to ``new_state`` or raise `InvalidOrderTransitionError`.

        Self-transitions are idempotent no-ops so duplicate or out-of-order
        broker events cannot corrupt the projection.
        """
        if current_state == new_state:
            return current_state

        if not cls.can_transition(current_state, new_state):
            raise InvalidOrderTransitionError(
                f"Illegal order state transition: cannot transition from "
                f"'{current_state.value}' to '{new_state.value}'."
            )
        return new_state
