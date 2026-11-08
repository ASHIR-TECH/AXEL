"""
Heston stochastic-volatility option pricing — Phase 8.

Pure, deterministic implementation of the Heston (1993) model using the
numerically stable formulation of Albrecher et al. (2007) (the "little
Heston trap").  No I/O, no LLM, no look-ahead.

Model
-----
    dS_t = (r - q) * S_t * dt + sqrt(v_t) * S_t * dW_t
    dv_t = kappa * (theta - v_t) * dt + xi * sqrt(v_t) * dZ_t
    Corr(dW_t, dZ_t) = rho * dt

The characteristic function is evaluated with complex arithmetic and the
risk-neutral probabilities are recovered by one-dimensional numerical
integration (composite Simpson).  The price is returned in the same units
as :func:`axel.options.pricing.black_scholes_price` (per share).

Conventions
-----------
- ``xi`` is the volatility of variance (not volatility of volatility).
- ``v0`` and ``theta`` are variances, not volatilities.
- Greeks are computed by central finite differences.  ``vega`` is
  dPrice / d(sigma) where ``sigma = sqrt(v0)``, so it is comparable with
  Black-Scholes vega per 1.0 volatility.  ``theta`` is calendar decay,
  i.e. ``-dPrice / dT``.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass

from axel.options.pricing import PricingInputs, black_scholes_price
from axel.options.types import Greeks, OptionRight

# Integration defaults.  The integrand decays quickly for typical equity
# parameters; callers can tighten these for accuracy or loosen them for speed.
DEFAULT_INTEGRATION_LIMIT = 200.0
DEFAULT_INTEGRATION_STEPS = 4000


@dataclass(frozen=True)
class HestonParameters:
    """Heston model parameters.

    Attributes
    ----------
    v0:     initial instantaneous variance (> 0)
    kappa:  mean-reversion speed (> 0)
    theta:  long-run variance (> 0)
    xi:     volatility of variance (>= 0)
    rho:    correlation between price and variance Brownian motions,
            in (-1, 1)
    """

    v0: float
    kappa: float
    theta: float
    xi: float
    rho: float

    def __post_init__(self) -> None:
        if self.v0 <= 0:
            raise ValueError(f"v0 must be positive, got {self.v0}")
        if self.kappa <= 0:
            raise ValueError(f"kappa must be positive, got {self.kappa}")
        if self.theta <= 0:
            raise ValueError(f"theta must be positive, got {self.theta}")
        if self.xi < 0:
            raise ValueError(f"xi must be non-negative, got {self.xi}")
        if not -1.0 < self.rho < 1.0:
            raise ValueError(f"rho must be in (-1, 1), got {self.rho}")

    @property
    def feller_satisfied(self) -> bool:
        """True when ``2*kappa*theta >= xi^2`` (variance stays positive)."""
        return 2.0 * self.kappa * self.theta >= self.xi * self.xi

    @property
    def long_run_volatility(self) -> float:
        """Long-run annualised volatility, ``sqrt(theta)``."""
        return math.sqrt(self.theta)


@dataclass(frozen=True)
class HestonInputs:
    """Complete point-in-time inputs for pricing a single option."""

    spot: float
    strike: float
    time_to_expiry_years: float
    risk_free_rate: float
    dividend_yield: float
    option_right: OptionRight
    parameters: HestonParameters

    def __post_init__(self) -> None:
        if self.spot <= 0:
            raise ValueError(f"spot must be positive, got {self.spot}")
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.time_to_expiry_years <= 0:
            raise ValueError(
                f"time_to_expiry_years must be positive, got {self.time_to_expiry_years}"
            )
        if self.risk_free_rate < 0:
            raise ValueError(
                f"risk_free_rate must be non-negative, got {self.risk_free_rate}"
            )


def _simpson(f, lower: float, upper: float, steps: int) -> float:
    """Composite Simpson integration on a uniform grid."""
    if steps <= 0:
        raise ValueError("steps must be positive")
    if steps % 2:
        steps += 1
    width = upper - lower
    if width <= 0:
        return 0.0
    step = width / steps
    total = f(lower) + f(upper)
    for index in range(1, steps):
        total += (4.0 if index % 2 else 2.0) * f(lower + index * step)
    return total * step / 3.0


def _characteristic_function(
    u: float,
    inputs: HestonInputs,
) -> complex:
    """Risk-neutral characteristic function of ``ln(S_T)`` at real ``u``."""
    p = inputs.parameters
    iu = 1j * u
    a = p.kappa - p.rho * p.xi * iu
    d = cmath.sqrt(a * a + p.xi * p.xi * (iu + u * u))
    g = (a - d) / (a + d)
    exp_neg_dt = cmath.exp(-d * inputs.time_to_expiry_years)
    c_term = (p.kappa * p.theta / (p.xi * p.xi)) * (
        (a - d) * inputs.time_to_expiry_years
        - 2.0 * cmath.log((1.0 - g * exp_neg_dt) / (1.0 - g))
    )
    d_term = ((a - d) / (p.xi * p.xi)) * (
        (1.0 - exp_neg_dt) / (1.0 - g * exp_neg_dt)
    )
    drift = math.log(inputs.spot) + (inputs.risk_free_rate - inputs.dividend_yield) * (
        inputs.time_to_expiry_years
    )
    return cmath.exp(c_term + d_term * p.v0 + iu * drift)


def _risk_neutral_probability(
    inputs: HestonInputs,
    *,
    use_share_measure: bool,
    integration_limit: float,
    integration_steps: int,
) -> float:
    """Return P1 (share measure) or P2 (risk-neutral measure)."""
    log_strike = math.log(inputs.strike)

    def integrand(u: float) -> float:
        # The integrand has a finite non-zero limit at the origin (the
        # complex pole 1/u is purely imaginary), so evaluate a tiny offset
        # rather than returning zero.  Returning zero introduces an
        # O(1/steps) discontinuity and makes Simpson converge very slowly.
        if u == 0.0:
            u = 1e-12
        if use_share_measure:
            numerator = _characteristic_function(u - 1j, inputs)
            denominator = _characteristic_function(-1j, inputs)
            phi = numerator / denominator
        else:
            phi = _characteristic_function(u, inputs)
        value = cmath.exp(-1j * u * log_strike) * phi / (1j * u)
        return value.real

    integral = _simpson(integrand, 0.0, integration_limit, integration_steps)
    return 0.5 + integral / math.pi


def heston_price(
    inputs: HestonInputs,
    *,
    integration_limit: float = DEFAULT_INTEGRATION_LIMIT,
    integration_steps: int = DEFAULT_INTEGRATION_STEPS,
) -> float:
    """Heston price per share for a European call or put.

    When ``xi == 0`` the variance process is deterministic and the model
    reduces exactly to Black-Scholes with the time-averaged variance.  That
    limit is handled analytically to avoid the ``0/0`` in the characteristic
    function.
    """
    p = inputs.parameters
    if p.xi == 0.0:
        average_variance = _average_variance(p, inputs.time_to_expiry_years)
        bs_inputs = PricingInputs(
            spot=inputs.spot,
            strike=inputs.strike,
            time_to_expiry_years=inputs.time_to_expiry_years,
            risk_free_rate=inputs.risk_free_rate,
            volatility=math.sqrt(average_variance),
            dividend_yield=inputs.dividend_yield,
            option_right=inputs.option_right,
        )
        return black_scholes_price(bs_inputs)[0]

    probability_2 = _risk_neutral_probability(
        inputs,
        use_share_measure=False,
        integration_limit=integration_limit,
        integration_steps=integration_steps,
    )
    probability_1 = _risk_neutral_probability(
        inputs,
        use_share_measure=True,
        integration_limit=integration_limit,
        integration_steps=integration_steps,
    )

    discount_q = math.exp(-inputs.dividend_yield * inputs.time_to_expiry_years)
    discount_r = math.exp(-inputs.risk_free_rate * inputs.time_to_expiry_years)
    call = inputs.spot * discount_q * probability_1 - inputs.strike * discount_r * probability_2

    if inputs.option_right == OptionRight.CALL:
        return call
    # Put-call parity keeps the put consistent with the integrated call and
    # avoids a second pair of oscillatory integrals.
    return call - inputs.spot * discount_q + inputs.strike * discount_r


def _average_variance(parameters: HestonParameters, time: float) -> float:
    """Time-averaged deterministic variance when ``xi == 0``."""
    decay = parameters.kappa * time
    if abs(decay) < 1e-12:
        return parameters.v0
    factor = (1.0 - math.exp(-decay)) / decay
    return parameters.theta + (parameters.v0 - parameters.theta) * factor


def heston_greeks(
    inputs: HestonInputs,
    *,
    spot_bump: float | None = None,
    vol_bump: float = 1e-4,
    time_bump: float = 1e-5,
    rate_bump: float = 1e-5,
) -> Greeks:
    """Heston Greeks by central finite differences.

    ``vega`` is dPrice / d(sigma) with ``sigma = sqrt(v0)`` so it is directly
    comparable to Black-Scholes vega per 1.0 volatility.  ``theta`` is
    calendar decay (``-dPrice/dT``).
    """
    def price(**overrides) -> float:
        values = {
            "spot": inputs.spot,
            "strike": inputs.strike,
            "time_to_expiry_years": inputs.time_to_expiry_years,
            "risk_free_rate": inputs.risk_free_rate,
            "dividend_yield": inputs.dividend_yield,
            "option_right": inputs.option_right,
            "parameters": inputs.parameters,
        }
        values.update(overrides)
        return heston_price(HestonInputs(**values))

    h = spot_bump if spot_bump is not None else max(inputs.spot * 1e-3, 1e-6)
    up = price(spot=inputs.spot + h)
    down = price(spot=inputs.spot - h)
    delta = (up - down) / (2.0 * h)
    gamma = (up - 2.0 * price() + down) / (h * h)

    sigma = math.sqrt(inputs.parameters.v0)
    vega = 0.0
    if sigma > vol_bump:
        up_v0 = (sigma + vol_bump) ** 2
        down_v0 = max((sigma - vol_bump) ** 2, 1e-12)
        params_up = _replace_v0(inputs.parameters, up_v0)
        params_down = _replace_v0(inputs.parameters, down_v0)
        vega = (price(parameters=params_up) - price(parameters=params_down)) / (
            2.0 * vol_bump
        )

    theta = 0.0
    if inputs.time_to_expiry_years > time_bump:
        theta = -(
            price(time_to_expiry_years=inputs.time_to_expiry_years + time_bump)
            - price(time_to_expiry_years=inputs.time_to_expiry_years - time_bump)
        ) / (2.0 * time_bump)

    rho = (
        price(risk_free_rate=inputs.risk_free_rate + rate_bump)
        - price(risk_free_rate=max(inputs.risk_free_rate - rate_bump, 0.0))
    ) / (2.0 * rate_bump)

    return Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        iv=sigma,
        model="heston",
    )


def _replace_v0(parameters: HestonParameters, v0: float) -> HestonParameters:
    return HestonParameters(
        v0=v0,
        kappa=parameters.kappa,
        theta=parameters.theta,
        xi=parameters.xi,
        rho=parameters.rho,
    )


def heston_price_and_greeks(
    inputs: HestonInputs,
    *,
    integration_limit: float = DEFAULT_INTEGRATION_LIMIT,
    integration_steps: int = DEFAULT_INTEGRATION_STEPS,
) -> tuple[float, Greeks]:
    """Convenience: Heston price plus finite-difference Greeks."""
    price = heston_price(
        inputs,
        integration_limit=integration_limit,
        integration_steps=integration_steps,
    )
    greeks = heston_greeks(inputs)
    return price, greeks


__all__ = [
    "DEFAULT_INTEGRATION_LIMIT",
    "DEFAULT_INTEGRATION_STEPS",
    "HestonInputs",
    "HestonParameters",
    "heston_greeks",
    "heston_price",
    "heston_price_and_greeks",
]
