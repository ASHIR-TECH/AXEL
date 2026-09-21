"""
Alpaca broker adapter for US Equities execution.
Supports both paper and live endpoints with built-in mock mode for offline testing.
"""

import httpx

from axel.core.clock import Clock, RealClock
from axel.core.config import settings
from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderState
from axel.execution.broker_base import BrokerAdapter, OrderResult, PositionInfo


class AlpacaAdapter(BrokerAdapter):
    """
    Alpaca Markets API adapter.
    Enforces idempotency using client_order_id.
    Operates in mock mode if mock_mode=True or if credentials are unset.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        mock_mode: bool = False,
        clock: Clock | None = None,
    ):
        self.clock = clock or RealClock()
        self.api_key = api_key or (settings.alpaca_api_key.get_secret_value() if settings.alpaca_api_key else None)
        self.secret_key = secret_key or (settings.alpaca_secret_key.get_secret_value() if settings.alpaca_secret_key else None)
        self.base_url = base_url or settings.alpaca_base_url
        self.mock_mode = mock_mode or not (self.api_key and self.secret_key)

        # Mock in-memory state for offline drills
        self._mock_balance: dict[str, float] = {
            "total_equity": 100_000.0,
            "cash": 100_000.0,
            "buying_power": 200_000.0,
        }
        self._mock_positions: dict[str, PositionInfo] = {}
        self._mock_orders: dict[str, OrderResult] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.secret_key or "",
            "Content-Type": "application/json",
        }

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

    def _execute_order(self, order: Order, risk_decision: RiskDecision) -> OrderResult:
        if self.mock_mode:
            # Check idempotency in mock
            if order.client_order_id in self._mock_orders:
                return self._mock_orders[order.client_order_id]

            result = OrderResult(
                client_order_id=order.client_order_id,
                broker_order_id=f"alpaca_mock_{order.client_order_id[:8]}",
                state=OrderState.ACCEPTED,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                filled_qty=0.0,
                filled_avg_price=None,
                created_at=self.clock.now(),
            )
            self._mock_orders[order.client_order_id] = result
            return result

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

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()

            status_str = data.get("status", "accepted")
            state_map = {
                "new": OrderState.ACCEPTED,
                "accepted": OrderState.ACCEPTED,
                "partially_filled": OrderState.PARTIALLY_FILLED,
                "filled": OrderState.FILLED,
                "canceled": OrderState.CANCELED,
                "expired": OrderState.EXPIRED,
                "rejected": OrderState.REJECTED,
            }
            order_state = state_map.get(status_str, OrderState.ACCEPTED)

            return OrderResult(
                client_order_id=order.client_order_id,
                broker_order_id=data.get("id"),
                state=order_state,
                symbol=data.get("symbol", order.symbol),
                side=order.side,
                qty=float(data.get("qty", order.qty)),
                filled_qty=float(data.get("filled_qty", 0.0)),
                filled_avg_price=float(data["filled_avg_price"]) if data.get("filled_avg_price") else None,
                created_at=self.clock.now(),
                raw_response=data,
            )

    def cancel_order(self, client_order_id: str) -> bool:
        if self.mock_mode:
            if client_order_id in self._mock_orders:
                prev = self._mock_orders[client_order_id]
                self._mock_orders[client_order_id] = OrderResult(
                    client_order_id=prev.client_order_id,
                    broker_order_id=prev.broker_order_id,
                    state=OrderState.CANCELED,
                    symbol=prev.symbol,
                    side=prev.side,
                    qty=prev.qty,
                    filled_qty=prev.filled_qty,
                    created_at=prev.created_at,
                )
                return True
            return False

        # Alpaca lookup by client_order_id
        url = f"{self.base_url}/v2/orders:by_client_order_id"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers(), params={"client_order_id": client_order_id})
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            broker_id = resp.json().get("id")
            if not broker_id:
                return False

            del_resp = client.delete(f"{self.base_url}/v2/orders/{broker_id}", headers=self._headers())
            return del_resp.status_code in (200, 204)

    def cancel_all_orders(self) -> int:
        if self.mock_mode:
            cancelled = 0
            for k, ord_res in list(self._mock_orders.items()):
                if ord_res.state in (OrderState.ACCEPTED, OrderState.SUBMITTED):
                    self._mock_orders[k] = OrderResult(
                        client_order_id=ord_res.client_order_id,
                        broker_order_id=ord_res.broker_order_id,
                        state=OrderState.CANCELED,
                        symbol=ord_res.symbol,
                        side=ord_res.side,
                        qty=ord_res.qty,
                        created_at=ord_res.created_at,
                    )
                    cancelled += 1
            return cancelled

        url = f"{self.base_url}/v2/orders"
        with httpx.Client(timeout=10.0) as client:
            resp = client.delete(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return len(data) if isinstance(data, list) else 0
