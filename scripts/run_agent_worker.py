"""Run the deterministic analyst signal and proposal worker."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from axel.runtime.agent import agent_once
from axel.runtime.runner import run_worker


def step(session: Session, now: datetime, _seed: int, _interval: float) -> dict[str, int]:
    return agent_once(session, now=now)


if __name__ == "__main__":
    raise SystemExit(run_worker(step, "agent-worker"))
