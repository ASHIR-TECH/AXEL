"""Shared loop runner for the deterministic paper runtime workers."""

from __future__ import annotations

import argparse
import signal
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.db.session import SessionLocal, init_db

Step = Callable[[Session, datetime, int, float], Any]


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.paper_runtime_interval_seconds,
        help="seconds between cycles (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=settings.paper_runtime_seed,
        help="deterministic feed seed (default: %(default)s)",
    )
    parser.add_argument("--once", action="store_true", help="run exactly one cycle")
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="number of cycles to run; 0 means until stopped (default: 0)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="create tables before starting (local development only)",
    )
    return parser


def run_worker(step: Step, description: str) -> int:
    """Run ``step`` on a fixed interval until interrupted."""
    args = build_parser(description).parse_args()
    if args.init_db:
        init_db()

    interval = max(0.1, float(args.interval))
    cycles = 1 if args.once else args.cycles
    stop = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    completed = 0
    while not stop.is_set() and (cycles <= 0 or completed < cycles):
        now = datetime.now(UTC)
        with SessionLocal() as session:
            result = step(session, now, args.seed, interval)
            session.commit()
        completed += 1
        print(f"[{description}] cycle={completed} {result}", flush=True)
        if cycles > 0 and completed >= cycles:
            break
        stop.wait(interval)
    return 0


__all__ = ["Step", "build_parser", "run_worker"]
