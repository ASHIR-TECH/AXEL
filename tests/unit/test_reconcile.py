from axel.core.contracts import Order
from axel.core.types import OrderSide, OrderState, OrderType
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.broker_base import PositionInfo
from axel.execution.reconcile import ReconciliationEngine


def test_reconcile_positions_drift():
    broker = AlpacaAdapter(mock_mode=True)
    # Put 50 shares of AAPL on broker
    broker._mock_positions["AAPL"] = PositionInfo(
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=50.0,
        current_price=150.0,
        market_value=7500.0,
        cost_basis=7500.0,
        unrealized_pnl=0.0,
    )

    reconciler = ReconciliationEngine(broker)

    # DB thinks we have 60 shares
    db_positions = {"AAPL": 60.0}
    drifts = reconciler.reconcile_positions(db_positions)

    assert len(drifts) == 1
    assert drifts[0].symbol == "AAPL"
    assert drifts[0].difference == 10.0


def test_reconcile_ghost_orders():
    broker = AlpacaAdapter(mock_mode=True)
    reconciler = ReconciliationEngine(broker)

    active_db_orders = [
        Order(
            client_order_id="ord_1",
            proposal_id="prop_1",
            symbol="AAPL",
            side=OrderSide.BUY,
            qty=10.0,
            type=OrderType.LIMIT,
            state=OrderState.ACCEPTED,
        )
    ]

    # Broker has ord_1 and an unrecorded ghost order ord_ghost
    broker_open_orders = ["ord_1", "ord_ghost"]
    report = reconciler.reconcile_orders(active_db_orders, broker_open_orders)

    assert not report.is_healthy
    assert "ord_ghost" in report.ghost_broker_orders
