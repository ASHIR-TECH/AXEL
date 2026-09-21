from datetime import timedelta

import pytest

from axel.core.clock import SimulatedClock
from axel.core.contracts import Proposal, RiskDecision
from axel.core.types import OrderSide, ProposalStatus, RiskVerdict, Section
from axel.execution.approval import ApprovalService


def test_approval_workflow():
    clock = SimulatedClock()
    service = ApprovalService(clock=clock)

    prop = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.BUY,
        entry=150.0,
        stop=145.0,
        target=160.0,
        strategy_id="strat_v1",
        panel_confidence=0.85,
    )
    decision = RiskDecision(
        proposal_id=prop.id,
        approved=True,
        verdict=RiskVerdict.APPROVED,
        approved_qty=10.0,
        approved_notional_usd=1500.0,
    )

    ticket = service.submit_for_approval(prop, decision, custom_ttl_seconds=300)
    assert ticket.status == ProposalStatus.PENDING

    # Approve
    approved_ticket = service.approve(prop.id, operator_id="trader_bob")
    assert approved_ticket.status == ProposalStatus.APPROVED
    assert approved_ticket.operator_id == "trader_bob"


def test_approval_ttl_expiration():
    clock = SimulatedClock()
    service = ApprovalService(clock=clock)

    prop = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.BUY,
        entry=150.0,
        stop=145.0,
        target=160.0,
        strategy_id="strat_v1",
        panel_confidence=0.85,
    )
    decision = RiskDecision(
        proposal_id=prop.id,
        approved=True,
        verdict=RiskVerdict.APPROVED,
        approved_qty=10.0,
    )

    service.submit_for_approval(prop, decision, custom_ttl_seconds=120)

    # Step clock forward 150 seconds
    clock.advance(timedelta(seconds=150))

    # Sweeping expired tickets
    expired = service.sweep_expired()
    assert len(expired) == 1
    assert expired[0].status == ProposalStatus.EXPIRED

    # Attempting to approve after expiration must fail
    with pytest.raises(ValueError):
        service.approve(prop.id, operator_id="trader_bob")
