"""Run the full deterministic paper runtime in one process.

Useful for a laptop demo or a single-container deployment.  Production uses the
three separate workers so ingest, proposal generation and execution can scale
and fail independently.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from axel.risk.killswitch import KillSwitch
from axel.runtime.agent import agent_once
from axel.runtime.core import CycleResult, core_once
from axel.runtime.ingest import ingest_once
from axel.runtime.runner import run_worker

KILLSWITCH = KillSwitch()


def step(session: Session, now: datetime, seed: int, interval: float) -> dict[str, object]:
    ingest = ingest_once(session, now=now, interval_seconds=interval, seed=seed)
    agent = agent_once(session, now=now)
    core: CycleResult = core_once(session, now=now, killswitch=KILLSWITCH)
    return {"ingest": ingest, "agent": agent, "core": core}


if __name__ == "__main__":
    raise SystemExit(run_worker(step, "paper-runtime"))
