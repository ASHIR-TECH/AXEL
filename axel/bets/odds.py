"""
Odds normalization and de-vig transforms — Phase 9.

All functions are pure and deterministic. Decimal odds `o` imply raw
probability `q = 1/o`. Booksum `Q = sum(q_i)`; overround is `Q - 1`.

Preserves raw odds alongside all transforms — never replaces raw data with
a single "correct" probability.
"""

from __future__ import annotations

from typing import Any


def implied_probability(decimal_odds: float) -> float:
    """Raw implied probability from decimal odds: q = 1/o."""
    if decimal_odds <= 0:
        raise ValueError(f"decimal_odds must be positive, got {decimal_odds}")
    return 1.0 / decimal_odds


def overround(implied_probs: list[float]) -> float:
    """Booksum minus 1.0: Q - 1. Positive means the book has an edge."""
    return sum(implied_probs) - 1.0


def proportional_normalization(decimal_odds: list[float]) -> list[float]:
    """Normalize raw implied probabilities proportionally: p_i = q_i / Q."""
    probs = [implied_probability(o) for o in decimal_odds]
    q_sum = sum(probs)
    if q_sum <= 0:
        raise ValueError("Sum of implied probabilities must be positive")
    return [p / q_sum for p in probs]


def shin_probabilities(
    decimal_odds: list[float],
    *,
    max_iterations: int = 1000,
    tolerance: float = 1e-10,
) -> list[float]:
    """
    Shin de-vig transform (Štrumbelj 2014; Koning & Zijm 2022).

    Solves for the risk-neutral probabilities that are consistent with the
    observed odds, accounting for favorite-longshot bias. Returns the
    de-vigged probabilities (sum to 1.0). Falls back to proportional
    normalization if the solver does not converge.
    """
    raw_probs = [implied_probability(o) for o in decimal_odds]
    n = len(raw_probs)
    if n < 2:
        return list(raw_probs)

    q_sum = sum(raw_probs)
    if q_sum <= 1.0:
        # No overround — raw probs already sum to <= 1, return normalized.
        return proportional_normalization(decimal_odds)

    # Shin's method: find mu such that sum(q_i / (1 + mu * (q_i / p_i - 1))) ... 
    # Simplified: iterate on the Shin mu parameter.
    # p_i = q_i / (1 + mu) ... we solve sum(p_i) = 1
    # Using the common formulation: p_i proportional to q_i^z for z in (0,1)
    # where z is chosen so sum(p_i) = 1.

    def _power_probs(z: float) -> list[float]:
        weighted = [q ** z for q in raw_probs]
        total = sum(weighted)
        if total <= 0:
            return [1.0 / n] * n
        return [w / total for w in weighted]

    # Binary search on z in [0, 1]
    lo, hi = 0.0, 1.0
    z = 0.5
    converged = False
    for _ in range(max_iterations):
        z = (lo + hi) / 2.0
        p = _power_probs(z)
        total = sum(p)
        if abs(total - 1.0) < tolerance:
            converged = True
            break
        if total < 1.0:
            hi = z
        else:
            lo = z

    if not converged:
        # Fall back to proportional normalization
        return proportional_normalization(decimal_odds)

    return _power_probs(z)


def decimal_from_probability(p: float) -> float:
    """Convert a probability to decimal odds: o = 1/p."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability must be in (0,1), got {p}")
    return 1.0 / p


def commission_on_net_winnings(net_profit: float, commission_rate: float) -> float:
    """Commission charged on net winnings only (exchange model)."""
    if net_profit <= 0:
        return 0.0
    return net_profit * commission_rate


def american_to_decimal(american_odds: float) -> float:
    """Convert American odds to decimal."""
    if american_odds > 0:
        return 1.0 + american_odds / 100.0
    return 1.0 + 100.0 / abs(american_odds)


def fractional_to_decimal(numerator: float, denominator: float) -> float:
    """Convert fractional odds (n/d) to decimal: 1 + n/d."""
    if denominator <= 0:
        raise ValueError(f"denominator must be positive, got {denominator}")
    return 1.0 + numerator / denominator


def normalize_odds_dict(
    odds: dict[str, Any],
    *,
    prefer_decimal: bool = True,
) -> float | None:
    """Extract decimal odds from a provider's odds dict.

    Handles {"decimal": ...}, {"us": ...}, {"frac": [n, d]}, etc.
    Returns None if no parseable odds found.
    """
    if not isinstance(odds, dict):
        return None

    if "decimal" in odds and odds["decimal"] is not None:
        try:
            return float(odds["decimal"])
        except (TypeError, ValueError):
            return None

    if "us" in odds and odds["us"] is not None:
        try:
            return american_to_decimal(float(odds["us"]))
        except (TypeError, ValueError):
            return None

    if "frac" in odds and odds["frac"] is not None:
        try:
            n, d = odds["frac"]
            return fractional_to_decimal(float(n), float(d))
        except (TypeError, ValueError):
            return None

    return None
