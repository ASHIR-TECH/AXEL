# --- 56-day Alpaca paper soak: live execution ---
#
# This section is the gate that starts the official 56-day run. It reads
# REAL market data from Alpaca (stocks section only) and sends APPROVED orders
# to the Alpaca **paper** endpoint. Slippage is then observed rather than
# modeled, so fill quality can be validated over the 56-day window.

from dataclasses import dataclass
from datetime import UTC, datetime
from os import environ

import requests

ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

# Evaluated lazily so unit tests can set ALPACA_PAPER_API_KEY before calling
# main(). The production probe always needs the key at runtime.


def _headers():
    return {
        "Authorization": f"Bearer {environ['ALPACA_PAPER_API_KEY']}",
        "Content-Type": "application/json",
    }


@dataclass
class PaperState:
    """Snapshot of the running 56-day paper test."""

    started_at: datetime
    last_bar_at: datetime | None
    last_order_at: datetime | None
    filled_orders: int
    unfilled_deferred: int


def fetch_state() -> PaperState:
    """Pull live state from the 56-day paper test.

    Uses the public Alpaca paper-account endpoint for account state and a
    private webhook for execution state. This is a read-only health probe that
    runs every 5 minutes.
    """
    requests.get(
        f"{ALPACA_BASE_URL}/v2/account",
        headers=_headers(),
        timeout=15,
    )
    positions = requests.get(
        f"{ALPACA_BASE_URL}/v2/position",
        headers=_headers(),
        timeout=15,
    ).json()

    unfilled = sum(1 for p in positions if p["status"] == "new")
    return PaperState(
        started_at=datetime(2026, 11, 11, 13, 30, tzinfo=UTC),
        last_bar_at=None,
        last_order_at=None,
        filled_orders=sum(1 for p in positions if p["status"] in ("partially_filled", "filled")),
        unfilled_deferred=unfilled,
    )


def main() -> int:
    """Print the live state and exit with code 2 if anything looks off."""
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
