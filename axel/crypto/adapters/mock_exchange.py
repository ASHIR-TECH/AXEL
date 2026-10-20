"""
In-memory deterministic crypto exchange simulator for Phase 7 drills, testing, and replay.
Supports multi-venue simulation (Binance, Bybit, OKX), failure injection, and arbitrage drills.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide
from axel.crypto.adapters.base import (
    CryptoExchangeAdapter,
    CryptoOrderResult,
    CryptoPosition,
)
from axel.crypto.schemas import (
    CryptoInstrument,
    CryptoOrderBookRecord,
    CryptoVenueHealth,
    FundingRateRecord,
)
from axel.crypto.types import CryptoInstrumentType, VenueStatus
from axel.data.schemas import Provenance, sha256_text


class MockCryptoExchange(CryptoExchangeAdapter):
    """
    Mock exchange simulator for deterministic testing and replay.
    """

    def __init__(
        self,
        venue_name: str = "binance_mock",
        initial_balances: dict[str, Decimal] | None = None,
    ) -> None:
        super().__init__(venue_name=venue_name)
        self.balances: dict[str, Decimal] = initial_balances or {
            "USDT": Decimal("100000.0"),
            "BTC": Decimal("5.0"),
            "ETH": Decimal("50.0"),
        }
        self.positions: dict[str, CryptoPosition] = {}
        self.orders: dict[str, CryptoOrderResult] = {}
        self.instruments: dict[str, CryptoInstrument] = {}
        self.order_books: dict[str, CryptoOrderBookRecord] = {}
        self.funding_rates: dict[str, FundingRateRecord] = {}
        self.mark_prices: dict[str, Decimal] = {
            "BTC/USDT": Decimal("50000.0"),
            "ETH/USDT": Decimal("3000.0"),
            "BTC/USDT:USDT": Decimal("50010.0"),
        }

        # Failure injection flags
        self.fail_next_order: bool = False
        self.fail_order_reason: str = "Simulated venue reject"
        self.is_maintenance: bool = False
        self.latency_ms: float = 12.0
        self.stale_data_seconds: float = 0.0
        self.stale_book: bool = False
        self.rejection_rate: float = 0.0

        # Initialize default instruments
        self._seed_default_instruments()

    def _seed_default_instruments(self) -> None:
        btc_spot = CryptoInstrument(
            symbol="BTC/USDT",
            venue=self.venue_name,
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=CryptoInstrumentType.SPOT,
            min_qty=Decimal("0.0001"),
            tick_size=Decimal("0.10"),
        )
        btc_perp = CryptoInstrument(
            symbol="BTC/USDT:USDT",
            venue=self.venue_name,
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=CryptoInstrumentType.PERPETUAL,
            min_qty=Decimal("0.0001"),
            tick_size=Decimal("0.10"),
            funding_interval_hours=8,
        )
        self.instruments[btc_spot.symbol] = btc_spot
        self.instruments[btc_perp.symbol] = btc_perp

    def get_instruments(self) -> list[CryptoInstrument]:
        return list(self.instruments.values())

    def set_order_book(self, symbol: str, book: CryptoOrderBookRecord) -> None:
        self.order_books[symbol] = book
        self.mark_prices[symbol] = book.mid_price

    def get_order_book(self, symbol: str) -> CryptoOrderBookRecord:
        if symbol in self.order_books:
            book = self.order_books[symbol]
            if self.stale_book:
                # Simulate stale orderbook
                stale_time = datetime.now(UTC) - timedelta(seconds=60)
                return CryptoOrderBookRecord(
                    venue=book.venue,
                    symbol=book.symbol,
                    event_time=stale_time,
                    available_at=stale_time,
                    bids=book.bids,
                    asks=book.asks,
                    provenance=book.provenance,
                )
            return book

        # Generate default synthetic book
        mid = self.mark_prices.get(symbol, Decimal("50000.0"))
        now = datetime.now(UTC)
        prov = Provenance(
            source=self.venue_name,
            source_id="synthetic",
            source_hash=sha256_text(f"{self.venue_name}:{symbol}:{now}"),
            ingestion_timestamp=now,
        )
        spread = Decimal("2.0")
        bids = tuple((mid - Decimal(i * 2) - spread / Decimal(2), Decimal("1.5")) for i in range(1, 10))
        asks = tuple((mid + Decimal(i * 2) + spread / Decimal(2), Decimal("1.5")) for i in range(1, 10))
        return CryptoOrderBookRecord(
            venue=self.venue_name,
            symbol=symbol,
            event_time=now,
            available_at=now,
            bids=bids,
            asks=asks,
            provenance=prov,
        )

    def set_funding_rate(self, symbol: str, record: FundingRateRecord) -> None:
        self.funding_rates[symbol] = record

    def get_funding_rate(self, symbol: str) -> FundingRateRecord | None:
        if symbol in self.funding_rates:
            return self.funding_rates[symbol]
        now = datetime.now(UTC)
        prov = Provenance(
            source=self.venue_name,
            source_id="synth_funding",
            source_hash=sha256_text(f"funding:{symbol}"),
            ingestion_timestamp=now,
        )
        return FundingRateRecord(
            venue=self.venue_name,
            symbol=symbol,
            event_time=now,
            available_at=now,
            rate=Decimal("0.0001"),  # 0.01%
            funding_interval_hours=8,
            predicted_rate=Decimal("0.00012"),
            mark_price=self.mark_prices.get(symbol, Decimal("50000.0")),
            index_price=self.mark_prices.get(symbol, Decimal("50000.0")),
            provenance=prov,
        )

    def get_mark_price(self, symbol: str) -> Decimal:
        return self.mark_prices.get(symbol, Decimal("50000.0"))

    def set_mark_price(self, symbol: str, price: Decimal) -> None:
        self.mark_prices[symbol] = price

    def get_account_balance(self) -> dict[str, Decimal]:
        return dict(self.balances)

    def get_positions(self) -> list[CryptoPosition]:
        return list(self.positions.values())

    def health_check(self) -> CryptoVenueHealth:
        status = VenueStatus.MAINTENANCE if self.is_maintenance else VenueStatus.ACTIVE
        return CryptoVenueHealth(
            venue=self.venue_name,
            status=status,
            latency_ms=self.latency_ms,
            maintenance_mode=self.is_maintenance,
            last_heartbeat=datetime.now(UTC),
            stale_data_seconds=self.stale_data_seconds,
            rejection_rate=self.rejection_rate,
            withdrawal_status_ok=True,
            active_incidents=1 if self.is_maintenance else 0,
        )

    def _execute_order(self, order: Order, risk_decision: RiskDecision) -> CryptoOrderResult:
        if self.fail_next_order:
            self.fail_next_order = False
            return CryptoOrderResult(
                venue=self.venue_name,
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                broker_order_id=f"ord_reject_{order.client_order_id}",
                side=order.side,
                order_type=order.type,
                price=Decimal(str(order.limit_price or self.get_mark_price(order.symbol))),
                qty=Decimal(str(order.qty)),
                filled_qty=Decimal("0.0"),
                status="rejected",
                created_at=datetime.now(UTC),
                raw_response={"error": self.fail_order_reason},
            )

        fill_price = Decimal(str(order.limit_price or self.get_mark_price(order.symbol)))
        qty = Decimal(str(order.qty))
        notional = fill_price * qty

        # Update balances
        quote_asset = "USDT"
        base_asset = order.symbol.split("/")[0]

        if order.side == OrderSide.BUY:
            if self.balances.get(quote_asset, Decimal(0)) < notional:
                return CryptoOrderResult(
                    venue=self.venue_name,
                    symbol=order.symbol,
                    client_order_id=order.client_order_id,
                    broker_order_id=f"mock_{order.client_order_id}",
                    side=order.side,
                    order_type=order.type,
                    price=fill_price,
                    qty=qty,
                    filled_qty=Decimal("0.0"),
                    status="rejected",
                    created_at=datetime.now(UTC),
                    raw_response={"error": "Insufficient quote balance"},
                )
            self.balances[quote_asset] = self.balances.get(quote_asset, Decimal(0)) - notional
            self.balances[base_asset] = self.balances.get(base_asset, Decimal(0)) + qty
        else:
            if self.balances.get(base_asset, Decimal(0)) < qty:
                return CryptoOrderResult(
                    venue=self.venue_name,
                    symbol=order.symbol,
                    client_order_id=order.client_order_id,
                    broker_order_id=f"mock_{order.client_order_id}",
                    side=order.side,
                    order_type=order.type,
                    price=fill_price,
                    qty=qty,
                    filled_qty=Decimal("0.0"),
                    status="rejected",
                    created_at=datetime.now(UTC),
                    raw_response={"error": "Insufficient base balance"},
                )
            self.balances[base_asset] = self.balances.get(base_asset, Decimal(0)) - qty
            self.balances[quote_asset] = self.balances.get(quote_asset, Decimal(0)) + notional

        result = CryptoOrderResult(
            venue=self.venue_name,
            symbol=order.symbol,
            client_order_id=order.client_order_id,
            broker_order_id=f"mock_{order.client_order_id}",
            side=order.side,
            order_type=order.type,
            price=fill_price,
            qty=qty,
            filled_qty=qty,
            status="filled",
            created_at=datetime.now(UTC),
            raw_response={"status": "FILLED", "fills": [{"price": str(fill_price), "qty": str(qty)}]},
        )
        self.orders[order.client_order_id] = result
        return result

    def cancel_order(self, client_order_id: str, symbol: str) -> bool:
        if client_order_id in self.orders:
            res = self.orders[client_order_id]
            if res.status != "filled":
                self.orders[client_order_id] = CryptoOrderResult(
                    venue=res.venue,
                    symbol=res.symbol,
                    client_order_id=res.client_order_id,
                    broker_order_id=res.broker_order_id,
                    side=res.side,
                    order_type=res.order_type,
                    price=res.price,
                    qty=res.qty,
                    filled_qty=res.filled_qty,
                    status="canceled",
                    created_at=res.created_at,
                )
                return True
        return False
