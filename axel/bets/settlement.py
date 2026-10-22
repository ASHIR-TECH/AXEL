"""
Settlement engine — Phase 9.

Settlement rules for win/loss/push/void/dead heat. The payout function is
settlement-specific — not valid unchanged for all contract types.
All functions are pure and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettlementResult:
    """Result of settling a bet."""

    stake: float
    price: float  # decimal odds at placement
    commission_rate: float
    outcome: str  # "win", "loss", "push", "void", "dead_heat"
    payout: float  # total returned to bettor (stake + profit)
    profit: float  # net profit (can be negative)
    commission: float  # commission charged on net winnings
    reason: str = ""


def compute_payout(
    stake: float,
    decimal_odds: float,
    outcome: str,
    *,
    commission_rate: float = 0.0,
    dead_heat_divisor: int = 1,
) -> SettlementResult:
    """
    Compute settlement payout for a bet.

    Handles:
      - "win":   profit = stake * (odds - 1), commission on net winnings
      - "loss":  profit = -stake, payout = 0
      - "push":  profit = 0, payout = stake (stake returned)
      - "void":  profit = 0, payout = stake (stake returned, no action)
      - "dead_heat": profit split by divisor (e.g. 2-way dead heat halves odds)

    Commission is charged on net winnings only (exchange model).
    """
    if stake <= 0:
        raise ValueError(f"stake must be positive, got {stake}")
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal_odds must be > 1.0, got {decimal_odds}")
    if not 0.0 <= commission_rate <= 1.0:
        raise ValueError(f"commission_rate must be in [0,1], got {commission_rate}")
    if dead_heat_divisor < 1:
        raise ValueError(f"dead_heat_divisor must be >= 1, got {dead_heat_divisor}")

    outcome = outcome.lower()

    if outcome == "loss":
        return SettlementResult(
            stake=stake,
            price=decimal_odds,
            commission_rate=commission_rate,
            outcome="loss",
            payout=0.0,
            profit=-stake,
            commission=0.0,
            reason="Bet lost",
        )

    if outcome in ("push", "void"):
        return SettlementResult(
            stake=stake,
            price=decimal_odds,
            commission_rate=commission_rate,
            outcome=outcome,
            payout=stake,
            profit=0.0,
            commission=0.0,
            reason=f"Bet {outcome} — stake returned",
        )

    if outcome == "dead_heat":
        # Dead heat: odds are divided by the number of tied competitors
        effective_odds = 1.0 + (decimal_odds - 1.0) / dead_heat_divisor
        gross_profit = stake * (effective_odds - 1.0)
        commission = commission_rate * max(gross_profit, 0.0)
        net_profit = gross_profit - commission
        payout = stake + net_profit
        return SettlementResult(
            stake=stake,
            price=decimal_odds,
            commission_rate=commission_rate,
            outcome="dead_heat",
            payout=payout,
            profit=net_profit,
            commission=commission,
            reason=f"Dead heat (divisor={dead_heat_divisor}) — odds halved",
        )

    if outcome == "win":
        gross_profit = stake * (decimal_odds - 1.0)
        commission = commission_rate * max(gross_profit, 0.0)
        net_profit = gross_profit - commission
        payout = stake + net_profit
        return SettlementResult(
            stake=stake,
            price=decimal_odds,
            commission_rate=commission_rate,
            outcome="win",
            payout=payout,
            profit=net_profit,
            commission=commission,
            reason="Bet won",
        )

    raise ValueError(f"Unknown settlement outcome: {outcome!r}")


def settle_multiple(
    bets: list[tuple[float, float, str]],
    *,
    commission_rate: float = 0.0,
) -> list[SettlementResult]:
    """Settle multiple bets at once."""
    return [
        compute_payout(stake, odds, outcome, commission_rate=commission_rate)
        for stake, odds, outcome in bets
    ]
