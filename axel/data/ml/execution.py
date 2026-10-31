"""Deterministic fill, partial-fill and rejection model.

A backtest that assumes every order fills in full at the bar open overstates
capacity. This module makes the execution assumption explicit:

* A bar with no volume cannot fill anything.
* With ``max_participation_rate`` set, a fill is capped at that fraction of the
  bar's volume. The remainder is either left unfilled (rejection) or reported as
  a partial fill, depending on ``allow_partial_fills``.
* Fills below ``min_fill_quantity`` are rejected rather than rounded up.

The model is deliberately stateless: the backtester re-targets every bar, so an
unfilled remainder is naturally retried on the next decision. That keeps the
simulation deterministic and avoids hidden order queues.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import copysign

from axel.data.schemas import BarRecord


class RejectionReason(StrEnum):
    """Why an order (or part of an order) did not fill."""

    NO_LIQUIDITY = "no_liquidity"
    PARTICIPATION_LIMIT = "participation_limit"
    BELOW_MINIMUM = "below_minimum"


@dataclass(frozen=True)
class ExecutionPlan:
    """Result of applying the execution model to one requested quantity."""

    requested_quantity: float
    filled_quantity: float
    rejection_reason: RejectionReason | None = None
    capacity: float | None = None

    @property
    def rejected_quantity(self) -> float:
        return self.requested_quantity - self.filled_quantity

    @property
    def is_partial(self) -> bool:
        return (
            self.filled_quantity != 0
            and abs(self.filled_quantity) < abs(self.requested_quantity)
        )


@dataclass(frozen=True)
class ExecutionModel:
    """Volume-aware fill assumptions.

    ``max_participation_rate=None`` means no volume cap, but zero-volume bars
    still fail closed unless ``reject_zero_volume`` is disabled.
    """

    max_participation_rate: float | None = None
    min_fill_quantity: float = 0.0
    allow_partial_fills: bool = True
    reject_zero_volume: bool = True

    def __post_init__(self) -> None:
        if self.max_participation_rate is not None and not (
            0.0 < self.max_participation_rate <= 1.0
        ):
            raise ValueError("max_participation_rate must be in (0, 1]")
        if self.min_fill_quantity < 0:
            raise ValueError("min_fill_quantity cannot be negative")

    def capacity(self, bar: BarRecord) -> float | None:
        """Maximum absolute quantity executable against this bar."""
        if bar.volume < 0:
            raise ValueError("bar volume cannot be negative")
        if bar.volume == 0 and self.reject_zero_volume:
            return 0.0
        if self.max_participation_rate is None:
            return None
        return bar.volume * self.max_participation_rate

    def plan(self, requested_quantity: float, bar: BarRecord) -> ExecutionPlan:
        requested = float(requested_quantity)
        capacity = self.capacity(bar)
        if requested == 0:
            return ExecutionPlan(requested, 0.0, None, capacity)
        if capacity is not None and capacity <= 0:
            return ExecutionPlan(
                requested, 0.0, RejectionReason.NO_LIQUIDITY, capacity
            )

        filled = requested
        reason: RejectionReason | None = None
        if capacity is not None and abs(requested) > capacity:
            reason = RejectionReason.PARTICIPATION_LIMIT
            filled = copysign(capacity, requested) if self.allow_partial_fills else 0.0

        if filled != 0 and abs(filled) < self.min_fill_quantity:
            return ExecutionPlan(
                requested, 0.0, RejectionReason.BELOW_MINIMUM, capacity
            )
        return ExecutionPlan(requested, filled, reason, capacity)


__all__ = ["ExecutionModel", "ExecutionPlan", "RejectionReason"]
