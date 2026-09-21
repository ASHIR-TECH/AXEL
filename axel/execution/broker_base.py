"""
Abstract broker interface and common execution contracts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderState, RiskVerdict


@dataclass(frozen=True)
class PositionInfo:
    symbol: str
    side: OrderSide
    qty: float
    current_price: float
    market_value: float
    cost_basis: float
    unrealized_pnl: float


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    broker_order_id: str | None
    state: OrderState
    symbol: str
    side: OrderSide
    qty: float
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_response: dict | None = None


class BrokerAdapter(ABC):
    """
    Abstract interface for broker interaction.
    Enforces that NO order may be submitted without an approved RiskDecision.
    """

    @abstractmethod
    def get_account_summary(self) -> dict[str, float]:
        """Returns dict with keys: total_equity, cash, buying_power."""

    @abstractmethod
    def get_positions(self) -> list[PositionInfo]:
        """Returns list of active broker positions."""

    def submit_order(self, order: Order, risk_decision: RiskDecision) -> OrderResult:
        """
        Guarded order submission:
        Deterministic verification that risk decision is explicitly APPROVED.
        """
        if not risk_decision.approved or risk_decision.verdict != RiskVerdict.APPROVED:
            raise PermissionError(
                f"EXECUTION BLOCKED: Cannot submit order {order.client_order_id} without "
                f"an APPROVED RiskDecision (Verdict: {risk_decision.verdict})."
            )
        if order.qty <= 0:
            raise ValueError(f"EXECUTION BLOCKED: Order quantity {order.qty} must be > 0.")

        return self._execute_order(order, risk_decision)

    @abstractmethod
    def _execute_order(self, order: Order, risk_decision: RiskDecision) -> OrderResult:
        """Vendor-specific implementation."""

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> bool:
        """Cancel an open order by client order ID."""

    @abstractmethod
    def cancel_all_orders(self) -> int:
        """Emergency function: cancel all open working orders. Returns count cancelled."""
