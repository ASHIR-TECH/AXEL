"""
Bankroll and stake sizing — Phase 9.

Fractional Kelly is a candidate, never a command. No full Kelly in
production. No martingale, no loss-chasing, no stake increases after losses.
"""

from __future__ import annotations


def fractional_kelly(
    decimal_odds: float,
    estimated_prob: float,
    *,
    kelly_fraction: float = 0.25,
    bankroll: float,
    max_stake_pct: float = 0.05,
    min_edge_pct: float = 0.0,
) -> dict:
    """
    Conservative fractional Kelly stake sizing.

    For a two-outcome bet, net profit per unit stake if won: b = o - 1.
    Full Kelly: f* = (b * p - (1 - p)) / b
    Fractional Kelly: f = kelly_fraction * f*

    Returns a dict with:
      - stake: currency units to bet
      - kelly_fraction_applied: the actual fraction used (0 = no bet)
      - full_kelly_fraction: the raw f* before scaling
      - is_no_bet: True if no bet should be placed
      - reason: why no bet (if applicable)
    """
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal_odds must be > 1.0, got {decimal_odds}")
    if not 0.0 <= estimated_prob <= 1.0:
        raise ValueError(f"estimated_prob must be in [0,1], got {estimated_prob}")
    if not 0.0 < kelly_fraction <= 1.0:
        raise ValueError(f"kelly_fraction must be in (0,1], got {kelly_fraction}")
    if bankroll <= 0:
        raise ValueError(f"bankroll must be positive, got {bankroll}")

    b = decimal_odds - 1.0  # net profit per unit stake if won
    p = estimated_prob
    q = 1.0 - p

    # Full Kelly fraction
    numerator = b * p - q
    full_kelly = numerator / b if b > 0 else 0.0

    # Clamp negative to zero — never bet negative
    full_kelly = max(0.0, full_kelly)

    # Cap at 1.0 — never bet more than bankroll
    full_kelly = min(1.0, full_kelly)

    # Apply fractional scaling
    fraction = kelly_fraction * full_kelly

    # Cap by max stake % of bankroll
    fraction = min(fraction, max_stake_pct)

    stake = bankroll * fraction

    # Minimum edge check: only bet if conservative edge exceeds threshold
    if numerator <= 0:
        return {
            "stake": 0.0,
            "kelly_fraction_applied": 0.0,
            "full_kelly_fraction": full_kelly,
            "is_no_bet": True,
            "reason": "non_positive_edge",
        }

    if min_edge_pct > 0 and numerator / b < min_edge_pct:
        return {
            "stake": 0.0,
            "kelly_fraction_applied": 0.0,
            "full_kelly_fraction": full_kelly,
            "is_no_bet": True,
            "reason": "edge_below_threshold",
        }

    return {
        "stake": stake,
        "kelly_fraction_applied": fraction,
        "full_kelly_fraction": full_kelly,
        "is_no_bet": False,
        "reason": "",
    }


def flat_stake(
    bankroll: float,
    *,
    stake_pct: float = 0.01,
    max_stake_pct: float = 0.05,
) -> dict:
    """
    Flat small stake — fixed percentage of bankroll, no Kelly.

    Simpler and more robust than Kelly when probabilities are uncertain.
    """
    if bankroll <= 0:
        raise ValueError(f"bankroll must be positive, got {bankroll}")
    if not 0.0 < stake_pct <= max_stake_pct:
        raise ValueError(f"stake_pct must be in (0, {max_stake_pct}], got {stake_pct}")

    stake = bankroll * stake_pct
    return {
        "stake": stake,
        "stake_pct": stake_pct,
        "bankroll": bankroll,
    }


def check_bankroll_limits(
    proposed_stake: float,
    bankroll: float,
    *,
    max_stake_per_bet_pct: float = 0.05,
    max_stake_per_event_pct: float = 0.10,
    max_daily_stake_pct: float = 0.15,
    open_exposure_pct: float = 0.0,
    daily_stake_used_pct: float = 0.0,
    max_correlated_exposure_pct: float = 0.20,
) -> dict:
    """
    Check bankroll and correlation limits before placing a bet.

    Returns a dict with:
      - allowed: bool
      - reason: str (empty if allowed)
      - max_allowed_stake: float
    """
    if bankroll <= 0:
        return {"allowed": False, "reason": "zero_or_negative_bankroll", "max_allowed_stake": 0.0}

    max_stake_per_bet = bankroll * max_stake_per_bet_pct
    max_stake_per_event = bankroll * max_stake_per_event_pct
    remaining_daily = bankroll * max_daily_stake_pct * (1.0 - daily_stake_used_pct)

    max_allowed = min(
        max_stake_per_bet,
        max_stake_per_event,
        remaining_daily,
        bankroll - bankroll * open_exposure_pct,  # reserve bankroll
    )

    if proposed_stake <= 0:
        return {"allowed": False, "reason": "non_positive_stake", "max_allowed_stake": max_allowed}

    if proposed_stake > max_allowed:
        return {
            "allowed": False,
            "reason": "stake_exceeds_limit",
            "max_allowed_stake": max_allowed,
        }

    if open_exposure_pct > max_correlated_exposure_pct:
        return {
            "allowed": False,
            "reason": "correlated_exposure_limit",
            "max_allowed_stake": max_allowed,
        }

    return {"allowed": True, "reason": "", "max_allowed_stake": max_allowed}
