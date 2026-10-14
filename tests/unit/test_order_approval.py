"""Phase 6 canonical order approval tests (spec §6.4 / §8.2)."""

from datetime import timedelta

import pytest

from axel.core.clock import SimulatedClock
from axel.core.contracts import Order
from axel.core.types import OrderSide, OrderType
from axel.execution.approval import (
    ApprovalError,
    AuthenticatedPrincipal,
    OrderApprovalService,
    PrincipalKind,
    compute_order_hash,
)
from axel.execution.reason_codes import ApprovalReason

HUMAN = AuthenticatedPrincipal(principal_id="trader_bob", kind=PrincipalKind.HUMAN, auth_reference="mfa-123")
AGENT = AuthenticatedPrincipal(principal_id="analyst-1", kind=PrincipalKind.AGENT, auth_reference="agent")


def _order(**overrides) -> Order:
    base = {
        "client_order_id": "ord_1",
        "proposal_id": "prop_1",
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


def test_order_hash_is_stable_and_config_sensitive():
    order = _order()
    h1 = compute_order_hash(order, config_version="cfg-1")
    h2 = compute_order_hash(_order(), config_version="cfg-1")
    assert h1 == h2
    assert compute_order_hash(order, config_version="cfg-2") != h1


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "QQQ"},
        {"side": OrderSide.SELL},
        {"qty": 11.0},
        {"limit_price": 101.0},
        {"type": OrderType.MARKET, "limit_price": None},
        {"strategy_version": "1.0.1"},
    ],
)
def test_material_change_changes_hash(change):
    base_hash = compute_order_hash(_order(), config_version="cfg-1")
    assert compute_order_hash(_order(**change), config_version="cfg-1") != base_hash


def test_happy_path_approve_verify_consume_once():
    clock = SimulatedClock()
    service = OrderApprovalService(clock=clock)
    order = _order()

    record = service.request(order, config_version="cfg-1", requested_by="agent-9")
    service.approve(record.approval_id, HUMAN)

    verified = service.verify(order, config_version="cfg-1")
    assert verified.approval_id == record.approval_id

    service.consume(order, config_version="cfg-1")
    with pytest.raises(ApprovalError) as exc:
        service.consume(order, config_version="cfg-1")
    assert exc.value.reason is ApprovalReason.ALREADY_CONSUMED


def test_verify_without_approval_fails():
    service = OrderApprovalService()
    with pytest.raises(ApprovalError) as exc:
        service.verify(_order(), config_version="cfg-1")
    assert exc.value.reason is ApprovalReason.NOT_FOUND


def test_expired_approval_rejected():
    clock = SimulatedClock()
    service = OrderApprovalService(clock=clock)
    order = _order()
    record = service.request(order, config_version="cfg-1", requested_by="agent-9")
    service.approve(record.approval_id, HUMAN, ttl_seconds=30)
    clock.advance(timedelta(seconds=31))
    with pytest.raises(ApprovalError) as exc:
        service.consume(order, config_version="cfg-1")
    assert exc.value.reason is ApprovalReason.EXPIRED


def test_agent_cannot_approve():
    service = OrderApprovalService()
    order = _order()
    record = service.request(order, config_version="cfg-1", requested_by="agent-9")
    with pytest.raises(ApprovalError) as exc:
        service.approve(record.approval_id, AGENT)
    assert exc.value.reason is ApprovalReason.UNAUTHENTICATED


def test_self_approval_forbidden():
    service = OrderApprovalService()
    order = _order()
    record = service.request(order, config_version="cfg-1", requested_by="trader_bob")
    with pytest.raises(ApprovalError) as exc:
        service.approve(record.approval_id, HUMAN)
    assert exc.value.reason is ApprovalReason.SELF_APPROVAL_FORBIDDEN


def test_approval_bound_to_exact_order_hash():
    service = OrderApprovalService()
    order = _order()
    record = service.request(order, config_version="cfg-1", requested_by="agent-9")
    service.approve(record.approval_id, HUMAN)

    # A materially changed order (different qty) has a different hash -> refused.
    with pytest.raises(ApprovalError) as exc:
        service.verify(_order(qty=99.0), config_version="cfg-1")
    assert exc.value.reason is ApprovalReason.NOT_FOUND

    # A different config version is likewise refused.
    with pytest.raises(ApprovalError) as exc:
        service.verify(order, config_version="cfg-2")
    assert exc.value.reason is ApprovalReason.NOT_FOUND


def test_rejected_approval_cannot_be_consumed():
    service = OrderApprovalService()
    order = _order()
    record = service.request(order, config_version="cfg-1", requested_by="agent-9")
    service.reject(record.approval_id, HUMAN, reason="not today")
    with pytest.raises(ApprovalError) as exc:
        service.consume(order, config_version="cfg-1")
    assert exc.value.reason is ApprovalReason.REJECTED


def test_consumed_approval_not_resurrected_by_new_lookup():
    service = OrderApprovalService()
    order = _order()
    record = service.request(order, config_version="cfg-1", requested_by="agent-9")
    service.approve(record.approval_id, HUMAN)
    service.consume(order, config_version="cfg-1")
    with pytest.raises(ApprovalError):
        service.verify(order, config_version="cfg-1")
