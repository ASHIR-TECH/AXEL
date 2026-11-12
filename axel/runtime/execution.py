"""Execution backends for the deterministic paper runtime.

The risk engine is the only authority that approves an order. A backend only
carries out an already-approved order:

* :class:`InternalBackend` reproduces the offline deterministic fill used by the
  synthetic runtime (reference price plus a fixed slippage assumption).
* :class:`AlpacaBackend` submits the same order to the Alpaca **paper** endpoint
  and reports the broker's actual fill, so realized slippage is measured instead
  of modeled. It refuses anything that is not clearly paper configuration.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as clock_time
from typing import Protocol
from zoneinfo import ZoneInfo

from axel.core.config import settings
from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, RiskVerdict
from axel.execution.alpaca import AlpacaAdapter

SLIPPAGE_BPS = 2.0
# US regular session, expressed in the exchange's local clock so that the
# 09:30–16:00 window follows daylight saving instead of drifting by an hour.
MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = clock_time(9, 30)
MARKET_CLOSE = clock_time(16, 0)


@dataclass(frozen=True)
class FillReport:
    """One executed (fully or partially) order."""

    client_order_id: str
    broker_order_id: str | None
    side: str
    qty: float
    price: float
    fee: float
    state: str


class ExecutionBackend(Protocol):
    name: str

    def available(self, now: datetime) -> bool:
        """Whether the venue can accept and fill an order right now."""

    def execute(
        self, order: Order, *, reference_price: float, now: datetime
    ) -> FillReport | None:
        """Execute an approved order; ``None`` means nothing filled."""


def market_is_open(now: datetime) -> bool:
    """US regular-hours check in exchange time (holidays defer the order)."""
    stamp = now.astimezone(MARKET_TZ)
    if stamp.weekday() >= 5:
        return False
    return MARKET_OPEN <= stamp.time() <= MARKET_CLOSE


class InternalBackend:
    """Deterministic offline fill used by the synthetic runtime."""

    name = "internal"

    def available(self, now: datetime) -> bool:
        return True

    def execute(
        self, order: Order, *, reference_price: float, now: datetime
    ) -> FillReport | None:
        sign = 1.0 if order.side is OrderSide.BUY else -1.0
        price = reference_price * (1.0 + sign * SLIPPAGE_BPS / 10_000.0)
        return FillReport(
            client_order_id=order.client_order_id,
            broker_order_id=None,
            side=order.side.value,
            qty=float(order.qty),
            price=price,
            fee=0.0,
            state="filled",
        )


class AlpacaBackend:
    """Real paper execution through the Alpaca paper API."""

    name = "alpaca"

    def __init__(
        self,
        adapter: AlpacaAdapter | None = None,
        *,
        poll_seconds: float = 1.0,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not settings.alpaca_paper:
            raise RuntimeError("Alpaca execution requires ALPACA_PAPER=true")
        self.adapter = adapter or AlpacaAdapter()
        base_url = getattr(self.adapter, "base_url", "")
        if "paper-api" not in base_url:
            raise RuntimeError(
                f"refusing non-paper Alpaca endpoint for the paper runtime: {base_url}"
            )
        self.poll_seconds = max(0.1, poll_seconds)
        self.timeout_seconds = max(self.poll_seconds, timeout_seconds)

    def available(self, now: datetime) -> bool:
        return market_is_open(now)

    def _approved(self, order: Order, reference_price: float) -> RiskDecision:
        """A decision contract for an order the deterministic core approved.

        The core has already run :class:`~axel.risk.engine.SectionRiskAgent`.
        This object only satisfies the adapter's fail-closed submission guard.
        """
        return RiskDecision(
            proposal_id=order.proposal_id,
            approved=True,
            verdict=RiskVerdict.APPROVED,
            approved_qty=float(order.qty),
            approved_notional_usd=float(order.qty) * float(reference_price),
        )

    def execute(
        self, order: Order, *, reference_price: float, now: datetime
    ) -> FillReport | None:
        if not self.available(now):
            return None

        result = self.adapter.submit_order(order, self._approved(order, reference_price))
        broker_order_id = result.broker_order_id
        deadline = time.monotonic() + self.timeout_seconds

        filled_qty = float(result.filled_qty or 0.0)
        filled_price = result.filled_avg_price
        while filled_qty <= 0 or filled_price is None:
            if time.monotonic() >= deadline or broker_order_id is None:
                break
            time.sleep(self.poll_seconds)
            broker_order = self.adapter.get_order(broker_order_id)
            if broker_order is None:
                continue
            filled_qty = float(broker_order.filled_qty or 0.0)
            filled_price = (
                float(broker_order.filled_avg_price)
                if broker_order.filled_avg_price is not None
                else None
            )
            if str(broker_order.state).lower().endswith(("rejected", "canceled", "expired")):
                return None

        if filled_qty <= 0 or filled_price is None:
            if broker_order_id is not None:
                # Cancellation is best effort: the unfilled order is reported as
                # no fill either way, and the core re-queues the proposal.
                with contextlib.suppress(Exception):
                    self.adapter.cancel_order(order.client_order_id)
            return None

        fee = 0.0
        return FillReport(
            client_order_id=order.client_order_id,
            broker_order_id=broker_order_id,
            side=order.side.value,
            qty=filled_qty,
            price=filled_price,
            fee=fee,
            state="filled",
        )


def build_backend() -> ExecutionBackend:
    """Return the backend selected by ``PAPER_EXECUTION``."""
    if settings.paper_execution == "alpaca":
        return AlpacaBackend()
    return InternalBackend()


__all__ = [
    "MARKET_CLOSE",
    "MARKET_OPEN",
    "SLIPPAGE_BPS",
    "AlpacaBackend",
    "ExecutionBackend",
    "FillReport",
    "InternalBackend",
    "build_backend",
    "market_is_open",
]
