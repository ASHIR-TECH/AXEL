"""
Unit tests for Phase 8 Options pricing — Black-Scholes price and IV solver.
"""

import math

import pytest

from axel.options.pricing import (
    PricingInputs,
    black_scholes_price,
    implied_volatility,
    option_price_from_contract,
)
from axel.options.types import OptionContract, OptionRight


def _inputs(**overrides):
    defaults = {
        "spot": 100.0,
        "strike": 100.0,
        "time_to_expiry_years": 1.0,
        "risk_free_rate": 0.05,
        "volatility": 0.20,
        "dividend_yield": 0.0,
        "option_right": OptionRight.CALL,
    }
    defaults.update(overrides)
    return PricingInputs(**defaults)


def test_black_scholes_at_the_money_call():
    price, greeks = black_scholes_price(_inputs(option_right=OptionRight.CALL))
    assert price > 0
    assert 0.0 < greeks.delta < 1.0
    assert greeks.gamma > 0
    assert greeks.vega > 0
    assert greeks.model == "black_scholes"


def test_black_scholes_put_call_parity():
    call_price, _ = black_scholes_price(_inputs(option_right=OptionRight.CALL))
    put_price, _ = black_scholes_price(_inputs(option_right=OptionRight.PUT))
    S, K, T, r = 100.0, 100.0, 1.0, 0.05
    # C - P = S - K * e^{-rT}
    lhs = call_price - put_price
    rhs = S - K * math.exp(-r * T)
    assert abs(lhs - rhs) < 1e-6


def test_black_scholes_intrinsic_at_zero_vol():
    price, greeks = black_scholes_price(_inputs(volatility=0.0))
    assert price == max(100.0 - 100.0, 0.0)  # ATM -> 0 intrinsic
    assert greeks.vega == 0.0

    deep_itm = black_scholes_price(
        _inputs(volatility=0.0, strike=50.0, option_right=OptionRight.CALL)
    )
    assert deep_itm[0] == 50.0  # S - K


def test_iv_solver_recovers_input_vol():
    target_vol = 0.30
    inputs = _inputs(volatility=target_vol)
    market_price, _ = black_scholes_price(inputs)

    iv, info = implied_volatility(market_price, inputs)
    assert info["converged"]
    assert abs(iv - target_vol) < 1e-6
    assert abs(info["price_at_iv"] - market_price) < 1e-6


def test_iv_solver_rejects_out_of_bounds_price():
    inputs = _inputs()
    # Price below the zero-vol intrinsic floor -> no arbitrage-free IV.
    with pytest.raises(ValueError):
        implied_volatility(0.0001, inputs)


def test_iv_solver_rejects_nonpositive_market_price():
    inputs = _inputs()
    with pytest.raises(ValueError):
        implied_volatility(0.0, inputs)
    with pytest.raises(ValueError):
        implied_volatility(-1.0, inputs)


def test_option_price_from_contract():
    contract = OptionContract(
        contract_id="test_1",
        venue="test",
        underlying_id="spy",
        underlying_symbol="SPY",
        right=OptionRight.CALL,
        strike=100.0,
        expiry=__import__("datetime").datetime(2026, 12, 31, tzinfo=__import__("datetime").UTC),
    )
    price, greeks = option_price_from_contract(
        contract=contract,
        spot=100.0,
        volatility=0.20,
        risk_free_rate=0.05,
        time_to_expiry_years=1.0,
    )
    assert price > 0
    assert greeks.delta > 0
