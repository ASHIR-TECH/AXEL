import pytest
from pydantic import ValidationError

from axel.core.contracts import (
    Proposal,
    Signal,
)
from axel.core.ids import generate_client_order_id
from axel.core.types import (
    Direction,
    OrderSide,
    Section,
    TradingMode,
)


def test_proposal_validation_buy():
    # Valid buy proposal
    prop = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.BUY,
        entry=150.0,
        stop=145.0,
        target=160.0,
        strategy_id="strat_1",
        panel_confidence=0.8,
    )
    assert prop.symbol == "AAPL"
    assert prop.mode == TradingMode.PAPER

    # Invalid buy stop >= entry
    with pytest.raises(ValidationError):
        Proposal(
            section=Section.STOCKS,
            symbol="AAPL",
            side=OrderSide.BUY,
            entry=150.0,
            stop=150.0,
            target=160.0,
            strategy_id="strat_1",
            panel_confidence=0.8,
        )


def test_proposal_validation_sell():
    # Valid sell proposal
    prop = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.SELL,
        entry=150.0,
        stop=155.0,
        target=140.0,
        strategy_id="strat_1",
        panel_confidence=0.8,
    )
    assert prop.stop > prop.entry

    # Invalid sell stop <= entry
    with pytest.raises(ValidationError):
        Proposal(
            section=Section.STOCKS,
            symbol="AAPL",
            side=OrderSide.SELL,
            entry=150.0,
            stop=145.0,
            target=140.0,
            strategy_id="strat_1",
            panel_confidence=0.8,
        )


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Signal(
            section=Section.STOCKS,
            symbol="AAPL",
            analyst="Technical",
            direction=Direction.LONG,
            strength=0.8,
            hallucinated_extra_key="dangerous_injection",  # Forbidden
        )


def test_client_order_id_idempotency():
    oid1 = generate_client_order_id("prop_12345", attempt=1)
    oid2 = generate_client_order_id("prop_12345", attempt=1)
    oid_retry = generate_client_order_id("prop_12345", attempt=2)

    assert oid1 == oid2
    assert oid1 != oid_retry
    assert len(oid1) <= 64
