"""Probabilistic and Deflated Sharpe Ratio (Bailey & Lopez de Prado)."""

from __future__ import annotations

from math import erf, exp, log, sqrt

EULER_MASCHERONI = 0.5772156649015329


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def norm_ppf(probability: float) -> float:
    """Acklam's rational approximation to the inverse standard normal CDF."""
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be in (0, 1)")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow, phigh = 0.02425, 1.0 - 0.02425
    if probability < plow:
        q = sqrt(-2.0 * log(probability))
        return (
            ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if probability > phigh:
        q = sqrt(-2.0 * log(1.0 - probability))
        return -(
            ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = probability - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    ) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def probabilistic_sharpe_ratio(
    observed_sr: float,
    benchmark_sr: float,
    *,
    n_returns: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probability the true Sharpe exceeds ``benchmark_sr``."""
    if n_returns < 2:
        return 0.0
    variance_term = 1.0 - skew * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr**2
    denominator = sqrt(max(variance_term, 1e-12))
    statistic = (observed_sr - benchmark_sr) * sqrt(n_returns - 1) / denominator
    return norm_cdf(statistic)


def expected_max_sharpe(n_trials: int, variance_of_sr: float) -> float:
    """Expected maximum Sharpe from ``n_trials`` under the null (DSR benchmark)."""
    if n_trials < 2 or variance_of_sr <= 0:
        return 0.0
    high = norm_ppf(1.0 - 1.0 / n_trials)
    low = norm_ppf(1.0 - 1.0 / (n_trials * exp(1.0)))
    return sqrt(variance_of_sr) * (
        (1.0 - EULER_MASCHERONI) * high + EULER_MASCHERONI * low
    )


def deflated_sharpe_ratio(
    observed_sr: float,
    *,
    n_trials: int,
    variance_of_sr: float,
    n_returns: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Sharpe probability after deflating for selection across ``n_trials``."""
    benchmark = expected_max_sharpe(n_trials, variance_of_sr)
    return probabilistic_sharpe_ratio(
        observed_sr,
        benchmark,
        n_returns=n_returns,
        skew=skew,
        kurtosis=kurtosis,
    )
