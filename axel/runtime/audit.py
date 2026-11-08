"""Append-only audit helpers shared by the paper runtime workers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from axel.core.ids import generate_id
from axel.db.models import AuditLogModel


def record(
    session: Session,
    *,
    event_type: str,
    actor: str,
    message: str,
    logger: str | None = None,
    channel: str | None = None,
    level: str | None = None,
    timestamp: datetime | None = None,
    **extra: Any,
) -> AuditLogModel:
    """Add one audit row. Caller owns the transaction."""
    payload: dict[str, Any] = {
        "message": message,
        "logger": logger or actor,
        "channel": channel or "SYS",
    }
    if level:
        payload["level"] = level
    payload.update(extra)
    row = AuditLogModel(
        id=generate_id("audit"),
        timestamp=timestamp or datetime.now(UTC),
        event_type=event_type,
        actor=actor,
        payload=payload,
    )
    session.add(row)
    return row


__all__ = ["record"]
