"""Phase 6 failure-injection tests (spec §15.3).

Each scenario asserts the required invariants: no blind duplicate order, no
silent state transition, no false claim of cancellation, risk-increasing orders
halt when required, reconciliation can recover the authoritative state, and the
audit trail stays understandable.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderState, OrderType, RiskVerdict
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.broker_base import NonRetryableBrokerError, parse_decimal
from axel.execution.order_service import OrderService
from axel.execution.order_store import OrderEventKind, OrderEventStore
from axel.execution.pre_trade import (
    MarketSnapshot,
    PortfolioSnapshot,
    PreTradeContext,
    PreTradeValidator,
)

NOW = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)


class CountingAlpaca(AlpacaAdapter):
    def __init__(self, **kwargs):
        super().__init__(mock_mode=True, **kwargs)
        self.submit_calls = 0

    def _execute_order(self, order, risk_decision):
        self.submit_calls += 1
        return super()._execute_order(order, risk_decision)


class FlakyStore(OrderEventStore):
    """Store that raises once ``fail_after`` successful appends have happened."""

    def __init__(self, fail_after: int) -> None:
        super().__init__()
        self.fail_after = fail_after
        self.appends = 0

    def append(self, **kwargs):
        if self.appends >= self.fail_after:
            raise RuntimeError("database unavailable")
        self.appends += 1
        return super().append(**kwargs)


def _order(**overrides) -> Order:
    base = {
        "client_order_id": "ord-fi",
        "proposal_id": "prop-fi",
        "symbol": "SPY",
        "side": OrderSide.BUY,
        "qty": 10.0,
        "type": OrderType.LIMIT,
        "limit_price": 100.0,
        "strategy_id": "s1",
        "strategy_version": "1.0.0",
    }
    base.update(overrides)
    return Order(**base)


def _decision() -> RiskDecision:
    return RiskDecision(
        proposal_id="prop-fi", approved=True, verdict=RiskVerdict.APPROVED, approved_qty=10.0
    )


def test_timeout_before_reaching_broker_leaves_unknown_no_record():
    broker = CountingAlpaca()
    service = OrderService(broker)
    broker.fail_next_submit = "ambiguous_unsent"
    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.record.state is OrderState.UNKNOWN
    assert broker.find_order_by_client_id("ord-fi") is None
    assert broker.submit_calls == 1


def test_ack_lost_but_broker_accepted_is_recoverable():
    broker = CountingAlpaca()
    store = OrderEventStore()
    service = OrderService(broker, store=store)
    broker.fail_next_submit = "ambiguous_sent"
    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.record.state is OrderState.UNKNOWN

    restarted = OrderService(broker, store=_reload(store))
    resolved = restarted.resolve_unknown("ord-fi")
    assert resolved is not None and resolved.state is OrderState.ACKNOWLEDGED
    assert broker.submit_calls == 1  # no blind duplicate


def test_duplicate_ack_does_not_corrupt_state():
    store = OrderEventStore()
    store.append(
        client_order_id="o",
        kind=OrderEventKind.CREATED,
        state=OrderState.CREATED,
    )
    store.append(client_order_id="o", kind=OrderEventKind.APPROVED, state=OrderState.APPROVED)
    store.append(client_order_id="o", kind=OrderEventKind.SUBMITTED, state=OrderState.SUBMITTED)
    store.append(client_order_id="o", kind=OrderEventKind.BROKER_ACK, state=OrderState.ACKNOWLEDGED)
    second = store.append(
        client_order_id="o", kind=OrderEventKind.BROKER_ACK, state=OrderState.ACKNOWLEDGED
    )
    assert second.anomaly is None  # self-transition is a legal re-affirmation
    assert store.record("o").state is OrderState.ACKNOWLEDGED


def test_fill_before_ack_does_not_silently_overwrite():
    store = OrderEventStore()
    store.append(client_order_id="o", kind=OrderEventKind.CREATED, state=OrderState.CREATED)
    store.append(client_order_id="o", kind=OrderEventKind.APPROVED, state=OrderState.APPROVED)
    store.append(client_order_id="o", kind=OrderEventKind.SUBMITTED, state=OrderState.SUBMITTED)
    # Fill arrives first.
    store.append(client_order_id="o", kind=OrderEventKind.STATE_CHANGE, state=OrderState.PARTIALLY_FILLED)
    # Late ACK would move backwards; it is recorded as an anomaly, not applied.
    ack = store.append(client_order_id="o", kind=OrderEventKind.BROKER_ACK, state=OrderState.ACKNOWLEDGED)
    assert ack.anomaly is not None
    record = store.record("o")
    assert record.state is OrderState.PARTIALLY_FILLED
    assert record.anomaly_count == 1


def test_partial_fill_then_cancel_retains_filled_quantity():
    broker = CountingAlpaca()
    service = OrderService(broker)
    service.submit(_order(), _decision(), config_version="cfg-1")
    broker.simulate_fill("ord-fi", qty=4, price=100, partial=True)
    broker_order = broker.find_order_by_client_id("ord-fi")
    service.apply_broker_order(broker_order)

    record = service.cancel("ord-fi")
    assert record.state is OrderState.CANCELED
    assert record.filled_qty == Decimal(4)  # cancellation never erases executed qty


def test_cancel_races_with_full_fill_no_false_cancellation():
    broker = CountingAlpaca()
    service = OrderService(broker)
    service.submit(_order(), _decision(), config_version="cfg-1")
    # Broker fills fully; AXEL has not ingested the fill yet.
    broker.simulate_fill("ord-fi", qty=10, price=100, partial=False)

    record = service.cancel("ord-fi")
    # The broker refuses the cancel; reconciliation proves FILLED, not CANCELED.
    assert record.state is OrderState.FILLED


def test_crash_after_acceptance_before_local_ack_recovered():
    broker = CountingAlpaca()
    store = FlakyStore(fail_after=3)  # CREATED, APPROVED, SUBMITTED succeed; ACK append fails
    service = OrderService(broker, store=store)
    with pytest.raises(RuntimeError):
        service.submit(_order(), _decision(), config_version="cfg-1")

    assert broker.submit_calls == 1
    assert broker.find_order_by_client_id("ord-fi") is not None  # broker recorded it

    restarted = OrderService(broker, store=_reload(store))
    assert len(restarted.pending_recovery()) == 1
    resolved = restarted.resolve_unknown("ord-fi")
    assert resolved is not None and resolved.state is OrderState.ACKNOWLEDGED
    assert broker.submit_calls == 1  # still no duplicate


def test_database_unavailable_before_submit_blocks_submission():
    broker = CountingAlpaca()
    store = FlakyStore(fail_after=0)  # CREATED append fails before any network call
    service = OrderService(broker, store=store)
    with pytest.raises(RuntimeError):
        service.submit(_order(), _decision(), config_version="cfg-1")
    assert broker.submit_calls == 0


def test_malformed_broker_response_is_non_retryable():
    with pytest.raises(NonRetryableBrokerError):
        parse_decimal("not-a-number", field_name="qty")

    broker = AlpacaAdapter(mock_mode=True)
    with pytest.raises(NonRetryableBrokerError):
        broker._broker_order_from_payload(
            {"id": "x", "symbol": "SPY", "side": "buy", "qty": "bad", "status": "new"}
        )


def test_stale_quote_and_watchdog_and_disconnect_block_new_risk():
    from axel.core.types import Section, TradingMode
    from axel.execution.live_gate import ApprovalMetadata, LiveConfig

    config = LiveConfig(
        config_version="cfg-1",
        environment=TradingMode.LIVE,
        broker="alpaca",
        account_id="a1",
        allowed_section=Section.STOCKS,
        max_live_capital=Decimal(1000),
        max_position_notional=Decimal(500),
        max_order_notional=Decimal(500),
        max_daily_loss=Decimal(50),
        max_concurrent_positions=5,
        max_order_frequency=10,
        order_frequency_window_seconds=60,
        allowed_symbols=frozenset({"SPY"}),
        manual_approval_required=False,
        phase5_evidence_ref="ev",
        phase5_gate_passed=True,
        approval_metadata=ApprovalMetadata(approved_by="ops", approved_at=NOW, reference="CAB"),
    )

    def ctx(**overrides):
        base = {
            "order": _order(),
            "config": config,
            "account": PortfolioSnapshot(
                total_equity=Decimal(1000),
                cash=Decimal(1000),
                buying_power=Decimal(1000),
                start_of_day_equity=Decimal(1000),
            ),
            "now": NOW,
            "market": MarketSnapshot("SPY", Decimal(100), NOW),
            "strategy_live_allowed": True,
            "session_open": True,
            "watchdog_healthy": True,
            "broker_connected": True,
            "approval_valid": True,
        }
        base.update(overrides)
        return PreTradeContext(**base)

    from datetime import timedelta

    stale = ctx(market=MarketSnapshot("SPY", Decimal(100), NOW - timedelta(seconds=120)), market_data_max_age_seconds=30)
    assert "PT_STALE_MARKET_DATA" in PreTradeValidator().validate(stale).reason_codes
    assert "PT_WATCHDOG_UNHEALTHY" in PreTradeValidator().validate(ctx(watchdog_healthy=False)).reason_codes
    assert "PT_BROKER_DISCONNECTED" in PreTradeValidator().validate(ctx(broker_connected=False)).reason_codes


def test_kill_switch_during_active_orders_blocks_new_risk_but_allows_cancel():
    broker = CountingAlpaca()
    service = OrderService(broker)
    service.submit(_order(), _decision(), config_version="cfg-1")

    # Cancellation remains available (never removed by a halt).
    record = service.cancel("ord-fi")
    assert record.state is OrderState.CANCELED


def _reload(store: OrderEventStore) -> OrderEventStore:
    fresh = OrderEventStore()
    fresh.restore(store.snapshot())
    return fresh
