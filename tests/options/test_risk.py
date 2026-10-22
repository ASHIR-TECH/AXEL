"""
Unit tests for Phase 8 Options risk limits — defined-risk validation.
"""

from datetime import UTC, datetime

import pytest

from axel.options.risk import (
    OPTIONS_LIMITS,
    OptionsRiskLimits,
    is_defined_risk_structure,
    validate_defined_risk,
)
from axel.options.types import (
    OptionContract,
    OptionLeg,
    OptionRight,
    OptionSide,
    OptionsProposal,
)


def _contract(contract_id: str, right: OptionRight, strike: float) -> OptionContract:
    return OptionContract(
        contract_id=contract_id,
        venue="test",
        underlying_id="spy",
        underlying_symbol="SPY",
        right=right,
        strike=strike,
        expiry=datetime(2026, 11, 15, tzinfo=UTC),  # ~30 DTE from Oct 2026
    )


def _leg(contract: OptionContract, side: OptionSide, entry_price: float) -> OptionLeg:
    return OptionLeg(contract=contract, side=side, entry_price=entry_price)


def _proposal(
    legs: tuple[OptionLeg, ...],
    max_loss: float | None = -300.0,
    max_gain: float | None = 200.0,
) -> OptionsProposal:
    return OptionsProposal(
        proposal_id="prop_1",
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


def test_defined_risk_debit_spread_passes():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    c105 = _contract("c105", OptionRight.CALL, 105.0)
    legs = (
        _leg(c100, OptionSide.LONG, entry_price=5.0),
        _leg(c105, OptionSide.SHORT, entry_price=2.0),
    )
    prop = _proposal(legs, max_loss=300.0, max_gain=200.0)
    passed, _reasons, codes = validate_defined_risk(prop, spot=100.0)
    assert passed
    assert "DEFINED_RISK_OK" in codes


def test_defined_risk_long_only_passes():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    prop = _proposal((_leg(c100, OptionSide.LONG, entry_price=5.0),), max_loss=500.0, max_gain=5000.0)
    passed, _reasons, _codes = validate_defined_risk(prop, spot=100.0)
    assert passed


def test_undefined_risk_all_short_rejected():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    prop = _proposal(
        (_leg(c100, OptionSide.SHORT, entry_price=5.0),),
        max_loss=None,
        max_gain=500.0,
    )
    passed, _reasons, codes = validate_defined_risk(prop, spot=100.0)
    assert not passed
    assert "UNDEFINED_RISK" in codes


def test_empty_structure_rejected():
    prop = _proposal((), max_loss=0.0, max_gain=0.0)
    passed, _reasons, codes = validate_defined_risk(prop, spot=100.0)
    assert not passed
    assert "EMPTY_STRUCTURE" in codes


def test_too_many_legs_rejected():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    many_legs = tuple(
        _leg(c100, OptionSide.LONG, entry_price=5.0) for _ in range(5)
    )
    prop = _proposal(many_legs)
    passed, _reasons, codes = validate_defined_risk(prop, spot=100.0)
    assert not passed
    assert "TOO_MANY_LEGS" in codes


def test_max_loss_mismatch_rejected():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    c105 = _contract("c105", OptionRight.CALL, 105.0)
    # Claim max_loss=999 but actual grid max_loss is 300 -> mismatch
    legs = (
        _leg(c100, OptionSide.LONG, entry_price=5.0),
        _leg(c105, OptionSide.SHORT, entry_price=2.0),
    )
    prop = _proposal(legs, max_loss=999.0, max_gain=200.0)
    passed, _reasons, codes = validate_defined_risk(prop, spot=100.0)
    assert not passed
    assert "MAX_LOSS_MISMATCH" in codes


def test_is_defined_risk_structure_long_only():
    c100 = _contract("c100", OptionRight.CALL, 100.0)
    prop = _proposal((_leg(c100, OptionSide.LONG, entry_price=5.0),), max_loss=500.0, max_gain=5000.0)
    assert is_defined_risk_structure(prop)

    # max_loss unset -> not defined risk
    prop2 = _proposal((_leg(c100, OptionSide.LONG, entry_price=5.0),), max_loss=None, max_gain=None)
    assert not is_defined_risk_structure(prop2)


def test_limits_are_frozen():
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        OPTIONS_LIMITS.MAX_POSITION_PCT = 0.10  # type: ignore[misc]

    custom = OptionsRiskLimits(MAX_POSITION_PCT=0.10)
    assert custom.MAX_POSITION_PCT == 0.10
    assert OPTIONS_LIMITS.MAX_POSITION_PCT == 0.05  # unchanged
