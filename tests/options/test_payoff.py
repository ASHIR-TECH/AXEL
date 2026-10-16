"""
Unit tests for Phase 8 Options payoff engine — multi-leg payoff grids,
max loss/gain, and break-even points.
"""

from datetime import UTC, datetime

import pytest

from axel.options.payoff import (
    PayoffGrid,
    build_payoff_grid,
    compute_break_even_points,
    compute_leg_payoff,
    compute_structure_payoff,
    max_gain_for_structure,
    max_loss_for_structure,
)
from axel.options.types import (
    OptionContract,
    OptionLeg,
    OptionRight,
    OptionSide,
    OptionsProposal,
)


def _contract(
    contract_id: str,
    right: OptionRight,
    strike: float,
    expiry: datetime | None = None,
    multiplier: float = 100.0,
) -> OptionContract:
    return OptionContract(
        contract_id=contract_id,
        venue="test",
        underlying_id="spy",
        underlying_symbol="SPY",
        right=right,
        strike=strike,
        expiry=expiry or datetime(2026, 11, 15, tzinfo=UTC),  # ~30 DTE from Oct 2026
        multiplier=multiplier,
    )


def _leg(
    contract: OptionContract,
    side: OptionSide,
    entry_price: float,
    ratio: int = 1,
) -> OptionLeg:
    return OptionLeg(contract=contract, side=side, entry_price=entry_price, ratio=ratio)


def _proposal(
    legs: tuple[OptionLeg, ...],
    proposal_id: str = "prop_1",
    max_loss: float | None = None,
    max_gain: float | None = None,
) -> OptionsProposal:
    return OptionsProposal(
        proposal_id=proposal_id,
        strategy_id="strat_v1",
        underlying_id="spy",
        underlying_symbol="SPY",
        legs=legs,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        valid_until=datetime(2026, 12, 31, tzinfo=UTC),
        forecast_version="v1",
        data_snapshot_id="snap_1",
        max_loss=max_loss,
        max_gain=max_gain,
    )


def test_long_call_payoff_at_expiry():
    contract = _contract("c1", OptionRight.CALL, 100.0)
    leg = _leg(contract, OptionSide.LONG, entry_price=5.0)
    # S=110 -> intrinsic 10 - premium 5 = +5 per share
    assert compute_leg_payoff(leg, 110.0) == 5.0
    # S=90 -> intrinsic 0 - premium 5 = -5 per share
    assert compute_leg_payoff(leg, 90.0) == -5.0


def test_short_call_payoff_at_expiry():
    contract = _contract("c1", OptionRight.CALL, 100.0)
    leg = _leg(contract, OptionSide.SHORT, entry_price=5.0)
    # S=110 -> -(10) + 5 = -5 per share
    assert compute_leg_payoff(leg, 110.0) == -5.0
    # S=90 -> -(0) + 5 = +5 per share
    assert compute_leg_payoff(leg, 90.0) == 5.0


def test_long_put_payoff_at_expiry():
    contract = _contract("p1", OptionRight.PUT, 100.0)
    leg = _leg(contract, OptionSide.LONG, entry_price=4.0)
    # S=90 -> intrinsic 10 - premium 4 = +6
    assert compute_leg_payoff(leg, 90.0) == 6.0
    # S=110 -> intrinsic 0 - premium 4 = -4
    assert compute_leg_payoff(leg, 110.0) == -4.0


def test_vertical_debit_spread_payoff():
    # Long 100 call @5, short 105 call @2 -> net debit 3 per share
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    c105 = _contract("c105", OptionRight.CALL, 105.0)
    legs = (
        _leg(c100, OptionSide.LONG, entry_price=5.0),
        _leg(c105, OptionSide.SHORT, entry_price=2.0),
    )
    prop = _proposal(legs)
    # S=100 -> long: -5, short: +2 => -3 per share * 100 multiplier = -300
    payoff = compute_structure_payoff(prop, 100.0)
    assert payoff == -3.0 * 100.0
    # S=110 -> long: 10-5=5, short: -(10-5)+2=-3 => +2 per share * 100 = 200
    payoff = compute_structure_payoff(prop, 110.0)
    assert payoff == 2.0 * 100.0


def test_max_loss_and_max_gain_debit_spread():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    c105 = _contract("c105", OptionRight.CALL, 105.0)
    legs = (
        _leg(c100, OptionSide.LONG, entry_price=5.0),
        _leg(c105, OptionSide.SHORT, entry_price=2.0),
    )
    prop = _proposal(legs, max_loss=300.0, max_gain=200.0)
    spot = 100.0

    assert max_loss_for_structure(prop, spot) == 300.0
    assert max_gain_for_structure(prop, spot) == 200.0


def test_break_even_points_debit_spread():
    # Long 100 call @5, short 105 call @2 -> net debit 3
    # Break-even: long call intrinsic must recover 3 debit -> S = 100 + 3 = 103
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    c105 = _contract("c105", OptionRight.CALL, 105.0)
    legs = (
        _leg(c100, OptionSide.LONG, entry_price=5.0),
        _leg(c105, OptionSide.SHORT, entry_price=2.0),
    )
    prop = _proposal(legs)
    bes = compute_break_even_points(prop, 100.0)
    assert len(bes) == 1
    assert abs(bes[0] - 103.0) < 1.0


def test_build_payoff_grid_shape():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    leg = _leg(c100, OptionSide.LONG, entry_price=5.0)
    prop = _proposal((leg,))
    grid = build_payoff_grid(prop, 100.0, grid_points=41, stress_pct=0.20)

    assert isinstance(grid, PayoffGrid)
    assert len(grid.underlying_prices) == len(grid.payoffs) == 41
    assert grid.max_loss < 0  # long call loses premium at low S
    assert grid.max_gain > 0  # long call gains at high S
    assert grid.underlying_prices[0] == pytest.approx(80.0)
    assert grid.underlying_prices[-1] == pytest.approx(120.0)
    assert 100.0 in grid.underlying_prices


def test_missing_entry_price_fail_closed():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    leg = _leg(c100, OptionSide.LONG, entry_price=None)
    prop = _proposal((leg,))
    grid = build_payoff_grid(prop, 100.0)
    assert grid.underlying_prices == ()
    assert grid.payoffs == ()
    assert grid.max_loss == 0.0
    assert grid.max_gain == 0.0
