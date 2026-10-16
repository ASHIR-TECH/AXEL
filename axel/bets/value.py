"""
Value engine — Phase 9.

Computes expected value (EV) and conservative EV for candidate bets,
subtracting commission and execution costs, applying uncertainty haircuts.
All functions are pure and deterministic.
"""

from __future__ import annotations


def expected_value(
    decimal_odds: float,
    estimated_prob: float,
    *,
    commission_rate: float = 0.0,
    execution_cost_bps: float = 0.0,
) -> float:
    """
    EV per unit stake for a simple win/loss bet at decimal odds `o`:

    EV = p * (o - 1) * (1 - commission) - (1 - p) - execution_cost

    Where:
      p * (o - 1)   = gross profit if won (per unit stake)
      (1 - p)       = loss of stake if lost
      commission    = charged on net winnings only
      execution_cost= bps of stake
    """
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal_odds must be > 1.0, got {decimal_odds}")
    if not 0.0 <= estimated_prob <= 1.0:
        raise ValueError(f"estimated_prob must be in [0,1], got {estimated_prob}")
    if not 0.0 <= commission_rate <= 1.0:
        raise ValueError(f"commission_rate must be in [0,1], got {commission_rate}")

    b = decimal_odds - 1.0  # net profit per unit stake if won
    gross_win = estimated_prob * b
    gross_loss = (1.0 - estimated_prob)
    ev = gross_win - gross_loss

    # Commission charged on net winnings only
    ev -= commission_rate * max(gross_win, 0.0)

    # Execution cost as basis points of stake
    ev -= execution_cost_bps / 10000.0

    return ev


def conservative_ev(
    decimal_odds: float,
    estimated_prob: float,
    market_implied_prob: float,
    *,
    commission_rate: float = 0.0,
    execution_cost_bps: float = 0.0,
    uncertainty_haircut: float = 0.0,
) -> float:
    """
    Conservative EV: apply an uncertainty haircut to the estimated
    probability, shrinking it toward the market-implied baseline.

    If uncertainty_haircut = 0: returns standard EV.
    If uncertainty_haircut = 1: estimated_prob = market_implied_prob
        (no edge assumed), EV = -commission - costs (always negative).

    The haircut moves probability toward the market-implied baseline,
    making the estimate more conservative when the model is uncertain.
    """
    if not 0.0 <= uncertainty_haircut <= 1.0:
        raise ValueError(f"uncertainty_haircut must be in [0,1], got {uncertainty_haircut}")
    if not 0.0 <= market_implied_prob <= 1.0:
        raise ValueError(f"market_implied_prob must be in [0,1], got {market_implied_prob}")

    # Shrink estimated probability toward market-implied baseline by haircut
    shrunk_prob = (
        estimated_prob * (1.0 - uncertainty_haircut)
        + market_implied_prob * uncertainty_haircut
    )

    return expected_value(
        decimal_odds,
        shrunk_prob,
        commission_rate=commission_rate,
        execution_cost_bps=execution_cost_bps,
    )


def break_even_probability(decimal_odds: float, *, commission_rate: float = 0.0) -> float:
    """
    The probability at which EV = 0 (break-even), accounting for commission.

    Solve: p * (o - 1) * (1 - c) - (1 - p) = 0
    p = 1 / ((o - 1) * (1 - c) + 1)
    """
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal_odds must be > 1.0, got {decimal_odds}")
    if not 0.0 <= commission_rate <= 1.0:
        raise ValueError(f"commission_rate must be in [0,1], got {commission_rate}")

    b = decimal_odds - 1.0
    return 1.0 / (b * (1.0 - commission_rate) + 1.0)
