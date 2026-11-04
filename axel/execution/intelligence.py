"""
Execution Intelligence and Almgren-Chriss Framework for Phase 12.

Implements Section 8 and Section 5.3 of the Developer 2 Technical Research Specification:
- Implementation Shortfall (§8.2): signed price shortfall, delay cost, unfilled opportunity cost.
- Market Impact Model (§8.3): Impact = eta * sigma * sqrt(Q / V).
- Almgren-Chriss Optimal Execution Framework (§5.3, §8.4):
    Minimise: E[C] + lambda * Var(C)
  Computes optimal liquidation and accumulation schedules, balancing market impact
  against variance of execution price.
- Baselines comparison (§8.5): Almgren-Chriss vs TWAP (time-weighted) and VWAP (volume-weighted).

Zero LLM execution authority. Generates execution trajectories and cost estimates only;
deterministic execution services handle authorized submissions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

# ---------------------------------------------------------------------------
# 1. Implementation Shortfall (§8.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImplementationShortfallBreakdown:
    """Detailed breakdown of execution costs and shortfall components (§8.2)."""

    decision_price: float
    arrival_price: float
    execution_price: float
    final_price: float
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    side: str  # "buy" or "sell"
    fees: float = 0.0

    @property
    def side_sign(self) -> float:
        return 1.0 if self.side.lower() in ("buy", "long") else -1.0

    @property
    def price_impact_shortfall_usd(self) -> float:
        """Price shortfall on filled shares: side_sign * filled * (P_exec - P_arrival)."""
        return self.side_sign * self.filled_quantity * (self.execution_price - self.arrival_price)

    @property
    def delay_cost_usd(self) -> float:
        """Cost of delay between decision and arrival: side_sign * filled * (P_arrival - P_decision)."""
        return self.side_sign * self.filled_quantity * (self.arrival_price - self.decision_price)

    @property
    def opportunity_cost_usd(self) -> float:
        """Cost of unexecuted shares: side_sign * unfilled * (P_final - P_decision)."""
        return self.side_sign * self.unfilled_quantity * (self.final_price - self.decision_price)

    @property
    def total_implementation_shortfall_usd(self) -> float:
        """Total shortfall: price impact + delay + opportunity cost + fees."""
        return (
            self.price_impact_shortfall_usd
            + self.delay_cost_usd
            + self.opportunity_cost_usd
            + self.fees
        )

    @property
    def shortfall_bps(self) -> float:
        """Total shortfall in basis points relative to initial notional."""
        initial_notional = self.requested_quantity * self.decision_price
        if initial_notional <= 0:
            return 0.0
        return (self.total_implementation_shortfall_usd / initial_notional) * 10_000.0


def calculate_implementation_shortfall(
    decision_price: float,
    arrival_price: float,
    execution_price: float,
    final_price: float,
    requested_quantity: float,
    filled_quantity: float,
    side: str,
    fees: float = 0.0,
) -> ImplementationShortfallBreakdown:
    """Calculate complete Almgren-style implementation shortfall."""
    unfilled = max(0.0, requested_quantity - filled_quantity)
    return ImplementationShortfallBreakdown(
        decision_price=decision_price,
        arrival_price=arrival_price,
        execution_price=execution_price,
        final_price=final_price,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        unfilled_quantity=unfilled,
        side=side,
        fees=fees,
    )


# ---------------------------------------------------------------------------
# 2. Square-Root Market Impact Model (§8.3)
# ---------------------------------------------------------------------------


def estimate_square_root_impact(
    quantity: float,
    market_volume: float,
    volatility: float,
    eta: float = 0.142,
) -> float:
    """Estimate price impact using the square-root law (§8.3).

    Impact = eta * sigma * sqrt(Q / V)

    Parameters
    ----------
    quantity : order quantity Q.
    market_volume : reference market volume V (e.g. daily volume or interval volume).
    volatility : price volatility sigma over the same interval.
    eta : calibrated impact coefficient (typically between 0.1 and 0.5).

    Returns
    -------
    Estimated fractional price impact (e.g. 0.002 = 20 bps).
    """
    if quantity <= 0 or market_volume <= 0 or volatility <= 0 or eta <= 0:
        return 0.0
    participation_ratio = min(quantity / market_volume, 1.0)
    return eta * volatility * math.sqrt(participation_ratio)


# ---------------------------------------------------------------------------
# 3. Almgren-Chriss Optimal Execution Framework (§5.3, §8.4)
# ---------------------------------------------------------------------------


class ScheduleType(StrEnum):
    ALMGREN_CHRISS = "almgren_chriss"
    TWAP = "twap"
    VWAP = "vwap"


@dataclass(frozen=True)
class ExecutionStep:
    """A single discrete interval in an execution schedule."""

    interval_index: int
    shares_to_trade: float
    shares_remaining: float
    trading_rate: float
    expected_impact_fraction: float


@dataclass(frozen=True)
class ExecutionSchedule:
    """A full trading schedule over N discrete periods."""

    schedule_type: ScheduleType
    total_quantity: float
    num_intervals: int
    tau: float  # interval length in time units
    steps: list[ExecutionStep]
    expected_cost_usd: float
    cost_variance_usd: float
    half_life: float
    risk_aversion_lambda: float
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def utility(self) -> float:
        """Almgren-Chriss objective value: E[C] + lambda * Var(C)."""
        return self.expected_cost_usd + self.risk_aversion_lambda * self.cost_variance_usd


def generate_almgren_chriss_schedule(
    total_quantity: float,
    num_intervals: int,
    initial_price: float,
    volatility: float,
    daily_volume: float,
    risk_aversion_lambda: float = 1e-6,
    eta: float = 0.142,
    gamma: float = 0.05,
    tau: float = 1.0,
) -> ExecutionSchedule:
    """Generate optimal Almgren-Chriss liquidation / accumulation schedule (§5.3, §8.4).

    Minimises: E[C] + lambda * Var(C)

    Parameters
    ----------
    total_quantity : total shares X to trade.
    num_intervals : number of discrete trading steps N.
    initial_price : baseline asset price S_0.
    volatility : per-step volatility sigma.
    daily_volume : average daily trading volume.
    risk_aversion_lambda : risk aversion parameter lambda (higher = trade faster to avoid risk).
    eta : temporary market impact parameter.
    gamma : permanent market impact parameter.
    tau : length of each interval (tau = T / N).
    """
    if total_quantity <= 0 or num_intervals <= 0:
        raise ValueError("total_quantity and num_intervals must be positive")

    X = float(total_quantity)
    N = int(num_intervals)
    sigma = max(volatility, 1e-6)

    # Scaling eta and gamma by price and volume
    eta_effective = eta * initial_price / max(daily_volume, 1.0)
    gamma_effective = gamma * initial_price / max(daily_volume, 1.0)

    # Urgency parameter kappa:
    # 2 * sinh^2(kappa * tau / 2) = lambda * sigma^2 * tau / (2 * eta)
    # Using small-tau approximation: kappa^2 = lambda * sigma^2 / eta
    lambda_param = max(risk_aversion_lambda, 1e-12)
    kappa_sq = (lambda_param * (sigma**2)) / max(eta_effective, 1e-12)
    kappa = math.sqrt(kappa_sq)

    steps: list[ExecutionStep] = []
    x_prev = X

    # Half life of the trade
    half_life = math.log(2.0) / kappa if kappa > 1e-6 else float(N)

    # Generate holding trajectory x_j = X * sinh(kappa * (T - t_j)) / sinh(kappa * T)
    T = N * tau
    sinh_kappa_T = math.sinh(min(kappa * T, 50.0))  # prevent overflow

    for j in range(1, N + 1):
        t_j = j * tau
        if kappa * T > 1e-4 and sinh_kappa_T > 1e-6:
            x_j = X * math.sinh(max(kappa * (T - t_j), 0.0)) / sinh_kappa_T
        else:
            # Linear TWAP as lambda -> 0
            x_j = X * (1.0 - t_j / T)

        # On the last step, remaining shares must reach 0
        if j == N:
            x_j = 0.0

        n_j = max(0.0, x_prev - x_j)
        trading_rate = n_j / tau
        impact_frac = estimate_square_root_impact(n_j, daily_volume / N, sigma, eta)

        steps.append(
            ExecutionStep(
                interval_index=j,
                shares_to_trade=round(n_j, 4),
                shares_remaining=round(x_j, 4),
                trading_rate=round(trading_rate, 4),
                expected_impact_fraction=round(impact_frac, 6),
            )
        )
        x_prev = x_j

    # Expected execution cost: E[C] = 0.5 * gamma * X^2 + eta * sum(n_j^2 / tau)
    e_cost = 0.5 * gamma_effective * (X**2) + eta_effective * sum((s.shares_to_trade**2) / tau for s in steps)

    # Variance of execution cost: Var(C) = sigma^2 * sum(tau * x_j^2)
    var_cost = (sigma**2) * sum(tau * (s.shares_remaining**2) for s in steps)

    return ExecutionSchedule(
        schedule_type=ScheduleType.ALMGREN_CHRISS,
        total_quantity=X,
        num_intervals=N,
        tau=tau,
        steps=steps,
        expected_cost_usd=round(e_cost, 4),
        cost_variance_usd=round(var_cost, 4),
        half_life=round(half_life, 2),
        risk_aversion_lambda=lambda_param,
    )


def generate_twap_schedule(
    total_quantity: float,
    num_intervals: int,
    initial_price: float,
    volatility: float,
    daily_volume: float,
    eta: float = 0.142,
    gamma: float = 0.05,
    tau: float = 1.0,
) -> ExecutionSchedule:
    """Generate uniform Time-Weighted Average Price (TWAP) execution schedule."""
    if total_quantity <= 0 or num_intervals <= 0:
        raise ValueError("total_quantity and num_intervals must be positive")

    X = float(total_quantity)
    N = int(num_intervals)
    step_qty = X / N
    steps: list[ExecutionStep] = []
    x_rem = X

    eta_effective = eta * initial_price / max(daily_volume, 1.0)
    gamma_effective = gamma * initial_price / max(daily_volume, 1.0)

    for j in range(1, N + 1):
        x_rem = max(0.0, x_rem - step_qty)
        if j == N:
            x_rem = 0.0
        impact_frac = estimate_square_root_impact(step_qty, daily_volume / N, volatility, eta)
        steps.append(
            ExecutionStep(
                interval_index=j,
                shares_to_trade=round(step_qty, 4),
                shares_remaining=round(x_rem, 4),
                trading_rate=round(step_qty / tau, 4),
                expected_impact_fraction=round(impact_frac, 6),
            )
        )

    e_cost = 0.5 * gamma_effective * (X**2) + eta_effective * sum((s.shares_to_trade**2) / tau for s in steps)
    var_cost = (volatility**2) * sum(tau * (s.shares_remaining**2) for s in steps)

    return ExecutionSchedule(
        schedule_type=ScheduleType.TWAP,
        total_quantity=X,
        num_intervals=N,
        tau=tau,
        steps=steps,
        expected_cost_usd=round(e_cost, 4),
        cost_variance_usd=round(var_cost, 4),
        half_life=N / 2.0,
        risk_aversion_lambda=0.0,
    )


def generate_vwap_schedule(
    total_quantity: float,
    volume_profile: Sequence[float],
    initial_price: float,
    volatility: float,
    daily_volume: float,
    eta: float = 0.142,
    gamma: float = 0.05,
    tau: float = 1.0,
) -> ExecutionSchedule:
    """Generate Volume-Weighted Average Price (VWAP) schedule tracking volume profile."""
    if not volume_profile:
        raise ValueError("volume_profile must not be empty")
    total_prof = sum(volume_profile)
    if total_prof <= 0:
        raise ValueError("sum of volume_profile must be positive")

    X = float(total_quantity)
    N = len(volume_profile)
    steps: list[ExecutionStep] = []
    x_rem = X

    eta_effective = eta * initial_price / max(daily_volume, 1.0)
    gamma_effective = gamma * initial_price / max(daily_volume, 1.0)

    for j, v_weight in enumerate(volume_profile, start=1):
        proportion = v_weight / total_prof
        step_qty = X * proportion
        x_rem = max(0.0, x_rem - step_qty)
        if j == N:
            x_rem = 0.0
        impact_frac = estimate_square_root_impact(step_qty, daily_volume / N, volatility, eta)
        steps.append(
            ExecutionStep(
                interval_index=j,
                shares_to_trade=round(step_qty, 4),
                shares_remaining=round(x_rem, 4),
                trading_rate=round(step_qty / tau, 4),
                expected_impact_fraction=round(impact_frac, 6),
            )
        )

    e_cost = 0.5 * gamma_effective * (X**2) + eta_effective * sum((s.shares_to_trade**2) / tau for s in steps)
    var_cost = (volatility**2) * sum(tau * (s.shares_remaining**2) for s in steps)

    return ExecutionSchedule(
        schedule_type=ScheduleType.VWAP,
        total_quantity=X,
        num_intervals=N,
        tau=tau,
        steps=steps,
        expected_cost_usd=round(e_cost, 4),
        cost_variance_usd=round(var_cost, 4),
        half_life=N / 2.0,
        risk_aversion_lambda=0.0,
    )


@dataclass(frozen=True)
class ScheduleComparisonReport:
    """Evaluation and comparison report across execution scheduling policies (§8.5)."""

    symbol: str
    total_quantity: float
    initial_price: float
    schedules: dict[ScheduleType, ExecutionSchedule]
    recommended_policy: ScheduleType
    rationale: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def compare_execution_policies(
    symbol: str,
    total_quantity: float,
    num_intervals: int,
    initial_price: float,
    volatility: float,
    daily_volume: float,
    volume_profile: Sequence[float] | None = None,
    risk_aversion_lambda: float = 1e-5,
) -> ScheduleComparisonReport:
    """Compare Almgren-Chriss, TWAP, and VWAP execution schedules (§8.5)."""
    prof = volume_profile or [1.0] * num_intervals

    ac = generate_almgren_chriss_schedule(
        total_quantity=total_quantity,
        num_intervals=num_intervals,
        initial_price=initial_price,
        volatility=volatility,
        daily_volume=daily_volume,
        risk_aversion_lambda=risk_aversion_lambda,
    )
    twap = generate_twap_schedule(
        total_quantity=total_quantity,
        num_intervals=num_intervals,
        initial_price=initial_price,
        volatility=volatility,
        daily_volume=daily_volume,
    )
    vwap = generate_vwap_schedule(
        total_quantity=total_quantity,
        volume_profile=prof,
        initial_price=initial_price,
        volatility=volatility,
        daily_volume=daily_volume,
    )

    schedules = {
        ScheduleType.ALMGREN_CHRISS: ac,
        ScheduleType.TWAP: twap,
        ScheduleType.VWAP: vwap,
    }

    # Best policy minimizing Almgren-Chriss total utility: E[C] + lambda * Var(C)
    best_policy = min(schedules.keys(), key=lambda k: schedules[k].utility)

    rationale = (
        f"Selected {best_policy.value} with utility {schedules[best_policy].utility:.4f} "
        f"(expected cost ${schedules[best_policy].expected_cost_usd:.2f}, "
        f"variance ${schedules[best_policy].cost_variance_usd:.2f} at lambda={risk_aversion_lambda})"
    )

    return ScheduleComparisonReport(
        symbol=symbol,
        total_quantity=total_quantity,
        initial_price=initial_price,
        schedules=schedules,
        recommended_policy=best_policy,
        rationale=rationale,
    )
