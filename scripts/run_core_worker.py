"""Run the deterministic risk, execution and accounting worker."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from axel.risk.killswitch import KillSwitch
from axel.runtime.core import CycleResult, core_once
from axel.runtime.runner import run_worker

# One instance per process so the high-water mark survives between cycles.
KILLSWITCH = KillSwitch()


def step(
    session: Session, now: datetime, _seed: int, _interval: float
) -> CycleResult:
    return core_once(session, now=now, killswitch=KILLSWITCH)


if __name__ == "__main__":
    raise SystemExit(run_worker(step, "core-worker"))
