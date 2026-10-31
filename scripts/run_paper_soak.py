"""Long-running paper-soak process. It refuses live configuration at startup."""

import argparse
import signal
from datetime import UTC, datetime
from pathlib import Path
from time import sleep

from axel.core.config import settings
from axel.core.logging import log_event, setup_logging
from axel.db.session import init_db

HEARTBEAT_PATH = Path("/tmp/axel-paper-soak-heartbeat")


def validate_paper_mode() -> None:
    if settings.environment != "paper" or not settings.alpaca_paper:
        raise RuntimeError("Paper soak refuses to start unless ENVIRONMENT=paper and ALPACA_PAPER=true")


def tick() -> None:
    """Record liveness. Trade generation is intentionally attached separately after validation."""
    HEARTBEAT_PATH.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    log_event(
        "info",
        "paper_soak_heartbeat",
        "paper-soak heartbeat recorded; no live trading path is enabled",
        heartbeat_path=str(HEARTBEAT_PATH),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AXEL's paper-only soak process")
    parser.add_argument("--once", action="store_true", help="validate configuration and emit one heartbeat")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.interval_seconds < 10:
        parser.error("--interval-seconds must be at least 10")

    setup_logging(
        settings.log_level,
        log_file=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        force=True,
    )
    validate_paper_mode()
    init_db()
    tick()
    if args.once:
        return 0

    running = True
    def stop_handler(*_args: object) -> None:
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    while running:
        sleep(args.interval_seconds)
        if running:
            tick()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
