"""Run the read-only AXEL dashboard API.

The API serves the unchanged terminal UI from ``axel/dashboard/ui`` and the
live JSON/SSE feed under ``/api/v1``.  It is safe to run in a separate
container from the workers because the database is the only shared state.

Examples
--------
    python scripts/run_dashboard_api.py --init-db
    python scripts/run_dashboard_api.py --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse

import uvicorn

from axel.db.session import init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AXEL read-only dashboard API")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8000, help="bind port")
    parser.add_argument("--reload", action="store_true", help="reload on source changes")
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="create tables before starting (local development only; use migrations in production)",
    )
    parser.add_argument("--log-level", default="info", help="uvicorn log level")
    args = parser.parse_args()

    if args.init_db:
        init_db()

    uvicorn.run(
        "axel.dashboard.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
