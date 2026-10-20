"""
Base abstract interface for crypto venue adapters (Phase 7).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderType, RiskVerdict
from axel.crypto.schemas import (
    CryptoInstrument,
    CryptoOrderBookRecord,
    CryptoVenueHealth,
    FundingRateRecord,
)


@dataclass(frozen=True)
class CryptoOrderResult:
    """Canonical broker order result."""

    venue: str
    symbol: str
    client_order_id: str
    broker_order_id: str
    side: OrderSide
    order_type: OrderType
    price: Decimal | None
    qty: Decimal
    filled_qty: Decimal
    status: str
    created_at: datetime
    raw_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class CryptoPosition:
    """Current open position on a crypto venue."""

    venue: str
    symbol: str
    side: OrderSide
    qty: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    liquidation_price: Decimal | None = None
    leverage: Decimal = Decimal("1.0")


class CryptoExchangeAdapter(ABC):
    """
    Abstract base class for crypto exchange integrations.
    Built-in security:
    1. Zero leverage allowed (enforced in submit_order).
    2. Withdrawals are strictly unsupported / forbidden.
    3. Orders require an APPROVED RiskDecision.
    """

    def __init__(self, venue_name: str) -> None:
        self.venue_name = venue_name

    @abstractmethod
    def get_instruments(self) -> list[CryptoInstrument]:
        """Fetch supported trading pairs."""

    @abstractmethod
    def get_order_book(self, symbol: str) -> CryptoOrderBookRecord:
        """Fetch current L2 order book snapshot."""

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> FundingRateRecord | None:
        """Fetch current funding rate for perpetuals."""

    @abstractmethod
    def get_mark_price(self, symbol: str) -> Decimal:
        """Fetch mark / index price."""

    @abstractmethod
    def get_account_balance(self) -> dict[str, Decimal]:
        """Fetch asset balances (e.g. {'USDT': Decimal('10000.0')})."""

    @abstractmethod
    def get_positions(self) -> list[CryptoPosition]:
        """Fetch currently open positions."""

    @abstractmethod
    def health_check(self) -> CryptoVenueHealth:
        """Query venue health, latency, and operational status."""

    def submit_order(
        self,
        order: Order,
        risk_decision: RiskDecision,
        leverage: Decimal | float = 1,
    ) -> CryptoOrderResult:
        """
        Base guard: verifies deterministic risk approval and NO-LEVERAGE rule.
        """
        if not risk_decision.approved or risk_decision.verdict is not RiskVerdict.APPROVED:
            raise PermissionError(
                f"Execution blocked on {self.venue_name}: RiskDecision is {risk_decision.verdict.value}"
            )

        # Enforce No-Leverage Rule (PRD Phase 7 requirement)
        lev = Decimal(str(leverage))
        if lev > Decimal("1.0"):
            raise ValueError(
                f"NO-LEVERAGE RULE VIOLATION: Requested leverage {lev} > 1.0 on {self.venue_name}. "
                "Phase 7 initial Crypto section operates without leverage."
            )

        if Decimal(str(order.qty)) <= Decimal(0):
            raise ValueError(f"Order qty must be positive, got {order.qty}")

        return self._execute_order(order, risk_decision)

    @abstractmethod
    def _execute_order(self, order: Order, risk_decision: RiskDecision) -> CryptoOrderResult:
        """Venue-specific order placement."""

    @abstractmethod
    def cancel_order(self, client_order_id: str, symbol: str) -> bool:
        """Cancel an open order."""

    def withdraw(self, *args: Any, **kwargs: Any) -> None:
        """Withdrawals are strictly forbidden on all trading adapters."""
        raise PermissionError("Withdrawal capabilities are permanently disabled on AXEL adapters.")
