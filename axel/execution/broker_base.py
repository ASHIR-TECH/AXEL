"""
Abstract broker interface and common execution contracts (Phase 6).

Provider-specific SDK/HTTP semantics stay behind this interface. The canonical
contract follows
`docs/AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md` §9, while retaining the
original Phase 1 helpers (``get_account_summary``/``get_positions``) so existing
callers keep working.

Financial values use :class:`~decimal.Decimal`. Adapters must:

* normalise provider states and error codes;
* apply timeouts/backoff to safe read-only calls;
* distinguish retryable, non-retryable and ambiguous submission errors;
* sanitise credentials and payloads before logging;
* preserve provider event time separately from receive time;
* never allow bypass of Trading Core approval.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from axel.core.contracts import Order, RiskDecision
from axel.core.money import to_decimal
from axel.core.types import OrderSide, OrderState, OrderType, RiskVerdict

# ── Error taxonomy ────────────────────────────────────────────────────────────


class BrokerError(Exception):
    """Base class for broker/adapter failures."""


class RetryableBrokerError(BrokerError):
    """A transient failure safe to retry (read-only calls, proven-unsent submits)."""


class NonRetryableBrokerError(BrokerError):
    """A permanent failure (bad credentials, invalid order, permissions)."""


class BrokerRejectedError(NonRetryableBrokerError):
    """The broker explicitly rejected the order/request."""


class AmbiguousSubmissionError(BrokerError):
    """
    The submit request may or may not have reached the broker (timeout, connection
    reset after send). Callers MUST NOT blind-retry; they resolve via
    lookup-by-client-id and reconciliation (spec §7.3).
    """


# ── Canonical records ─────────────────────────────────────────────────────────


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
class BrokerAccount:
    """Read-only broker account snapshot in fixed-point money."""

    account_id: str
    total_equity: Decimal
    cash: Decimal
    buying_power: Decimal
    currency: str = "USD"
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class BrokerOrder:
    """Normalised broker order."""

    broker_order_id: str
    client_order_id: str | None
    symbol: str
    side: OrderSide
    qty: Decimal
    filled_qty: Decimal
    state: OrderState
    order_type: OrderType = OrderType.LIMIT
    limit_price: Decimal | None = None
    filled_avg_price: Decimal | None = None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class BrokerFill:
    """Normalised broker execution/fill."""

    broker_order_id: str
    symbol: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    broker_fill_id: str | None = None
    client_order_id: str | None = None
    fee: Decimal = Decimal(0)
    event_time: datetime | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    venue: str | None = None
    liquidity: str | None = None


@dataclass(frozen=True)
class BrokerHealth:
    """Broker connectivity/health observation."""

    healthy: bool
    checked_at: datetime
    latency_ms: float | None = None
    rate_limit_remaining: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class BrokerCancelResult:
    client_order_id: str
    accepted: bool
    broker_order_id: str | None = None
    state: OrderState | None = None
    detail: str | None = None


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


# ── Helpers ───────────────────────────────────────────────────────────────────

_SENSITIVE_KEY_MARKERS = ("secret", "token", "password", "api_key", "apikey", "authorization")


def sanitize_payload(payload: object, *, secrets: tuple[str, ...] = ()) -> object:
    """
    Recursively redact sensitive keys and known secret values before logging.

    Returns a new structure; the input is never mutated.
    """
    if isinstance(payload, dict):
        clean: dict = {}
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
                clean[key] = "***REDACTED***"
            else:
                clean[key] = sanitize_payload(value, secrets=secrets)
        return clean
    if isinstance(payload, (list, tuple)):
        return [sanitize_payload(item, secrets=secrets) for item in payload]
    if isinstance(payload, str) and payload in secrets and payload:
        return "***REDACTED***"
    return payload


def parse_decimal(value: object, *, field_name: str) -> Decimal:
    """Parse a provider value to Decimal, raising a non-retryable error on garbage."""
    if value is None or value == "":
        raise NonRetryableBrokerError(f"Missing broker field {field_name!r}.")
    try:
        return to_decimal(value)  # type: ignore[arg-type]
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise NonRetryableBrokerError(
            f"Malformed broker field {field_name!r}: {value!r}."
        ) from exc


def parse_decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return to_decimal(value)  # type: ignore[arg-type]


# ── Adapter interface ─────────────────────────────────────────────────────────


class BrokerAdapter(ABC):
    """
    Abstract broker interface. Enforces that NO order may be submitted without an
    approved :class:`~axel.core.contracts.RiskDecision`.
    """

    # ── Read-only ───────────────────────────────────────────────────────────
    @abstractmethod
    def get_account_summary(self) -> dict[str, float]:
        """Returns dict with keys: total_equity, cash, buying_power (legacy)."""

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        """Returns a fixed-point account snapshot."""

    @abstractmethod
    def get_positions(self) -> list[PositionInfo]:
        """Returns list of active broker positions."""

    @abstractmethod
    def get_open_orders(self) -> list[BrokerOrder]:
        """Returns all currently open/working broker orders."""

    @abstractmethod
    def get_order(self, broker_order_id: str) -> BrokerOrder | None:
        """Look up a single order by broker order id."""

    @abstractmethod
    def find_order_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        """Look up a single order by deterministic client order id."""

    @abstractmethod
    def list_fills(self, since: datetime | None = None) -> list[BrokerFill]:
        """Returns recent fills, optionally since a timestamp."""

    @abstractmethod
    def health_check(self) -> BrokerHealth:
        """Read-only connectivity/health probe."""

    # ── Mutating (approval-guarded) ─────────────────────────────────────────
    def submit_order(self, order: Order, risk_decision: RiskDecision) -> OrderResult:
        """
        Guarded order submission.

        Deterministic verification that the risk decision is explicitly APPROVED,
        and that quantity is positive. The adapter's :meth:`_execute_order` must
        raise :class:`AmbiguousSubmissionError` rather than blindly retrying when
        the outcome is unknown.
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
        """Vendor-specific submission implementation."""

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> bool:
        """Cancel an open order by client order ID (legacy boolean form)."""

    @abstractmethod
    def cancel(self, client_order_id: str) -> BrokerCancelResult:
        """Request cancellation and report the broker's acknowledgement."""

    @abstractmethod
    def cancel_all_orders(self) -> int:
        """Emergency: cancel all open working orders. Returns count cancelled."""
