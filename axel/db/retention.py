"""Retention pruning for append-only and time-series tables.

AXEL's paper runtime writes hundreds of thousands of rows per day. Nothing
pruned them before this module existed, so an eight-week soak grew without
bound and the dashboard's projections slowed down with every cycle.

The policy is intentionally conservative: defaults keep everything a paper soak
needs (market data for a year, equity snapshots for two years) while trimming
high-volume operational noise. Deletes are batched so a prune never holds a
long transaction or generates a single enormous WAL record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    OhlcvBar,
    OperationalEventModel,
    SignalModel,
)


@dataclass(frozen=True)
class RetentionPolicy:
    """Age thresholds, in days, per table."""

    operational_event_days: int = 30
    audit_log_days: int = 90
    signal_days: int = 90
    market_data_days: int = 365
    equity_snapshot_days: int = 730
    batch_size: int = 5_000


def policy_from_settings() -> RetentionPolicy:
    """Build the policy from the process settings."""
    return RetentionPolicy(
        operational_event_days=settings.operational_event_retention_days,
        audit_log_days=settings.audit_log_retention_days,
        signal_days=settings.signal_retention_days,
        market_data_days=settings.market_data_retention_days,
        equity_snapshot_days=settings.equity_snapshot_retention_days,
        batch_size=settings.retention_batch_size,
    )


def _delete_before(
    session: Session,
    model: type,
    column,
    cutoff: datetime,
    *,
    batch_size: int,
) -> int:
    """Delete rows older than ``cutoff`` in bounded batches."""
    removed = 0
    while True:
        ids = list(
            session.execute(
                select(model.id).where(column < cutoff).limit(batch_size)
            ).scalars()
        )
        if not ids:
            return removed
        result = session.execute(delete(model).where(model.id.in_(ids)))
        removed += result.rowcount or 0
        if len(ids) < batch_size:
            return removed


def prune(
    session: Session,
    policy: RetentionPolicy | None = None,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Apply the retention policy and return rows removed per table.

    ``commit=False`` lets a caller run the prune inside a larger transaction.
    """
    policy = policy or policy_from_settings()
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    def cutoff(days: int) -> datetime:
        return stamp - timedelta(days=max(0, days))

    removed = {
        "operational_events": _delete_before(
            session,
            OperationalEventModel,
            OperationalEventModel.occurred_at,
            cutoff(policy.operational_event_days),
            batch_size=policy.batch_size,
        ),
        "audit_log": _delete_before(
            session,
            AuditLogModel,
            AuditLogModel.timestamp,
            cutoff(policy.audit_log_days),
            batch_size=policy.batch_size,
        ),
        "signals": _delete_before(
            session,
            SignalModel,
            SignalModel.ts,
            cutoff(policy.signal_days),
            batch_size=policy.batch_size,
        ),
        "ohlcv_bars": _delete_before(
            session,
            OhlcvBar,
            OhlcvBar.timestamp,
            cutoff(policy.market_data_days),
            batch_size=policy.batch_size,
        ),
        "equity_snapshots": _delete_before(
            session,
            EquitySnapshotModel,
            EquitySnapshotModel.timestamp,
            cutoff(policy.equity_snapshot_days),
            batch_size=policy.batch_size,
        ),
    }
    if commit:
        session.commit()
    return removed


__all__ = ["RetentionPolicy", "policy_from_settings", "prune"]
