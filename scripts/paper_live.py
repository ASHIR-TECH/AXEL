# --- 56-day Alpaca paper soak: live execution ---
#
# Status probe for the official 56-day run. It only reads the Alpaca paper
# orders and reports how many filled and how many are still working.

from dataclasses import dataclass
from datetime import UTC, datetime
from os import environ

import httpx

ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
SOAK_STARTED_AT = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
WORKING_STATES = ("new", "accepted", "held", "pending_new", "partially_filled")
FILLED_STATES = ("filled", "partially_filled")


def _headers() -> dict[str, str]:
    """Build auth headers from the environment at call time.

    Deferred so unit tests can set the key before invoking :func:`main`.
    """
    return {
        "Authorization": f"Bearer {environ['ALPACA_PAPER_API_KEY']}",
        "Content-Type": "application/json",
    }


@dataclass
class PaperState:
    """Snapshot of the running 56-day paper test."""

    started_at: datetime
    filled_orders: int
    unfilled_deferred: int


def fetch_state() -> PaperState:
    """Read recent Alpaca paper orders (read-only) and summarise them."""
    response = httpx.get(
        f"{ALPACA_BASE_URL}/v2/orders",
        params={"status": "all", "limit": 500, "direction": "desc"},
        headers=_headers(),
        timeout=15,
    )
    orders = response.json()

    working = sum(1 for order in orders if order.get("status") in WORKING_STATES)
    filled = sum(1 for order in orders if order.get("status") in FILLED_STATES)
    return PaperState(
        started_at=SOAK_STARTED_AT,
        filled_orders=filled,
        unfilled_deferred=working,
    )


def main() -> int:
    """Print the live state and exit non-zero if anything looks off."""
    try:
        state = fetch_state()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to fetch state: {exc!r}")
        return 2

    print(f"started_at={state.started_at.isoformat()}Z")
    print(f"filled_orders={state.filled_orders}")
    print(f"unfilled_deferred={state.unfilled_deferred}")

    if state.unfilled_deferred > 20:
        print("ALARM: too many unfilled orders")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
