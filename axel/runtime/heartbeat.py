"""Worker liveness and soak-continuity evidence.

The Phase 5 promotion gate asks for 56 days of paper operation. Before this
module, that number was derived from ``now - started_at``, so a week of downtime
counted as a week of soak. Each runtime worker now records a heartbeat every
cycle; when a worker resumes after a longer-than-expected gap it attributes the
gap to downtime and increments a restart counter.

The result is a coverage figure that can be defended at the Phase 6 review:

    covered = (last_seen - first_seen) - downtime
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from axel.db.models import WorkerHeartbeatModel

# A gap is only counted as downtime once it exceeds the expected cadence plus
# slack, so scheduler jitter and a slow cycle do not look like an outage.
GAP_SLACK_SECONDS = 5.0


def _as_utc(value: datetime) -> datetime:
    """SQLite round-trips ``DateTime(timezone=True)`` as naive; normalize it."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class WorkerContinuity:
    """Coverage summary for one worker."""

    worker: str
    first_seen: datetime
    last_seen: datetime
    cycles: int
    downtime_seconds: float
    gap_count: int

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, (self.last_seen - self.first_seen).total_seconds())

    @property
    def covered_seconds(self) -> float:
        return max(0.0, self.elapsed_seconds - self.downtime_seconds)

    @property
    def covered_days(self) -> float:
        return self.covered_seconds / 86_400.0

    @property
    def coverage_ratio(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return min(1.0, self.covered_seconds / self.elapsed_seconds)


def record_heartbeat(
    session: Session,
    worker: str,
    *,
    cycles: int,
    interval_seconds: float,
    now: datetime | None = None,
    version: str | None = None,
) -> WorkerHeartbeatModel:
    """Upsert one worker's heartbeat and account for any downtime.

    Call this once per cycle, inside the same transaction as the cycle's work,
    so a heartbeat only exists if the work was durable.
    """
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    row = session.get(WorkerHeartbeatModel, worker)
    if row is None:
        row = WorkerHeartbeatModel(
            worker=worker,
            cycles=cycles,
            interval_seconds=interval_seconds,
            first_seen=stamp,
            last_seen=stamp,
            downtime_seconds=0.0,
            gap_count=0,
            version=version,
        )
        session.add(row)
        return row

    elapsed = (stamp - _as_utc(row.last_seen)).total_seconds()
    tolerance = max(interval_seconds * 2.0, interval_seconds + GAP_SLACK_SECONDS)
    if elapsed > tolerance:
        # Attribute everything beyond one expected cycle to downtime.
        row.downtime_seconds = float(row.downtime_seconds or 0.0) + (elapsed - interval_seconds)
        row.gap_count = int(row.gap_count or 0) + 1
    row.cycles = cycles
    row.interval_seconds = interval_seconds
    row.last_seen = stamp
    if version is not None:
        row.version = version
    return row


def continuity_report(session: Session) -> list[WorkerContinuity]:
    """Return a coverage summary for every worker that has a heartbeat."""
    rows = session.execute(
        select(WorkerHeartbeatModel).order_by(WorkerHeartbeatModel.worker.asc())
    ).scalars()
    return [
        WorkerContinuity(
            worker=row.worker,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            cycles=int(row.cycles or 0),
            downtime_seconds=float(row.downtime_seconds or 0.0),
            gap_count=int(row.gap_count or 0),
        )
        for row in rows
    ]


def covered_days(
    session: Session,
    *,
    workers: tuple[str, ...] = ("ingest-worker", "agent-worker", "core-worker"),
) -> float:
    """Return the shortest covered duration across the required workers.

    The soak is only as continuous as its least-continuous worker, so the
    minimum is the honest number to compare against the 56-day gate.
    """
    reports = {report.worker: report for report in continuity_report(session)}
    present = [reports[name].covered_days for name in workers if name in reports]
    if not present:
        return 0.0
    return min(present)


__all__ = [
    "GAP_SLACK_SECONDS",
    "WorkerContinuity",
    "continuity_report",
    "covered_days",
    "record_heartbeat",
]
