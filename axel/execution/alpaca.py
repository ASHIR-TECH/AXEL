"""
Alpaca broker adapter for US Equities execution (Phase 6).

Implements the canonical :class:`~axel.execution.broker_base.BrokerAdapter`
contract for both paper and live endpoints. ``mock_mode`` provides a fully
in-memory, deterministic venue used by offline drills and tests, including
fault-injection hooks for ambiguous submit outcomes.

Money is parsed with explicit ``Decimal`` conversion, provider order states are
normalised to the AXEL lifecycle, credentials and payloads are sanitised before
logging, and provider event timestamps are preserved separately from receive
time (spec §9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

from axel.core.clock import Clock, RealClock
from axel.core.config import settings
from axel.core.contracts import Order, RiskDecision
from axel.core.logging import logger
from axel.core.money import to_decimal
from axel.core.types import OrderSide, OrderState, OrderType
from axel.execution.broker_base import (
    AmbiguousSubmissionError,
    BrokerAccount,
    BrokerAdapter,
    BrokerCancelResult,
    BrokerError,
    BrokerFill,
    BrokerHealth,
    BrokerOrder,
    BrokerRejectedError,
    NonRetryableBrokerError,
    OrderResult,
    PositionInfo,
    RetryableBrokerError,
    parse_decimal,
    parse_decimal_or_none,
    sanitize_payload,
)

# Alpaca statuses -> AXEL canonical lifecycle.
_STATUS_MAP: dict[str, OrderState] = {
    "new": OrderState.ACKNOWLEDGED,
    "accepted": OrderState.ACKNOWLEDGED,
    "pending_new": OrderState.ACKNOWLEDGED,
    "accepted_for_bidding": OrderState.ACKNOWLEDGED,
    "partially_filled": OrderState.PARTIALLY_FILLED,
    "filled": OrderState.FILLED,
    "canceled": OrderState.CANCELED,
    "expired": OrderState.EXPIRED,
    "rejected": OrderState.REJECTED,
    "pending_cancel": OrderState.CANCEL_PENDING,
    "pending_replace": OrderState.CANCEL_PENDING,
    "done_for_day": OrderState.EXPIRED,
    "stopped": OrderState.UNKNOWN,
    "suspended": OrderState.UNKNOWN,
    "calculated": OrderState.UNKNOWN,
    "held": OrderState.UNKNOWN,
}

_OPEN_STATES = frozenset(
    {
        OrderState.CREATED,
        OrderState.APPROVED,
        OrderState.SUBMITTED,
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN,
        OrderState.ACCEPTED,
    }
)


def normalize_state(status: str | None) -> OrderState:
    """Map a provider status string to the canonical AXEL order state."""
    if not status:
        return OrderState.UNKNOWN
    return _STATUS_MAP.get(status.strip().lower(), OrderState.UNKNOWN)


class AlpacaAdapter(BrokerAdapter):
    """
    Alpaca Markets API adapter.

    Enforces idempotency using ``client_order_id``. Operates in mock mode when
    ``mock_mode=True`` or when credentials are unset.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        mock_mode: bool = False,
        clock: Clock | None = None,
        account_id: str | None = None,
    ):
        self.clock = clock or RealClock()
        self.api_key = api_key or (
            settings.alpaca_api_key.get_secret_value() if settings.alpaca_api_key else None
        )
        self.secret_key = secret_key or (
            settings.alpaca_secret_key.get_secret_value() if settings.alpaca_secret_key else None
        )
        self.base_url = base_url or settings.alpaca_base_url
        self.mock_mode = mock_mode or not (self.api_key and self.secret_key)
        self.account_id = account_id or ("mock-account" if self.mock_mode else "")

        # Mock in-memory state for offline drills. ``_mock_balance`` and
        # ``_mock_positions`` keep their original shapes for backwards compat.
        self._mock_balance: dict[str, float] = {
            "total_equity": 100_000.0,
            "cash": 100_000.0,
            "buying_power": 200_000.0,
        }
        self._mock_positions: dict[str, PositionInfo] = {}
        self._mock_orders: dict[str, BrokerOrder] = {}
        self._mock_broker_index: dict[str, str] = {}  # broker_order_id -> client_order_id
        self._mock_fills: list[BrokerFill] = []

        # Fault injection used by failure drills/tests (mock mode only).
        # One of: None, "ambiguous_unsent", "ambiguous_sent", "transport",
        # "rejected", "nonretryable".
        self.fail_next_submit: str | None = None
        self.health_ok: bool = True

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.secret_key or "",
            "Content-Type": "application/json",
        }

    def _raw_response(self, data: object) -> dict:
        sanitized = sanitize_payload(
            data, secrets=(self.api_key or "", self.secret_key or "")
        )
        if isinstance(sanitized, dict):
            return sanitized
        return {"payload": sanitized}

    def _broker_order_from_payload(self, data: dict) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id=str(data.get("id", "")),
            client_order_id=data.get("client_order_id"),
            symbol=str(data.get("symbol", "")),
            side=OrderSide(data["side"]) if data.get("side") else OrderSide.BUY,
            qty=parse_decimal(data.get("qty"), field_name="qty"),
            filled_qty=parse_decimal_or_none(data.get("filled_qty")) or Decimal(0),
            filled_avg_price=parse_decimal_or_none(data.get("filled_avg_price")),
            state=normalize_state(data.get("status")),
            order_type=OrderType(data["type"]) if data.get("type") in {t.value for t in OrderType} else OrderType.LIMIT,
            limit_price=parse_decimal_or_none(data.get("limit_price")),
            submitted_at=_parse_ts(data.get("submitted_at") or data.get("created_at")),
            updated_at=_parse_ts(data.get("updated_at")),
            raw=self._raw_response(data),
        )

    # ── Read-only ───────────────────────────────────────────────────────────
    def get_account_summary(self) -> dict[str, float]:
        if self.mock_mode:
            return dict(self._mock_balance)

        url = f"{self.base_url}/v2/account"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return {
                "total_equity": float(data.get("equity", 0.0)),
                "cash": float(data.get("cash", 0.0)),
                "buying_power": float(data.get("buying_power", 0.0)),
            }

    def get_account(self) -> BrokerAccount:
        if self.mock_mode:
            return BrokerAccount(
                account_id=self.account_id,
                total_equity=to_decimal(self._mock_balance["total_equity"]),
                cash=to_decimal(self._mock_balance["cash"]),
                buying_power=to_decimal(self._mock_balance["buying_power"]),
                as_of=self.clock.now(),
            )

        url = f"{self.base_url}/v2/account"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        account_id = str(data.get("account_number") or data.get("id") or self.account_id)
        return BrokerAccount(
            account_id=account_id,
            total_equity=parse_decimal(data.get("equity"), field_name="equity"),
            cash=parse_decimal(data.get("cash"), field_name="cash"),
            buying_power=parse_decimal(data.get("buying_power"), field_name="buying_power"),
            currency=str(data.get("currency", "USD")),
            as_of=self.clock.now(),
        )

    def get_positions(self) -> list[PositionInfo]:
        if self.mock_mode:
            return list(self._mock_positions.values())

        url = f"{self.base_url}/v2/positions"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            positions = []
            for item in resp.json():
                side = OrderSide.BUY if float(item.get("qty", 0.0)) >= 0 else OrderSide.SELL
                positions.append(
                    PositionInfo(
                        symbol=item["symbol"],
                        side=side,
                        qty=abs(float(item["qty"])),
                        current_price=float(item.get("current_price", 0.0)),
                        market_value=float(item.get("market_value", 0.0)),
                        cost_basis=float(item.get("cost_basis", 0.0)),
                        unrealized_pnl=float(item.get("unrealized_pl", 0.0)),
                    )
                )
            return positions

    def get_open_orders(self) -> list[BrokerOrder]:
        if self.mock_mode:
            return [o for o in self._mock_orders.values() if o.state in _OPEN_STATES]

        url = f"{self.base_url}/v2/orders"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers(), params={"status": "open", "limit": 500})
            resp.raise_for_status()
            return [self._broker_order_from_payload(item) for item in resp.json()]

    def get_order(self, broker_order_id: str) -> BrokerOrder | None:
        if self.mock_mode:
            client_oid = self._mock_broker_index.get(broker_order_id)
            return self._mock_orders.get(client_oid) if client_oid else None

        url = f"{self.base_url}/v2/orders/{broker_order_id}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers())
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return self._broker_order_from_payload(resp.json())

    def find_order_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        if self.mock_mode:
            return self._mock_orders.get(client_order_id)

        url = f"{self.base_url}/v2/orders:by_client_order_id"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers(), params={"client_order_id": client_order_id})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return self._broker_order_from_payload(resp.json())

    def list_fills(self, since: datetime | None = None) -> list[BrokerFill]:
        if self.mock_mode:
            if since is None:
                return list(self._mock_fills)
            return [f for f in self._mock_fills if f.event_time is None or f.event_time >= since]

        params: dict[str, str] = {"activity_types": "FILL", "direction": "asc", "page_size": "100"}
        if since is not None:
            params["after"] = since.isoformat()
        url = f"{self.base_url}/v2/account/activities"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            fills: list[BrokerFill] = []
            for item in resp.json():
                fills.append(
                    BrokerFill(
                        broker_order_id=str(item.get("order_id", "")),
                        symbol=str(item.get("symbol", "")),
                        side=OrderSide.BUY if str(item.get("side", "buy")) == "buy" else OrderSide.SELL,
                        qty=parse_decimal(item.get("qty"), field_name="qty"),
                        price=parse_decimal(item.get("price"), field_name="price"),
                        broker_fill_id=str(item.get("id")) if item.get("id") else None,
                        event_time=_parse_ts(item.get("transaction_time")),
                        received_at=self.clock.now(),
                        venue="alpaca",
                    )
                )
            return fills

    def health_check(self) -> BrokerHealth:
        now = self.clock.now()
        if self.mock_mode:
            return BrokerHealth(
                healthy=self.health_ok,
                checked_at=now,
                latency_ms=0.0,
                detail=None if self.health_ok else "mock broker marked unhealthy",
            )

        url = f"{self.base_url}/v2/account"
        started = self.clock.now()
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=self._headers())
            latency = (self.clock.now() - started).total_seconds() * 1000.0
            return BrokerHealth(
                healthy=resp.status_code == 200,
                checked_at=now,
                latency_ms=latency,
                detail=None if resp.status_code == 200 else f"HTTP {resp.status_code}",
            )
        except httpx.HTTPError as exc:  # pragma: no cover - network path
            return BrokerHealth(healthy=False, checked_at=now, detail=type(exc).__name__)

    # ── Mutating ────────────────────────────────────────────────────────────
    def _execute_order(self, order: Order, risk_decision: RiskDecision) -> OrderResult:
        if self.mock_mode:
            return self._execute_mock_order(order)

        url = f"{self.base_url}/v2/orders"
        payload = {
            "symbol": order.symbol,
            "qty": str(order.qty),
            "side": order.side.value,
            "type": order.type.value,
            "time_in_force": order.time_in_force.value,
            "client_order_id": order.client_order_id,
        }
        if order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        if order.stop_price is not None:
            payload["stop_price"] = str(order.stop_price)

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, headers=self._headers(), json=payload)
        except httpx.TimeoutException as exc:
            # The request may have reached the venue; the outcome is unknown.
            raise AmbiguousSubmissionError(
                f"Alpaca submit timed out for {order.client_order_id}; outcome unknown."
            ) from exc
        except httpx.TransportError as exc:
            raise RetryableBrokerError(
                f"Alpaca transport error for {order.client_order_id}: {type(exc).__name__}."
            ) from exc

        if resp.status_code >= 500:
            raise RetryableBrokerError(f"Alpaca server error {resp.status_code} on submit.")
        if 400 <= resp.status_code < 500:
            detail = self._raw_response(_safe_json(resp))
            logger.warning(
                "Alpaca rejected order.",
                extra={"client_order_id": order.client_order_id, "status": resp.status_code, "detail": detail},
            )
            raise BrokerRejectedError(
                f"Alpaca rejected {order.client_order_id}: HTTP {resp.status_code}."
            )

        data = resp.json()
        return OrderResult(
            client_order_id=order.client_order_id,
            broker_order_id=data.get("id"),
            state=normalize_state(data.get("status")),
            symbol=data.get("symbol", order.symbol),
            side=order.side,
            qty=float(parse_decimal(data.get("qty", order.qty), field_name="qty")),
            filled_qty=float(parse_decimal_or_none(data.get("filled_qty")) or Decimal(0)),
            filled_avg_price=(
                float(parse_decimal(data["filled_avg_price"], field_name="filled_avg_price"))
                if data.get("filled_avg_price")
                else None
            ),
            created_at=self.clock.now(),
            raw_response=self._raw_response(data),
        )

    def _execute_mock_order(self, order: Order) -> OrderResult:
        fault = self.fail_next_submit
        self.fail_next_submit = None

        if fault == "transport":
            raise RetryableBrokerError(f"mock transport failure for {order.client_order_id}")
        if fault == "rejected":
            raise BrokerRejectedError(f"mock rejection for {order.client_order_id}")
        if fault == "nonretryable":
            raise NonRetryableBrokerError(f"mock permanent failure for {order.client_order_id}")
        if fault == "ambiguous_sent":
            self._record_mock_order(order)
            raise AmbiguousSubmissionError(
                f"mock ambiguous submit for {order.client_order_id} (may have reached venue)"
            )
        if fault == "ambiguous_unsent":
            raise AmbiguousSubmissionError(f"mock ambiguous submit for {order.client_order_id}")

        # Idempotent: an identical client_order_id returns the recorded order.
        existing = self._mock_orders.get(order.client_order_id)
        if existing is not None:
            return _to_order_result(existing)

        return _to_order_result(self._record_mock_order(order))

    def _record_mock_order(self, order: Order) -> BrokerOrder:
        broker_order_id = f"alpaca_mock_{order.client_order_id[:8]}"
        broker_order = BrokerOrder(
            broker_order_id=broker_order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            qty=to_decimal(order.qty),
            filled_qty=Decimal(0),
            state=OrderState.ACKNOWLEDGED,
            order_type=order.type,
            limit_price=to_decimal(order.limit_price) if order.limit_price is not None else None,
            submitted_at=self.clock.now(),
            updated_at=self.clock.now(),
        )
        self._mock_orders[order.client_order_id] = broker_order
        self._mock_broker_index[broker_order_id] = order.client_order_id
        return broker_order

    def simulate_fill(
        self,
        client_order_id: str,
        *,
        qty: Decimal | float | str,
        price: Decimal | float | str,
        fee: Decimal | float | str = "0",
        partial: bool = False,
    ) -> BrokerFill:
        """Mock-only: record a fill against a working order and update state."""
        if not self.mock_mode:
            raise BrokerError("simulate_fill is only available in mock mode.")
        broker_order = self._mock_orders.get(client_order_id)
        if broker_order is None:
            raise BrokerError(f"No mock order {client_order_id!r} to fill.")

        fill_qty = to_decimal(qty)
        fill = BrokerFill(
            broker_order_id=broker_order.broker_order_id,
            symbol=broker_order.symbol,
            side=broker_order.side,
            qty=fill_qty,
            price=to_decimal(price),
            broker_fill_id=f"fill_{len(self._mock_fills) + 1}",
            client_order_id=client_order_id,
            fee=to_decimal(fee),
            event_time=self.clock.now(),
            received_at=self.clock.now(),
            venue="alpaca",
        )
        self._mock_fills.append(fill)

        new_filled = broker_order.filled_qty + fill_qty
        remaining = broker_order.qty - new_filled
        new_state = (
            OrderState.PARTIALLY_FILLED
            if partial and remaining > 0
            else OrderState.FILLED
        )
        self._mock_orders[client_order_id] = BrokerOrder(
            broker_order_id=broker_order.broker_order_id,
            client_order_id=broker_order.client_order_id,
            symbol=broker_order.symbol,
            side=broker_order.side,
            qty=broker_order.qty,
            filled_qty=new_filled,
            filled_avg_price=fill.price,
            state=new_state,
            order_type=broker_order.order_type,
            limit_price=broker_order.limit_price,
            submitted_at=broker_order.submitted_at,
            updated_at=self.clock.now(),
        )
        self._apply_mock_position(fill)
        return fill

    def _apply_mock_position(self, fill: BrokerFill) -> None:
        existing = self._mock_positions.get(fill.symbol)
        signed = fill.qty if fill.side is OrderSide.BUY else -fill.qty
        prior_qty = 0.0
        prior_price = float(fill.price)
        if existing is not None:
            prior_qty = existing.qty if existing.side is OrderSide.BUY else -existing.qty
            prior_price = existing.current_price or float(fill.price)
        new_qty = prior_qty + float(signed)
        if new_qty == 0:
            self._mock_positions.pop(fill.symbol, None)
            return
        self._mock_positions[fill.symbol] = PositionInfo(
            symbol=fill.symbol,
            side=OrderSide.BUY if new_qty > 0 else OrderSide.SELL,
            qty=abs(new_qty),
            current_price=prior_price,
            market_value=abs(new_qty) * prior_price,
            cost_basis=abs(new_qty) * float(fill.price),
            unrealized_pnl=(prior_price - float(fill.price)) * new_qty,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        return self.cancel(client_order_id).accepted

    def cancel(self, client_order_id: str) -> BrokerCancelResult:
        if self.mock_mode:
            broker_order = self._mock_orders.get(client_order_id)
            if broker_order is None:
                return BrokerCancelResult(
                    client_order_id=client_order_id, accepted=False, detail="order not found"
                )
            if broker_order.state not in _OPEN_STATES:
                return BrokerCancelResult(
                    client_order_id=client_order_id,
                    accepted=False,
                    broker_order_id=broker_order.broker_order_id,
                    state=broker_order.state,
                    detail="order is not open",
                )
            self._mock_orders[client_order_id] = BrokerOrder(
                broker_order_id=broker_order.broker_order_id,
                client_order_id=broker_order.client_order_id,
                symbol=broker_order.symbol,
                side=broker_order.side,
                qty=broker_order.qty,
                filled_qty=broker_order.filled_qty,
                filled_avg_price=broker_order.filled_avg_price,
                state=OrderState.CANCELED,
                order_type=broker_order.order_type,
                limit_price=broker_order.limit_price,
                submitted_at=broker_order.submitted_at,
                updated_at=self.clock.now(),
            )
            return BrokerCancelResult(
                client_order_id=client_order_id,
                accepted=True,
                broker_order_id=broker_order.broker_order_id,
                state=OrderState.CANCELED,
            )

        found = self.find_order_by_client_id(client_order_id)
        if found is None:
            return BrokerCancelResult(client_order_id=client_order_id, accepted=False, detail="order not found")
        with httpx.Client(timeout=10.0) as client:
            del_resp = client.delete(f"{self.base_url}/v2/orders/{found.broker_order_id}", headers=self._headers())
            accepted = del_resp.status_code in (200, 204)
            return BrokerCancelResult(
                client_order_id=client_order_id,
                accepted=accepted,
                broker_order_id=found.broker_order_id,
                state=OrderState.CANCEL_PENDING if accepted else found.state,
                detail=None if accepted else f"HTTP {del_resp.status_code}",
            )

    def cancel_all_orders(self) -> int:
        if self.mock_mode:
            cancelled = 0
            for client_oid in list(self._mock_orders.keys()):
                result = self.cancel(client_oid)
                if result.accepted:
                    cancelled += 1
            return cancelled

        url = f"{self.base_url}/v2/orders"
        with httpx.Client(timeout=10.0) as client:
            resp = client.delete(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return len(data) if isinstance(data, list) else 0


# ── Module helpers ────────────────────────────────────────────────────────────


def _to_order_result(broker_order: BrokerOrder) -> OrderResult:
    return OrderResult(
        client_order_id=broker_order.client_order_id or "",
        broker_order_id=broker_order.broker_order_id,
        state=broker_order.state,
        symbol=broker_order.symbol,
        side=broker_order.side,
        qty=float(broker_order.qty),
        filled_qty=float(broker_order.filled_qty),
        filled_avg_price=float(broker_order.filled_avg_price) if broker_order.filled_avg_price is not None else None,
        created_at=broker_order.submitted_at or datetime.now(UTC),
        raw_response=broker_order.raw,
    )


def _parse_ts(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        # Python 3.11+ fromisoformat accepts a trailing 'Z'.
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _safe_json(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except ValueError:  # pragma: no cover - non-JSON error body
        return {"text": resp.text[:500]}
