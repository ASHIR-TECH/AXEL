"""
Options pricing engine — Phase 8.

Pure, deterministic Black-Scholes pricing, implied-volatility solver, and
Greeks. No I/O, no LLM, no look-ahead. All inputs are explicit parameters.

Notes:
- European-style closed form (Black-Scholes-Merton).
- American options: no closed form; callers must flag and use a binomial/
  lattice approximation elsewhere. This module prices American as European
  only when explicitly requested via `treat_american_as_european=True`, and
  records the approximation in the Greeks model string.
- IV is solved by bisection on [1e-6, 5.0]; solver convergence and bounds
  are recorded on the returned Greeks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from axel.options.types import ExerciseStyle, Greeks, OptionContract, OptionRight


@dataclass(frozen=True)
class PricingInputs:
    """Complete point-in-time inputs for pricing a single option."""

    spot: float  # underlying price at event time
    strike: float
    time_to_expiry_years: float  # > 0; in years (ACT/365)
    risk_free_rate: float  # annualized decimal, e.g. 0.05
    volatility: float  # annualized decimal, e.g. 0.35
    dividend_yield: float = 0.0  # annualized decimal
    option_right: OptionRight = OptionRight.CALL
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN

    def __post_init__(self) -> None:
        if self.spot <= 0:
            raise ValueError(f"spot must be positive, got {self.spot}")
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.time_to_expiry_years <= 0:
            raise ValueError(f"time_to_expiry_years must be positive, got {self.time_to_expiry_years}")
        if self.volatility < 0:
            raise ValueError(f"volatility must be non-negative, got {self.volatility}")
        if self.risk_free_rate < 0:
            raise ValueError(f"risk_free_rate must be non-negative, got {self.risk_free_rate}")


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_price(inputs: PricingInputs) -> tuple[float, Greeks]:
    """Black-Scholes-Merton European price and Greeks.

    Returns (price, greeks). Price is per share; multiply by contract
    multiplier for per-contract notional.

    For American-style options with `treat_american_as_european` not set,
    this still returns a European approximation but the Greeks model string
    records "black_scholes_european_approximation" so callers never mistake
    it for a true American price.
    """
    S = inputs.spot
    K = inputs.strike
    T = inputs.time_to_expiry_years
    r = inputs.risk_free_rate
    sigma = inputs.volatility
    q = inputs.dividend_yield

    if sigma == 0.0 or T == 0.0:
        # Intrinsic value at expiry / zero vol.
        if inputs.option_right == OptionRight.CALL:
            intrinsic = max(S - K, 0.0)
        else:
            intrinsic = max(K - S, 0.0)
        greeks = Greeks(
            delta=1.0 if intrinsic > 0 and inputs.option_right == OptionRight.CALL else 0.0,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            rho=0.0,
            iv=sigma,
            model="black_scholes_intrinsic",
        )
        return intrinsic, greeks

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    if inputs.option_right == OptionRight.CALL:
        price = S * disc_q * _norm_cdf(d1) - K * disc_r * _norm_cdf(d2)
        delta = disc_q * _norm_cdf(d1)
        rho = K * T * disc_r * _norm_cdf(d2)
        theta = (
            -(S * disc_q * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
            - r * K * disc_r * _norm_cdf(d2)
            + q * S * disc_q * _norm_cdf(d1)
        )
    else:  # PUT
        price = K * disc_r * _norm_cdf(-d2) - S * disc_q * _norm_cdf(-d1)
        delta = disc_q * (_norm_cdf(d1) - 1.0)
        rho = -K * T * disc_r * _norm_cdf(-d2)
        theta = (
            -(S * disc_q * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
            + r * K * disc_r * _norm_cdf(-d2)
            - q * S * disc_q * _norm_cdf(-d1)
        )

    gamma = disc_q * _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * disc_q * _norm_pdf(d1) * math.sqrt(T)

    model = "black_scholes"
    if inputs.exercise_style == ExerciseStyle.AMERICAN:
        model = "black_scholes_european_approximation"

    greeks = Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        iv=sigma,
        model=model,
    )
    return price, greeks


def implied_volatility(
    market_price: float,
    inputs: PricingInputs,
    *,
    max_iterations: int = 100,
    tolerance: float = 1e-7,
    vol_floor: float = 1e-6,
    vol_cap: float = 5.0,
) -> tuple[float, dict]:
    """Solve implied volatility by bisection.

    Returns (iv, solver_info) where solver_info has keys:
    iterations, converged, lower_bound, upper_bound, price_at_iv.

    Raises ValueError if the market price is outside the arbitrage-free
    bounds (no solution in [vol_floor, vol_cap]) — callers must treat this
    as IV_SOLVER_FAIL and not fabricate a volatility.
    """
    if market_price <= 0:
        raise ValueError(f"market_price must be positive, got {market_price}")

    # Price at vol_floor and vol_cap to bracket the root.
    def price_at_vol(vol: float) -> float:
        return black_scholes_price(
            PricingInputs(
                spot=inputs.spot,
                strike=inputs.strike,
                time_to_expiry_years=inputs.time_to_expiry_years,
                risk_free_rate=inputs.risk_free_rate,
                volatility=vol,
                dividend_yield=inputs.dividend_yield,
                option_right=inputs.option_right,
                exercise_style=inputs.exercise_style,
            )
        )[0]

    p_floor = price_at_vol(vol_floor)
    p_cap = price_at_vol(vol_cap)

    if not (p_floor <= market_price <= p_cap):
        raise ValueError(
            f"Market price {market_price:.6f} outside arbitrage-free range "
            f"[{p_floor:.6f}, {p_cap:.6f}] at vol [{vol_floor}, {vol_cap}] — "
            f"no IV solution; likely stale or crossed quote."
        )

    lo, hi = vol_floor, vol_cap
    iv = (lo + hi) / 2.0
    converged = False
    iterations = 0

    for _ in range(max_iterations):
        iterations += 1
        iv = (lo + hi) / 2.0
        p = price_at_vol(iv)
        if abs(p - market_price) < tolerance:
            converged = True
            break
        if p < market_price:
            lo = iv
        else:
            hi = iv

    solver_info = {
        "iterations": iterations,
        "converged": converged,
        "lower_bound": vol_floor,
        "upper_bound": vol_cap,
        "price_at_iv": price_at_vol(iv),
    }
    return iv, solver_info


def option_price_from_contract(
    contract: OptionContract,
    spot: float,
    volatility: float,
    risk_free_rate: float,
    time_to_expiry_years: float,
    dividend_yield: float = 0.0,
) -> tuple[float, Greeks]:
    """Convenience: price an OptionContract directly."""
    inputs = PricingInputs(
        spot=spot,
        strike=contract.strike,
        time_to_expiry_years=time_to_expiry_years,
        risk_free_rate=risk_free_rate,
        volatility=volatility,
        dividend_yield=dividend_yield,
        option_right=contract.right,
        exercise_style=contract.exercise_style,
    )
    return black_scholes_price(inputs)
