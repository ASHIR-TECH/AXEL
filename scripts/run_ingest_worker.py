"""Run the durable paper-market ingest worker."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from axel.runtime.ingest import ingest_once
from axel.runtime.runner import run_worker


def step(session: Session, now: datetime, seed: int, interval: float) -> dict[str, int]:
    return ingest_once(session, now=now, interval_seconds=interval, seed=seed)


if __name__ == "__main__":
    raise SystemExit(run_worker(step, "ingest-worker"))
