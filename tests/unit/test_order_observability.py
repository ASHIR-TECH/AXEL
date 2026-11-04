"""Tests that failed transactions produce durable operational events."""

from __future__ import annotations

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderType, RiskVerdict
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.order_service import OrderService
from axel.execution.reason_codes import OrderReason
from axel.observability.api_health import configure_event_sink


def _order() -> Order:
    return Order(
        client_order_id="ord-obs-1",
        proposal_id="prop-obs-1",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=10.0,
        type=OrderType.LIMIT,
        limit_price=100.0,
        strategy_id="strat_v1",
        strategy_version="1.0.0",
    )


def _decision() -> RiskDecision:
    return RiskDecision(
        proposal_id="prop-obs-1",
        approved=True,
        verdict=RiskVerdict.APPROVED,
        approved_qty=10.0,
        approved_notional_usd=1000.0,
    )


def _capture():
    events = []
    configure_event_sink(lambda event, call: events.append(event))
    return events


def test_rejected_order_emits_critical_event():
    events = _capture()
    broker = AlpacaAdapter(mock_mode=True)
    broker.fail_next_submit = "rejected"
    service = OrderService(broker)

    outcome = service.submit(_order(), _decision(), config_version="cfg-1")

    assert outcome.reason_code == OrderReason.BROKER_REJECTED.value
    assert len(events) == 1
    event = events[0]
    assert event.category.value == "order"
    assert event.event == "order_rejected"
    assert event.outcome == "rejected"
    assert event.severity == "critical"
    assert event.payload["client_order_id"] == "ord-obs-1"
    assert event.payload["symbol"] == "SPY"


def test_transport_failure_emits_critical_event():
    events = _capture()
    broker = AlpacaAdapter(mock_mode=True)
    broker.fail_next_submit = "transport"
    service = OrderService(broker)

    outcome = service.submit(_order(), _decision(), config_version="cfg-1")

    assert outcome.reason_code == OrderReason.BROKER_TRANSPORT_ERROR.value
    assert len(events) == 1
    assert events[0].event == "order_submit_failed"
    assert events[0].outcome == "transport_error"
    assert events[0].severity == "critical"


def test_ambiguous_submission_emits_critical_event():
    events = _capture()
    broker = AlpacaAdapter(mock_mode=True)
    broker.fail_next_submit = "ambiguous_unsent"
    service = OrderService(broker)

    outcome = service.submit(_order(), _decision(), config_version="cfg-1")

    assert outcome.ambiguous is True
    assert len(events) == 1
    assert events[0].event == "order_submission_ambiguous"
    assert events[0].outcome == "ambiguous"
    assert events[0].severity == "critical"


def test_successful_submission_emits_no_failure_event():
    events = _capture()
    service = OrderService(AlpacaAdapter(mock_mode=True))

    outcome = service.submit(_order(), _decision(), config_version="cfg-1")

    assert outcome.submitted is True
    assert events == []
