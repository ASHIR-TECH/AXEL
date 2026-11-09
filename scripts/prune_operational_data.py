"""Apply the database retention policy.

Run from cron/systemd-timer once a day. Deletes are batched, so the command is
safe to run while the API and workers are live.
"""

from __future__ import annotations

import argparse

from axel.core.logging import log_event, setup_logging
from axel.db.retention import policy_from_settings, prune
from axel.db.session import SessionLocal, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune AXEL tables by age")
    parser.add_argument("--init-db", action="store_true", help="create tables before pruning")
    args = parser.parse_args()

    setup_logging(force=True)
    if args.init_db:
        init_db()

    policy = policy_from_settings()
    with SessionLocal() as session:
        removed = prune(session, policy)

    total = sum(removed.values())
    for table, count in removed.items():
        print(f"{table:<20} {count:>10}")
    print(f"{'total':<20} {total:>10}")
    log_event(
        "info",
        "retention_pruned",
        f"retention removed {total} rows",
        removed=removed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
