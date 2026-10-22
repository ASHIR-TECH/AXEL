"""
Options payoff engine — Phase 8.

Pure functions for multi-leg options payoff grids, max loss/gain, and
break-even points. No I/O, no LLM. Underlying price grid is constructed
from the current spot plus/minus a stress range.
"""

from __future__ import annotations

from dataclasses import dataclass

from axel.options.types import OptionLeg, OptionSide, OptionsProposal


@dataclass(frozen=True)
class PayoffGrid:
    """Payoff grid across a range of underlying prices at expiry.

    underlying_prices: sorted list of underlying prices
    payoffs: net P&L at each underlying price (same length)
    max_loss: worst-case (most negative) payoff
    max_gain: best-case payoff
    break_even_points: underlying prices where payoff crosses zero
    """

    underlying_prices: tuple[float, ...]
    payoffs: tuple[float, ...]
    max_loss: float
    max_gain: float
    break_even_points: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.underlying_prices) != len(self.payoffs):
            raise ValueError("underlying_prices and payoffs must have the same length")


def compute_leg_payoff(leg: OptionLeg, underlying_price: float) -> float:
    """Payoff of a single option leg at expiry for one underlying price.

    Per contract, before multiplying by ratio and multiplier.
    Long call: max(S - K, 0) - premium
    Long put:  max(K - S, 0) - premium
    Short call: -max(S - K, 0) + premium
    Short put: -max(K - S, 0) + premium
    """
    contract = leg.contract
    premium = leg.entry_price
    if premium is None:
        # Cannot compute payoff without a known entry price — fail closed.
        raise ValueError(f"Leg {contract.contract_id} has no entry_price; payoff undefined.")

    intrinsic: float
    if contract.right.name.lower() == "call":  # OptionRight.CALL
        intrinsic = max(underlying_price - contract.strike, 0.0)
    else:
        intrinsic = max(contract.strike - underlying_price, 0.0)

    side_multiplier = 1.0 if leg.side == OptionSide.LONG else -1.0
    return side_multiplier * (intrinsic - premium)


def compute_structure_payoff(
    proposal: OptionsProposal,
    underlying_price: float,
) -> float:
    """Net payoff of the whole structure at one underlying price (expiry)."""
    total = 0.0
    for leg in proposal.legs:
        leg_payoff = compute_leg_payoff(leg, underlying_price)
        total += leg_payoff * leg.ratio * leg.contract.multiplier
    return total


def build_payoff_grid(
    proposal: OptionsProposal,
    spot: float,
    *,
    grid_points: int = 201,
    stress_pct: float = 0.50,
) -> PayoffGrid:
    """Build a payoff grid from spot*(1-stress_pct) to spot*(1+stress_pct).

    grid_points must be odd so the spot price is included in the grid.
    """
    if grid_points < 3 or grid_points % 2 == 0:
        grid_points = 201

    lo = spot * (1.0 - stress_pct)
    hi = spot * (1.0 + stress_pct)
    step = (hi - lo) / (grid_points - 1)

    underlying_prices: list[float] = []
    payoffs: list[float] = []

    for i in range(grid_points):
        s = lo + i * step
        try:
            payoff = compute_structure_payoff(proposal, s)
        except ValueError:
            # Missing entry price — cannot build a complete grid. Fail closed.
            underlying_prices = ()
            payoffs = ()
            return PayoffGrid(
                underlying_prices=(),
                payoffs=(),
                max_loss=0.0,
                max_gain=0.0,
                break_even_points=(),
            )
        underlying_prices.append(s)
        payoffs.append(payoff)

    if not payoffs:
        return PayoffGrid(
            underlying_prices=(),
            payoffs=(),
            max_loss=0.0,
            max_gain=0.0,
            break_even_points=(),
        )

    max_loss = min(payoffs)
    max_gain = max(payoffs)

    # Break-even points: where payoff changes sign between adjacent grid points.
    break_even_points: list[float] = []
    for i in range(len(payoffs) - 1):
        p0, p1 = payoffs[i], payoffs[i + 1]
        if p0 == 0.0:
            break_even_points.append(underlying_prices[i])
        elif p0 * p1 < 0:
            # Linear interpolation of the zero crossing.
            s0, s1 = underlying_prices[i], underlying_prices[i + 1]
            t = p0 / (p0 - p1)
            break_even_points.append(s0 + t * (s1 - s0))

    return PayoffGrid(
        underlying_prices=tuple(underlying_prices),
        payoffs=tuple(payoffs),
        max_loss=max_loss,
        max_gain=max_gain,
        break_even_points=tuple(sorted(set(break_even_points))),
    )


def max_loss_for_structure(proposal: OptionsProposal, spot: float) -> float:
    """Worst-case loss as a positive magnitude (e.g. 300.0 means lose up to $300).

    Returns 0.0 if the structure cannot lose money.
    """
    grid = build_payoff_grid(proposal, spot)
    worst = grid.max_loss  # negative or zero
    return abs(min(0.0, worst))


def max_gain_for_structure(proposal: OptionsProposal, spot: float) -> float:
    """Best-case gain as a positive magnitude."""
    grid = build_payoff_grid(proposal, spot)
    return max(0.0, grid.max_gain)


def compute_break_even_points(
    proposal: OptionsProposal,
    spot: float,
) -> tuple[float, ...]:
    """Convenience wrapper around build_payoff_grid break-evens."""
    return build_payoff_grid(proposal, spot).break_even_points
