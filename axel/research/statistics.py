"""Statistical reliability for Phase 10."""

from __future__ import annotations

import math


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    if p <= 0.0:
        return -10e18
    if p >= 1.0:
        return 10e18
    if p < 0.5:
        return -norm_ppf(1.0 - p)
    t = math.sqrt(-2.0 * math.log(1.0 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)


def expected_max_sharpe(num_trials: int, mean_sharpe: float = 0.0, std_sharpe: float = 1.0) -> float:
    if num_trials <= 1:
        return mean_sharpe
    gamma = 0.5772156649
    emn = (1.0 - gamma) * norm_ppf(1.0 - 1.0 / num_trials) + gamma * norm_ppf(1.0 - 1.0 / (num_trials * math.e))
    return mean_sharpe + std_sharpe * emn


def sharpe_ratio_stderr(sharpe: float, n: int, skew: float = 0.0, kurtosis: float = 3.0) -> float:
    if n <= 1:
        return float("inf")
    var = (1.0 + 0.5 * sharpe * sharpe - skew * sharpe + (kurtosis - 3.0) / 4.0 * sharpe * sharpe) / n
    if var < 0:
        var = (1.0 + 0.5 * sharpe * sharpe) / n
    return math.sqrt(var)


def deflated_sharpe_ratio(observed_sharpe: float, n: int, num_trials: int, skew: float = 0.0, kurtosis: float = 3.0) -> float:
    if n <= 1 or num_trials < 1:
        return observed_sharpe
    sr_std = sharpe_ratio_stderr(observed_sharpe, n, skew, kurtosis)
    if sr_std <= 0:
        return float("-inf")
    sr0 = expected_max_sharpe(num_trials)
    dsr = (observed_sharpe - sr0) / sr_std
    return dsr


def multiple_testing_correction(p_values: list[float], method: str = "fdr_bh") -> list[float]:
    if not p_values:
        return []
    if method == "bonferroni":
        m = len(p_values)
        return [min(p * m, 1.0) for p in p_values]
    elif method == "fdr_bh" or method == "bh":
        m = len(p_values)
        indexed = sorted([(p, i) for i, p in enumerate(p_values)], key=lambda x: x[0])
        adj = [0.0] * m
        for j in range(m - 1, -1, -1):
            p_j, i_j = indexed[j]
            rank = j + 1
            adj_p = min(p_j * m / rank, 1.0)
            if j < m - 1:
                adj_p = min(adj_p, adj[indexed[j + 1][1]])
            adj[i_j] = adj_p
        return adj
    return list(p_values)


def probabilistic_sharpe_ratio(observed_sharpe: float, sr_benchmark: float, n: int, skew: float = 0.0, kurtosis: float = 3.0) -> float:
    if n <= 1:
        return 0.0
    sr_std = sharpe_ratio_stderr(observed_sharpe, n, skew, kurtosis)
    if sr_std <= 0:
        return 0.0
    z = (observed_sharpe - sr_benchmark) / sr_std
    return norm_cdf(z)
