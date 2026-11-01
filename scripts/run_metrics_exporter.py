"""Run the read-only AXEL Prometheus exporter.

This process is safe to run next to the trading core: it opens a short-lived
database session per scrape, reads the persisted kill-switch/halt state, and
serves the Prometheus text format. It never writes to the database, submits an
order, or changes risk state.
"""

from __future__ import annotations

import argparse

from axel.core.config import settings
from axel.core.logging import setup_logging
from axel.db.session import SessionLocal
from axel.execution.alpaca import AlpacaAdapter
from axel.observability.exporter import MetricsExporter, run_server
from axel.risk.halt import HaltController
from axel.risk.killswitch import KillSwitch


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AXEL Prometheus exporter")
    parser.add_argument("--host", default=settings.metrics_host)
    parser.add_argument("--port", type=int, default=settings.metrics_port)
    args = parser.parse_args()

    setup_logging(
        settings.log_level,
        log_file=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        force=True,
    )

    exporter = MetricsExporter(
        session_factory=SessionLocal,
        broker=AlpacaAdapter(),
        kill_switch=KillSwitch(),
        halt=HaltController(),
        environment=settings.environment,
        live_enabled=settings.is_live_permitted(),
    )
    run_server(exporter, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
