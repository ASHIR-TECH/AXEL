from datetime import UTC, datetime

from axel.db.models import (
    FillModel,
    OrderModel,
    RiskDecisionModel,
    TradeProposalModel,
)


def test_db_models_roundtrip(in_memory_db):
    # 1. Insert Trade Proposal
    proposal = TradeProposalModel(
        id="prop_test_01",
        section="stocks",
        symbol="AAPL",
        side="buy",
        entry_price=150.0,
        stop_loss=145.0,
        take_profit=162.5,
        r_multiple_target=2.5,
        position_size_usd=5000.0,
        confidence_score=0.85,
        strategy_id="trend_breakout_v1",
        mode="paper",
        status="approved",
        ttl_expires_at=datetime.now(UTC),
        agent_reasoning="Strong bull flag confirmed on 1H chart",
    )
    in_memory_db.add(proposal)
    in_memory_db.commit()

    # 2. Insert Risk Decision linked to Proposal
    risk_dec = RiskDecisionModel(
        id="risk_test_01",
        proposal_id=proposal.id,
        approved=True,
        verdict="approved",
        approved_qty=33.0,
        approved_notional_usd=4950.0,
        binding_limit="NOTIONAL_CAP_5PCT",
        reasons=["All deterministic checks passed"],
        checks_passed=["GLOBAL_KILL_SWITCH_CLEAR", "RR_RATIO_VALID"],
        checks_failed=[],
    )
    in_memory_db.add(risk_dec)
    in_memory_db.commit()

    # 3. Insert Order linked to Proposal
    order = OrderModel(
        client_order_id="ord_test_01",
        proposal_id=proposal.id,
        broker_order_id="broker_12345",
        symbol="AAPL",
        side="buy",
        qty=33.0,
        type="limit",
        limit_price=150.0,
        state="filled",
        filled_qty=33.0,
        filled_avg_price=149.95,
    )
    in_memory_db.add(order)
    in_memory_db.commit()

    # 4. Insert Fill linked to Order
    fill = FillModel(
        id="fill_test_01",
        client_order_id=order.client_order_id,
        broker_fill_id="bfill_01",
        symbol="AAPL",
        side="buy",
        qty=33.0,
        price=149.95,
        fee=0.0,
        slippage=-0.05,
    )
    in_memory_db.add(fill)
    in_memory_db.commit()

    # 5. Verify query & relationships
    queried_prop = in_memory_db.query(TradeProposalModel).filter_by(id="prop_test_01").first()
    assert queried_prop is not None
    assert queried_prop.symbol == "AAPL"
    assert queried_prop.risk_decision.verdict == "approved"
    assert len(queried_prop.orders) == 1
    assert queried_prop.orders[0].fills[0].price == 149.95
