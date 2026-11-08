"""Unit tests for the Phase 8 Heston stochastic-volatility model."""

import math

import pytest

from axel.options.heston import (
    HestonInputs,
    HestonParameters,
    heston_greeks,
    heston_price,
    heston_price_and_greeks,
)
from axel.options.pricing import PricingInputs, black_scholes_price
from axel.options.types import OptionRight


def _parameters(**overrides):
    defaults = {
        "v0": 0.04,
        "kappa": 1.5,
        "theta": 0.04,
        "xi": 0.30,
        "rho": -0.50,
    }
    defaults.update(overrides)
    return HestonParameters(**defaults)


def _inputs(right=OptionRight.CALL, **overrides):
    defaults = {
        "spot": 100.0,
        "strike": 100.0,
        "time_to_expiry_years": 1.0,
        "risk_free_rate": 0.05,
        "dividend_yield": 0.0,
        "option_right": right,
        "parameters": _parameters(),
    }
    defaults.update(overrides)
    return HestonInputs(**defaults)


def test_heston_reduces_to_black_scholes_when_xi_is_zero():
    # With deterministic variance and v0 == theta, Heston is exactly
    # Black-Scholes with sigma = sqrt(v0).
    params = _parameters(v0=0.04, theta=0.04, xi=0.0)
    for right in (OptionRight.CALL, OptionRight.PUT):
        heston = heston_price(_inputs(right=right, parameters=params))
        bs = black_scholes_price(
            PricingInputs(
                spot=100.0,
                strike=100.0,
                time_to_expiry_years=1.0,
                risk_free_rate=0.05,
                volatility=0.20,
                dividend_yield=0.0,
                option_right=right,
            )
        )[0]
        assert heston == pytest.approx(bs, abs=1e-12)


def test_heston_deterministic_variance_uses_time_average():
    # When v0 != theta the deterministic variance path mean-reverts, so the
    # effective Black-Scholes variance is the time average of v(t).
    params = _parameters(v0=0.09, theta=0.01, xi=0.0, kappa=2.0)
    time = 0.5
    decay = params.kappa * time
    factor = (1.0 - math.exp(-decay)) / decay
    average_variance = params.theta + (params.v0 - params.theta) * factor
    expected = black_scholes_price(
        PricingInputs(
            spot=100.0,
            strike=100.0,
            time_to_expiry_years=time,
            risk_free_rate=0.05,
            volatility=math.sqrt(average_variance),
            option_right=OptionRight.CALL,
        )
    )[0]
    actual = heston_price(
        _inputs(time_to_expiry_years=time, parameters=params)
    )
    assert actual == pytest.approx(expected, abs=1e-12)


def test_heston_put_call_parity():
    call = heston_price(_inputs())
    put = heston_price(_inputs(right=OptionRight.PUT))
    parity = call - put
    expected = 100.0 - 100.0 * math.exp(-0.05)
    assert parity == pytest.approx(expected, abs=1e-6)


def test_heston_price_is_stable_across_integration_grids():
    value = heston_price(_inputs())
    finer = heston_price(_inputs(), integration_limit=100.0, integration_steps=2000)
    assert value == pytest.approx(finer, rel=1e-6, abs=1e-8)


def test_heston_price_regression_lock():
    # Regression lock for the default integration grid.  The value is stable
    # for every limit >= 50 and step count >= 1,000 to better than 1e-6.
    assert heston_price(_inputs()) == pytest.approx(10.3311469192498, rel=1e-9)


def test_heston_greeks_match_independent_finite_differences():
    inputs = _inputs()
    greeks = heston_greeks(inputs)
    bump = 0.05

    up = heston_price(_inputs(spot=100.0 + bump))
    down = heston_price(_inputs(spot=100.0 - bump))
    delta = (up - down) / (2.0 * bump)
    gamma = (up - 2.0 * heston_price(inputs) + down) / (bump * bump)

    assert greeks.delta == pytest.approx(delta, rel=1e-3, abs=1e-4)
    assert greeks.gamma == pytest.approx(gamma, rel=1e-3, abs=1e-5)
    assert greeks.vega > 0.0
    assert greeks.iv == pytest.approx(0.2)


def test_heston_price_and_greeks_returns_heston_model():
    price, greeks = heston_price_and_greeks(_inputs())
    assert price > 0.0
    assert greeks.model == "heston"
    assert -1.0 < greeks.delta < 1.0
    assert greeks.gamma > 0.0


def test_heston_feller_condition():
    assert _parameters(kappa=2.0, theta=0.04, xi=0.30).feller_satisfied
    assert not _parameters(kappa=0.5, theta=0.01, xi=0.50).feller_satisfied


@pytest.mark.parametrize(
    "overrides",
    [
        {"v0": 0.0},
        {"kappa": 0.0},
        {"theta": -0.01},
        {"xi": -0.1},
        {"rho": 1.0},
        {"rho": -1.0},
    ],
)
def test_heston_rejects_invalid_parameters(overrides):
    with pytest.raises(ValueError):
        _parameters(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"spot": 0.0},
        {"strike": 0.0},
        {"time_to_expiry_years": 0.0},
        {"risk_free_rate": -0.01},
    ],
)
def test_heston_rejects_invalid_inputs(overrides):
    with pytest.raises(ValueError):
        _inputs(**overrides)
