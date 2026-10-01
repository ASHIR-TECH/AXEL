"""
Point-in-time integrity helpers.

Phase 2 rule: a historical query at time ``t`` may use only information publicly
available at or before ``t``. Joins are performed on availability time, never on
economic period date.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from axel.data.schemas import CanonicalRecord, require_tz


class LookaheadError(ValueError):
    """Raised when a query would consume data not yet available at decision time."""


def available_at(*times: datetime) -> datetime:
    """``available_at = max(source_available_at, dependency_availability_times)``."""
    if not times:
        raise ValueError("available_at requires at least one timestamp")
    for value in times:
        require_tz(value, "available_at")
    return max(times)


def feature_available_at(
    source_available_at: datetime, dependencies: Iterable[datetime] = ()
) -> datetime:
    return available_at(source_available_at, *tuple(dependencies))


def filter_available(
    records: Iterable[CanonicalRecord], *, at: datetime
) -> list[CanonicalRecord]:
    require_tz(at, "at")
    return [record for record in records if record.available_at <= at]


def as_of(
    records: Iterable[CanonicalRecord],
    *,
    at: datetime,
    key: str | None = None,
) -> CanonicalRecord | None:
    """Return the most recent record knowable at ``at`` (optionally filtered by key)."""
    candidates = filter_available(records, at=at)
    if key is not None:
        candidates = [record for record in candidates if record.key() == key]
    if not candidates:
        return None
    return max(candidates, key=lambda record: (record.event_time, record.available_at))


def assert_no_lookahead(record: CanonicalRecord, *, at: datetime) -> None:
    require_tz(at, "at")
    if record.available_at > at:
        raise LookaheadError(
            f"record {record.key()} is not available until {record.available_at.isoformat()}"
        )


def join_as_of(
    base_records: Iterable[CanonicalRecord],
    other_records: Iterable[CanonicalRecord],
    *,
    base_time: Callable[[CanonicalRecord], datetime] | None = None,
) -> list[tuple[CanonicalRecord, CanonicalRecord | None]]:
    """As-of join each base record to the latest ``other`` known at its decision time."""
    others = list(other_records)
    clock = base_time or (lambda record: record.event_time)
    return [(base, as_of(others, at=clock(base))) for base in base_records]


class PointInTimeIndex:
    """Key-indexed store that resolves the correct version of a fact at any time."""

    def __init__(self, records: Iterable[CanonicalRecord] = ()) -> None:
        self._by_key: dict[str, list[CanonicalRecord]] = {}
        for record in records:
            self.add(record)

    def add(self, record: CanonicalRecord) -> None:
        self._by_key.setdefault(record.key(), []).append(record)

    def get(self, key: str, *, at: datetime) -> CanonicalRecord | None:
        return as_of(self._by_key.get(key, ()), at=at)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._by_key)
