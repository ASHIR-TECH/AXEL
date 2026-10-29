"""Statistical reliability and multiple-testing diagnostics for Phase 10."""

from __future__ import annotations

import math
from typing import Any

from axel.validation.deflated_sharpe import norm_cdf, norm_ppf


def expected_max_sharpe(num_trials: int, mean_sharpe: float = 0.0, std_sharpe: float = 1.0) -> float:
    """Expected maximum Sharpe ratio across N independent trials under the null."""
    if num_trials <= 1 or std_sharpe <= 0:
        return mean_sharpe
    euler_mascheroni = 0.5772156649015329
    high = norm_ppf(1.0 - 1.0 / num_trials)
    low = norm_ppf(1.0 - 1.0 / (num_trials * math.e))
    emn = (1.0 - euler_mascheroni) * high + euler_mascheroni * low
    return mean_sharpe + std_sharpe * emn


def sharpe_ratio_stderr(sharpe: float, n: int, skew: float = 0.0, kurtosis: float = 3.0) -> float:
    """Standard error of the Sharpe ratio estimator with skewness and kurtosis correction."""
    if n <= 1:
        return float("inf")
    var = (1.0 + 0.5 * sharpe * sharpe - skew * sharpe + (kurtosis - 3.0) / 4.0 * sharpe * sharpe) / n
    if var < 0:
        var = (1.0 + 0.5 * sharpe * sharpe) / n
    return math.sqrt(var)


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    sr_benchmark: float,
    n: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probabilistic Sharpe Ratio: probability that true Sharpe exceeds sr_benchmark."""
    if n <= 1:
        return 0.0
    sr_std = sharpe_ratio_stderr(observed_sharpe, n, skew, kurtosis)
    if sr_std <= 0 or math.isinf(sr_std):
        return 0.0
    z = (observed_sharpe - sr_benchmark) / sr_std
    return float(norm_cdf(z))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n: int,
    num_trials: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    variance_of_sr: float = 1.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014).
    
    Computes the probability (in [0, 1]) that the observed Sharpe ratio is not merely
    the result of selection bias / multiple testing across `num_trials`.
    """
    if n <= 1 or num_trials < 1:
        return 0.0
    std_sharpe = math.sqrt(variance_of_sr) if variance_of_sr > 0 else 1.0
    sr0 = expected_max_sharpe(num_trials, mean_sharpe=0.0, std_sharpe=std_sharpe)
    return probabilistic_sharpe_ratio(observed_sharpe, sr0, n, skew=skew, kurtosis=kurtosis)


def multiple_testing_correction(p_values: list[float], method: str = "fdr_bh") -> list[float]:
    """Adjust p-values for multiple testing via Bonferroni or Benjamini-Hochberg (FDR)."""
    if not p_values:
        return []
    m = len(p_values)
    if method == "bonferroni":
        return [min(p * m, 1.0) for p in p_values]
    elif method in ("fdr_bh", "bh"):
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


def brier_score(forecasts: list[float], outcomes: list[int | float]) -> float:
    """Mean squared error between predicted probabilities and binary outcomes (PRD §6.7)."""
    if not forecasts or len(forecasts) != len(outcomes):
        raise ValueError("forecasts and outcomes must be non-empty and of equal length")
    total_sq_err = sum((f - o) ** 2 for f, o in zip(forecasts, outcomes, strict=True))
    return total_sq_err / len(forecasts)


def log_loss(forecasts: list[float], outcomes: list[int | float], eps: float = 1e-15) -> float:
    """Cross-entropy / log-loss for probabilistic forecasts."""
    if not forecasts or len(forecasts) != len(outcomes):
        raise ValueError("forecasts and outcomes must be non-empty and of equal length")
    total_loss = 0.0
    for f, o in zip(forecasts, outcomes, strict=True):
        clipped_f = max(eps, min(1.0 - eps, float(f)))
        y = float(o)
        total_loss += -(y * math.log(clipped_f) + (1.0 - y) * math.log(1.0 - clipped_f))
    return total_loss / len(forecasts)


def calibration_metrics(
    forecasts: list[float],
    outcomes: list[int | float],
    n_bins: int = 5,
) -> dict[str, Any]:
    """Reliability and calibration analysis for probabilistic models (PRD §6.7)."""
    if not forecasts or len(forecasts) != len(outcomes):
        raise ValueError("forecasts and outcomes must be non-empty and of equal length")

    n = len(forecasts)
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    bin_width = 1.0 / n_bins

    for f, o in zip(forecasts, outcomes, strict=True):
        f_clamped = max(0.0, min(1.0, float(f)))
        b_idx = min(int(f_clamped / bin_width), n_bins - 1)
        bins[b_idx].append((f_clamped, float(o)))

    table = []
    ece = 0.0  # Expected Calibration Error
    mce = 0.0  # Maximum Calibration Error

    for idx, b_items in enumerate(bins):
        low, high = idx * bin_width, (idx + 1) * bin_width
        count = len(b_items)
        if count == 0:
            table.append({"bin": f"[{low:.2f}, {high:.2f}]", "count": 0, "avg_prob": 0.0, "empirical_freq": 0.0})
            continue
        avg_prob = sum(item[0] for item in b_items) / count
        empirical_freq = sum(item[1] for item in b_items) / count
        abs_diff = abs(avg_prob - empirical_freq)
        ece += (count / n) * abs_diff
        mce = max(mce, abs_diff)
        table.append({
            "bin": f"[{low:.2f}, {high:.2f}]",
            "count": count,
            "avg_prob": round(avg_prob, 4),
            "empirical_freq": round(empirical_freq, 4),
            "diff": round(abs_diff, 4),
        })

    return {
        "brier_score": round(brier_score(forecasts, outcomes), 6),
        "log_loss": round(log_loss(forecasts, outcomes), 6),
        "expected_calibration_error": round(ece, 6),
        "max_calibration_error": round(mce, 6),
        "reliability_table": table,
    }
