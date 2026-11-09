"""Report paper-soak continuity from worker heartbeats.

This is read-only evidence. It never changes trading mode or the promotion gate.
"""

from __future__ import annotations

import argparse

from axel.core.config import settings
from axel.db.session import SessionLocal
from axel.runtime.heartbeat import continuity_report, covered_days

REQUIRED_WORKERS = ("ingest-worker", "agent-worker", "core-worker")


def main() -> int:
    parser = argparse.ArgumentParser(description="Report paper-soak continuity")
    parser.add_argument(
        "--minimum-days",
        type=int,
        default=56,
        help="coverage required by the promotion gate (default: %(default)s)",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        reports = continuity_report(session)
        covered = covered_days(session, workers=REQUIRED_WORKERS)

    if not reports:
        print("No worker heartbeats recorded yet. Start the paper runtime first.")
        return 1

    print(f"{'worker':<18}{'cycles':>10}{'gaps':>7}{'downtime_h':>12}{'covered_d':>12}{'ratio':>8}")
    for report in reports:
        print(
            f"{report.worker:<18}{report.cycles:>10}{report.gap_count:>7}"
            f"{report.downtime_seconds / 3600:>12.2f}"
            f"{report.covered_days:>12.2f}"
            f"{report.coverage_ratio * 100:>7.1f}%"
        )

    print()
    print(f"Required workers: {', '.join(REQUIRED_WORKERS)}")
    print(f"Shortest covered duration: {covered:.2f} days")
    print(f"Gate requirement: {args.minimum_days} days")
    print(f"Environment: {settings.environment} (paper runtime is expected)")
    if covered >= args.minimum_days:
        print("Coverage requirement met. Promotion still requires trades, calibration,")
        print("slippage, reconciliation, drills and human sign-off.")
        return 0
    print(f"Coverage short by {args.minimum_days - covered:.2f} days.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
