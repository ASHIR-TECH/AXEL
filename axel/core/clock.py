from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta


class Clock(ABC):
    """Abstract interface for all time queries."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time (always timezone-aware UTC)."""

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Sleep or step forward by seconds."""


class RealClock(Clock):
    """Real wall-clock time provider."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        import time
        time.sleep(seconds)


class SimulatedClock(Clock):
    """
    Deterministic simulated clock for tests and replay.
    Can be stepped explicitly without blocking wall-clock execution.
    """

    def __init__(self, initial_time: datetime | None = None):
        if initial_time is None:
            self._current_time = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
        else:
            if initial_time.tzinfo is None:
                self._current_time = initial_time.replace(tzinfo=UTC)
            else:
                self._current_time = initial_time

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=UTC)
        else:
            self._current_time = new_time

    def advance(self, delta: timedelta) -> None:
        self._current_time += delta

    def sleep(self, seconds: float) -> None:
        self.advance(timedelta(seconds=seconds))
