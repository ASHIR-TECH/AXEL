"""
Pure-stdlib event study for evaluating whether a market event moved a symbol.

Adapted from the market-model event study in ai-hedge-fund
(https://github.com/virattt/ai-hedge-fund, hedge_fund/event_study/), rewritten
without numpy/scipy so it stays dependency-free and deterministic.

The market model is OLS: ``R_stock = alpha + beta * R_market + AR``. Abnormal
returns (AR) are the residuals; cumulative abnormal return (CAR) sums AR over an
event window. This is research tooling only — it never places orders.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class MarketModelFit:
    """OLS fit of stock returns on market returns over an estimation window."""

    alpha: float
    beta: float
    r_squared: float
    residual_std: float
    n_obs: int


@dataclass(frozen=True)
class EventStudyResult:
    """Abnormal-return path and inference for one event."""

    fit: MarketModelFit
    abnormal_returns: tuple[float, ...]
    cumulative_abnormal_return: float
    mean_abnormal_return: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    n_obs: int


@dataclass(frozen=True)
class CrossSectionalSummary:
    """Aggregate CAR inference across many independent events."""

    mean_car: float
    median_car: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    hit_rate: float
    n_events: int


def _validate_pair(stock_returns: Sequence[float], market_returns: Sequence[float]) -> None:
    if len(stock_returns) != len(market_returns):
        raise ValueError("stock_returns and market_returns must be the same length")
    if len(stock_returns) < 3:
        raise ValueError("at least three observations are required")


def fit_market_model(
    stock_returns: Sequence[float],
    market_returns: Sequence[float],
) -> MarketModelFit:
    """Fit ``R_stock = alpha + beta * R_market`` by ordinary least squares."""
    _validate_pair(stock_returns, market_returns)
    n = len(stock_returns)
    mean_stock = sum(stock_returns) / n
    mean_market = sum(market_returns) / n

    covariance = sum(
        (stock - mean_stock) * (market - mean_market)
        for stock, market in zip(stock_returns, market_returns, strict=True)
    )
    market_variance = sum((market - mean_market) ** 2 for market in market_returns)
    if market_variance == 0.0:
        raise ValueError("market_returns must vary to fit a market model")

    beta = covariance / market_variance
    alpha = mean_stock - beta * mean_market

    residuals = [
        stock - (alpha + beta * market)
        for stock, market in zip(stock_returns, market_returns, strict=True)
    ]
    ss_res = sum(residual**2 for residual in residuals)
    ss_tot = sum((stock - mean_stock) ** 2 for stock in stock_returns)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    residual_std = math.sqrt(ss_res / (n - 2)) if n > 2 else 0.0

    return MarketModelFit(
        alpha=alpha,
        beta=beta,
        r_squared=r_squared,
        residual_std=residual_std,
        n_obs=n,
    )


def abnormal_returns(
    stock_returns: Sequence[float],
    market_returns: Sequence[float],
    fit: MarketModelFit,
) -> tuple[float, ...]:
    """Residual returns not explained by the fitted market model."""
    if len(stock_returns) != len(market_returns):
        raise ValueError("stock_returns and market_returns must be the same length")
    return tuple(
        stock - (fit.alpha + fit.beta * market)
        for stock, market in zip(stock_returns, market_returns, strict=True)
    )


def cumulative_abnormal_return(
    abnormal: Sequence[float],
    start: int = 0,
    end: int | None = None,
) -> float:
    """Sum abnormal returns over ``[start, end]`` (inclusive, Python slicing)."""
    if not abnormal:
        raise ValueError("abnormal returns must not be empty")
    stop = len(abnormal) if end is None else end + 1
    if start < 0 or stop > len(abnormal) or start >= stop:
        raise ValueError(f"invalid window [{start}, {stop - 1}] for {len(abnormal)} returns")
    return sum(abnormal[start:stop])


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    max_iterations = 200
    epsilon = 3e-14
    tiny = 1e-30

    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d

    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break

    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(log_beta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_two_sided_p_value(t_stat: float, degrees_of_freedom: int) -> float:
    """Two-sided p-value for a Student's t statistic, without scipy."""
    if degrees_of_freedom <= 0:
        raise ValueError("degrees_of_freedom must be positive")
    if t_stat == 0.0:
        return 1.0
    x = degrees_of_freedom / (degrees_of_freedom + t_stat * t_stat)
    return _regularized_incomplete_beta(degrees_of_freedom / 2.0, 0.5, x)


def one_sample_t_test(values: Sequence[float]) -> tuple[float, float]:
    """Test whether the mean of ``values`` differs from zero; returns (t, p)."""
    n = len(values)
    if n < 2:
        return 0.0, 1.0
    mean = sum(values) / n
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    if variance == 0.0:
        return (0.0, 1.0) if mean == 0.0 else (math.copysign(math.inf, mean), 0.0)
    t_stat = mean / math.sqrt(variance / n)
    return t_stat, student_t_two_sided_p_value(t_stat, n - 1)


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for the mean, reproducibly."""
    if not values:
        raise ValueError("values must not be empty")
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0,1)")

    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(rng.choices(values, k=n)) / n for _ in range(n_bootstrap)
    )
    tail = (1.0 - confidence) / 2.0
    return _percentile(means, tail), _percentile(means, 1.0 - tail)


def run_event_study(
    stock_returns: Sequence[float],
    market_returns: Sequence[float],
    event_index: int,
    *,
    estimation_start: int = -120,
    estimation_end: int = -11,
    event_start: int = 0,
    event_end: int = 5,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> EventStudyResult:
    """Fit on a pre-event window, then measure AR/CAR on the event window.

    Offsets are relative to ``event_index`` (day 0). The default estimation
    window ``[-120, -11]`` leaves a ten-day buffer before the event.
    """
    n = len(stock_returns)
    if n != len(market_returns):
        raise ValueError("stock_returns and market_returns must be the same length")

    estimation_lo = event_index + estimation_start
    estimation_hi = event_index + estimation_end + 1
    event_lo = event_index + event_start
    event_hi = event_index + event_end + 1
    if estimation_lo < 0 or estimation_hi > n:
        raise ValueError("estimation window falls outside the return series")
    if event_lo < 0 or event_hi > n or event_lo >= event_hi:
        raise ValueError("event window falls outside the return series")

    fit = fit_market_model(
        stock_returns[estimation_lo:estimation_hi],
        market_returns[estimation_lo:estimation_hi],
    )
    abnormal = abnormal_returns(
        stock_returns[event_lo:event_hi],
        market_returns[event_lo:event_hi],
        fit,
    )
    car = cumulative_abnormal_return(abnormal)
    mean_ar = car / len(abnormal)
    t_stat, p_value = one_sample_t_test(abnormal)
    ci_low, ci_high = bootstrap_mean_ci(
        abnormal, n_bootstrap=n_bootstrap, confidence=confidence, seed=seed
    )
    return EventStudyResult(
        fit=fit,
        abnormal_returns=abnormal,
        cumulative_abnormal_return=car,
        mean_abnormal_return=mean_ar,
        t_stat=t_stat,
        p_value=p_value,
        ci_low=ci_low,
        ci_high=ci_high,
        n_obs=len(abnormal),
    )


def cross_sectional_summary(
    cumulative_returns: Sequence[float],
    *,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> CrossSectionalSummary:
    """Aggregate CARs across events: mean, median, t-test, CI and hit rate."""
    if not cumulative_returns:
        raise ValueError("cumulative_returns must not be empty")

    ordered = sorted(cumulative_returns)
    n = len(ordered)
    mean_car = sum(ordered) / n
    median_car = (
        ordered[n // 2]
        if n % 2 == 1
        else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
    )
    t_stat, p_value = one_sample_t_test(ordered)
    ci_low, ci_high = bootstrap_mean_ci(
        ordered, n_bootstrap=n_bootstrap, confidence=confidence, seed=seed
    )
    hit_rate = sum(1 for value in ordered if value > 0.0) / n

    return CrossSectionalSummary(
        mean_car=mean_car,
        median_car=median_car,
        t_stat=t_stat,
        p_value=p_value,
        ci_low=ci_low,
        ci_high=ci_high,
        hit_rate=hit_rate,
        n_events=n,
    )
