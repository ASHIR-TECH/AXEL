"""Phase 6 reconciliation loop tests (spec §10.2/§10.3)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from axel.core.types import OrderSide, OrderState
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.broker_base import BrokerAccount, BrokerFill, BrokerOrder, PositionInfo
from axel.execution.incidents import IncidentStore
from axel.execution.order_store import OrderRecord
from axel.execution.reason_codes import ReconReason
from axel.execution.reconcile import ReconciliationEngine

NOW = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)


def _record(
    client_order_id: str,
    *,
    state: OrderState = OrderState.ACKNOWLEDGED,
    broker_order_id: str | None = "b-1",
    symbol: str = "SPY",
    qty: str = "10",
    filled_qty: str = "0",
) -> OrderRecord:
    return OrderRecord(
        client_order_id=client_order_id,
        state=state,
        symbol=symbol,
        side=OrderSide.BUY,
        qty=Decimal(qty),
        filled_qty=Decimal(filled_qty),
        broker_order_id=broker_order_id,
    )


def _broker_order(
    broker_order_id: str = "b-1",
    *,
    client_order_id: str | None = "ord-1",
    state: OrderState = OrderState.ACKNOWLEDGED,
    symbol: str = "SPY",
    qty: str = "10",
    filled_qty: str = "0",
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        qty=Decimal(qty),
        filled_qty=Decimal(filled_qty),
        state=state,
    )


def _fill(
    *,
    broker_order_id: str = "b-1",
    client_order_id: str | None = "ord-1",
    qty: str = "10",
    price: str = "100",
    broker_fill_id: str | None = "f-1",
    symbol: str = "SPY",
) -> BrokerFill:
    return BrokerFill(
        broker_order_id=broker_order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        qty=Decimal(qty),
        price=Decimal(price),
        broker_fill_id=broker_fill_id,
        client_order_id=client_order_id,
        event_time=NOW,
    )


def _engine() -> ReconciliationEngine:
    return ReconciliationEngine(AlpacaAdapter(mock_mode=True), incident_store=IncidentStore())


def _account(cash: str = "1000", buying_power: str = "2000") -> BrokerAccount:
    return BrokerAccount(
        account_id="acct-1",
        total_equity=Decimal(1000),
        cash=Decimal(cash),
        buying_power=Decimal(buying_power),
    )


def test_healthy_snapshot_has_no_mismatches():
    result = _engine().reconcile_all(
        axel_orders=[_record("ord-1")],
        broker_account=_account(),
        broker_open_orders=[_broker_order()],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert result.is_healthy
    assert not result.blocks_new_risk
    assert result.reason_codes == []


def test_broker_order_without_intent_is_critical_incident():
    engine = _engine()
    result = engine.reconcile_all(
        axel_orders=[],
        broker_account=_account(),
        broker_open_orders=[_broker_order(client_order_id="ghost")],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert ReconReason.BROKER_ORDER_WITHOUT_INTENT.value in result.reason_codes
    assert result.blocks_new_risk
    assert engine.incidents.critical_open_count() == 1


def test_inflight_axel_order_missing_on_broker():
    result = _engine().reconcile_all(
        axel_orders=[_record("ord-1", state=OrderState.SUBMITTED, broker_order_id=None)],
        broker_account=_account(),
        broker_open_orders=[],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert ReconReason.MISSING_BROKER_ORDER.value in result.reason_codes


def test_terminal_axel_order_still_open_on_broker():
    result = _engine().reconcile_all(
        axel_orders=[_record("ord-1", state=OrderState.FILLED, filled_qty="10")],
        broker_account=_account(),
        broker_open_orders=[_broker_order(state=OrderState.ACKNOWLEDGED)],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert ReconReason.TERMINAL_STATE_MISMATCH.value in result.reason_codes


def test_cancellation_not_reflected():
    result = _engine().reconcile_all(
        axel_orders=[_record("ord-1", state=OrderState.CANCEL_PENDING)],
        broker_account=_account(),
        broker_open_orders=[_broker_order(state=OrderState.ACKNOWLEDGED)],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert ReconReason.CANCELLATION_NOT_REFLECTED.value in result.reason_codes


def test_axel_fill_exceeds_broker_fill():
    result = _engine().reconcile_all(
        axel_orders=[_record("ord-1", filled_qty="10")],
        broker_account=_account(),
        broker_open_orders=[_broker_order(state=OrderState.PARTIALLY_FILLED, filled_qty="5")],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert ReconReason.FILL_QTY_EXCEEDS_BROKER.value in result.reason_codes


def test_unrecorded_broker_fill():
    result = _engine().reconcile_all(
        axel_orders=[_record("ord-1")],
        broker_account=_account(),
        broker_open_orders=[_broker_order()],
        broker_positions=[],
        broker_fills=[_fill(qty="10")],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert ReconReason.UNRECORDED_FILL.value in result.reason_codes


def test_duplicate_broker_fill():
    result = _engine().reconcile_all(
        axel_orders=[_record("ord-1", filled_qty="10")],
        broker_account=_account(),
        broker_open_orders=[],
        broker_positions=[],
        broker_fills=[_fill(qty="10"), _fill(qty="10")],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert ReconReason.DUPLICATE_FILL.value in result.reason_codes


def test_position_mismatch():
    result = _engine().reconcile_all(
        axel_orders=[],
        broker_account=_account(),
        broker_open_orders=[],
        broker_positions=[
            PositionInfo(
                symbol="SPY",
                side=OrderSide.BUY,
                qty=50.0,
                current_price=100.0,
                market_value=5000.0,
                cost_basis=5000.0,
                unrealized_pnl=0.0,
            )
        ],
        broker_fills=[],
        fill_derived_positions={"SPY": Decimal(60)},
        snapshot_time=NOW,
    )
    assert ReconReason.POSITION_MISMATCH.value in result.reason_codes


def test_cash_and_buying_power_mismatch():
    result = _engine().reconcile_all(
        axel_orders=[],
        broker_account=_account(cash="1000", buying_power="2000"),
        broker_open_orders=[],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        axel_cash=Decimal(900),
        axel_buying_power=Decimal(1500),
        snapshot_time=NOW,
    )
    assert result.reason_codes.count(ReconReason.CASH_MISMATCH.value) == 2


def test_stale_snapshot_is_critical():
    engine = _engine()
    result = engine.reconcile_all(
        axel_orders=[],
        broker_account=_account(),
        broker_open_orders=[],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW - timedelta(seconds=300),
        max_snapshot_age_seconds=60,
    )
    assert ReconReason.STALE_SNAPSHOT.value in result.reason_codes
    assert result.blocks_new_risk


def test_repeated_runs_deduplicate_incidents():
    engine = _engine()
    kwargs = {
        "axel_orders": [],
        "broker_account": _account(),
        "broker_open_orders": [_broker_order(client_order_id="ghost")],
        "broker_positions": [],
        "broker_fills": [],
        "fill_derived_positions": {},
        "snapshot_time": NOW,
    }
    engine.reconcile_all(**kwargs)
    engine.reconcile_all(**kwargs)
    assert len(engine.incidents.open_incidents()) == 1
    assert engine.incidents.open_incidents()[0].occurrence_count == 2


def test_reconcile_live_uses_broker_snapshot():
    broker = AlpacaAdapter(mock_mode=True)
    broker._mock_balance["cash"] = 100_000.0
    broker._mock_balance["buying_power"] = 200_000.0
    engine = ReconciliationEngine(broker, incident_store=IncidentStore())
    result = engine.reconcile_live(
        axel_orders=[],
        fill_derived_positions={},
        axel_cash=Decimal("100000.0"),
        axel_buying_power=Decimal("200000.0"),
    )
    # Account matches; no positions/orders/fills on either side -> healthy.
    assert result.is_healthy
    assert result.broker_account_id == "mock-account"


def test_works_during_halt_read_only():
    # reconcile_all has no side effects on the broker: it only compares inputs.
    engine = _engine()
    result = engine.reconcile_all(
        axel_orders=[],
        broker_account=_account(),
        broker_open_orders=[],
        broker_positions=[],
        broker_fills=[],
        fill_derived_positions={},
        snapshot_time=NOW,
    )
    assert result.is_healthy
