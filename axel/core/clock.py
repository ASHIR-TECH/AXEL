"""
Injectable Clock abstraction for AXEL.
Enables deterministic backtesting, event replay, and offline drill testing.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional


class Clock(ABC):
    """Abstract interface for all time queries."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time (always timezone-aware UTC)."""
        pass

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Sleep or step forward by seconds."""
        pass


class RealClock(Clock):
    """Real wall-clock time provider."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        import time
        time.sleep(seconds)


class SimulatedClock(Clock):
    """
    Deterministic simulated clock for tests and replay.
    Can be stepped explicitly without blocking wall-clock execution.
    """

    def __init__(self, initial_time: Optional[datetime] = None):
        if initial_time is None:
            self._current_time = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
        else:
            if initial_time.tzinfo is None:
                self._current_time = initial_time.replace(tzinfo=timezone.utc)
            else:
                self._current_time = initial_time

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=timezone.utc)
        else:
            self._current_time = new_time

    def advance(self, delta: timedelta) -> None:
        self._current_time += delta

    def sleep(self, seconds: float) -> None:
        self.advance(timedelta(seconds=seconds))
