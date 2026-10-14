"""Phase 6 idempotent order submission / recovery tests (spec §7.3/§7.4)."""

import pytest

from axel.core.contracts import Order, RiskDecision
from axel.core.ids import derive_client_order_id
from axel.core.types import OrderSide, OrderState, OrderType, RiskVerdict
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.approval import (
    ApprovalError,
    AuthenticatedPrincipal,
    OrderApprovalService,
    PrincipalKind,
    compute_order_hash,
)
from axel.execution.order_service import OrderIdempotencyConflict, OrderService
from axel.execution.order_store import OrderEventStore

HUMAN = AuthenticatedPrincipal(principal_id="trader_bob", kind=PrincipalKind.HUMAN, auth_reference="mfa")


class CountingAlpaca(AlpacaAdapter):
    """Mock adapter that counts real submit attempts."""

    def __init__(self, **kwargs):
        super().__init__(mock_mode=True, **kwargs)
        self.submit_calls = 0

    def _execute_order(self, order, risk_decision):
        self.submit_calls += 1
        return super()._execute_order(order, risk_decision)


def _order(**overrides) -> Order:
    base = {
        "client_order_id": "ord-1",
        "proposal_id": "prop-1",
        "symbol": "SPY",
        "side": OrderSide.BUY,
        "qty": 10.0,
        "type": OrderType.LIMIT,
        "limit_price": 100.0,
        "strategy_id": "strat_v1",
        "strategy_version": "1.0.0",
    }
    base.update(overrides)
    return Order(**base)


def _decision() -> RiskDecision:
    return RiskDecision(
        proposal_id="prop-1",
        approved=True,
        verdict=RiskVerdict.APPROVED,
        approved_qty=10.0,
        approved_notional_usd=1000.0,
    )


def test_happy_path_submits_once_and_acks():
    broker = CountingAlpaca()
    service = OrderService(broker)
    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.submitted and not outcome.duplicate and not outcome.ambiguous
    assert outcome.record.state is OrderState.ACKNOWLEDGED
    assert broker.submit_calls == 1


def test_idempotent_resubmit_does_not_hit_broker_again():
    broker = CountingAlpaca()
    service = OrderService(broker)
    first = service.submit(_order(), _decision(), config_version="cfg-1")
    second = service.submit(_order(), _decision(), config_version="cfg-1")
    assert second.duplicate and not second.submitted
    assert second.record.client_order_id == first.record.client_order_id
    assert broker.submit_calls == 1


def test_client_order_id_derived_is_deterministic():
    order_hash = compute_order_hash(_order(), config_version="cfg-1")
    assert derive_client_order_id(order_hash) == derive_client_order_id(order_hash)
    assert len(derive_client_order_id(order_hash)) <= 128


def test_same_client_id_different_intent_conflicts():
    broker = CountingAlpaca()
    service = OrderService(broker)
    service.submit(_order(), _decision(), config_version="cfg-1")
    with pytest.raises(OrderIdempotencyConflict):
        service.submit(_order(qty=999.0), _decision(), config_version="cfg-1")


def test_approval_is_required_and_consumed():
    broker = CountingAlpaca()
    service = OrderService(broker)
    approvals = OrderApprovalService()

    # No approval issued yet -> refused.
    with pytest.raises(ApprovalError):
        service.submit(_order(), _decision(), config_version="cfg-1", approvals=approvals)

    order = _order()
    record = approvals.request(order, config_version="cfg-1", requested_by="agent-1")
    approvals.approve(record.approval_id, HUMAN)

    outcome = service.submit(order, _decision(), config_version="cfg-1", approvals=approvals)
    assert outcome.submitted

    # The approval is now single-use: it can no longer be verified.
    with pytest.raises(ApprovalError) as exc:
        approvals.verify(order, config_version="cfg-1")
    assert exc.value.reason.value == "APPROVAL_ALREADY_CONSUMED"


def test_ambiguous_sent_becomes_unknown_then_repairs_on_restart():
    broker = CountingAlpaca()
    store = OrderEventStore()
    service = OrderService(broker, store=store)
    broker.fail_next_submit = "ambiguous_sent"

    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.ambiguous and outcome.record.state is OrderState.UNKNOWN

    # Simulate a crash + restart: only the event log survives.
    snapshot = store.snapshot()
    restarted_store = OrderEventStore()
    restarted_store.restore(snapshot)
    restarted = OrderService(broker, store=restarted_store)

    assert len(restarted.pending_recovery()) == 1
    resolved = restarted.resolve_unknown("ord-1")
    assert resolved is not None and resolved.state is OrderState.ACKNOWLEDGED
    # Recovery must not resubmit.
    assert broker.submit_calls == 1


def test_ambiguous_unsent_stays_unknown_without_repair():
    broker = CountingAlpaca()
    service = OrderService(broker)
    broker.fail_next_submit = "ambiguous_unsent"
    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.record.state is OrderState.UNKNOWN
    resolved = service.resolve_unknown("ord-1")
    assert resolved is not None and resolved.state is OrderState.UNKNOWN


def test_rejected_submission_maps_to_rejected():
    broker = CountingAlpaca()
    service = OrderService(broker)
    broker.fail_next_submit = "rejected"
    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.record.state is OrderState.REJECTED
    assert outcome.record.last_reason_code is not None


def test_transport_error_maps_to_error_and_is_not_retried_blindly():
    broker = CountingAlpaca()
    service = OrderService(broker)
    broker.fail_next_submit = "transport"
    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.record.state is OrderState.ERROR
    assert broker.submit_calls == 1


def test_cancel_flow_confirms_cancellation():
    broker = CountingAlpaca()
    service = OrderService(broker)
    service.submit(_order(), _decision(), config_version="cfg-1")
    record = service.cancel("ord-1")
    assert record.state is OrderState.CANCELED


def test_apply_fill_is_idempotent():
    from decimal import Decimal

    from axel.execution.broker_base import BrokerFill

    broker = CountingAlpaca()
    service = OrderService(broker)
    service.submit(_order(), _decision(), config_version="cfg-1")
    fill = BrokerFill(
        broker_order_id="b1",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=Decimal(10),
        price=Decimal(100),
        broker_fill_id="f-1",
        client_order_id="ord-1",
    )
    first = service.apply_fill(fill)
    assert first is not None and first.state is OrderState.FILLED
    assert service.apply_fill(fill) is None  # duplicate
