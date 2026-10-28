"""
P11-COST: transaction cost, market impact, funding, and turnover estimates.

Costs are positive numbers. Every estimate carries its model version, input
timestamp, coverage status, and limitations. Missing ADV or volatility uses an
explicit conservative fallback and degrades coverage; it is never silently
treated as zero cost or infinite liquidity.

The impact model is the specification's candidate power law:

    I(Q) = a * sigma * (|Q| / V) ** beta

Parameters must be calibrated from execution data before production use. The
defaults are conservative planning assumptions, not calibrated estimates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from axel.core.types import OrderSide
from axel.portfolio.state import worst_quality
from axel.portfolio.types import (
    AssetClass,
    CostEstimate,
    CostSummary,
    DataQuality,
    require_aware,
    require_finite,
    require_non_negative,
    require_positive,
    utc_now,
)


class CostModelError(ValueError):
    """Raised when a cost estimate cannot be produced from valid inputs."""


@dataclass(frozen=True)
class CostModelConfig:
    version: str = "p11-cost-1"
    default_fee_bps: float = 1.0
    default_spread_bps: float = 5.0
    default_slippage_bps: float = 2.0
    default_volatility: float = 0.02
    impact_coefficient: float = 0.1
    impact_exponent: float = 0.5
    default_funding_rate_annual: float = 0.0
    default_borrow_rate_annual: float = 0.0
    conversion_bps: float = 2.0
    settlement_bps: float = 0.0
    stress_multiplier: float = 3.0
    max_participation_rate: float = 0.10

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("version must be non-empty")
        for name in (
            "default_fee_bps",
            "default_spread_bps",
            "default_slippage_bps",
            "default_volatility",
            "impact_coefficient",
            "default_funding_rate_annual",
            "default_borrow_rate_annual",
            "conversion_bps",
            "settlement_bps",
        ):
            require_non_negative(getattr(self, name), name)
        if not 0.0 < self.impact_exponent <= 1.0:
            raise ValueError("impact_exponent must be in (0, 1]")
        if self.stress_multiplier < 1.0:
            raise ValueError("stress_multiplier must be >= 1")
        if not 0.0 < self.max_participation_rate <= 1.0:
            raise ValueError("max_participation_rate must be in (0, 1]")


DEFAULT_COST_MODEL = CostModelConfig()


@dataclass(frozen=True)
class CostInput:
    """One proposed trade. ``quantity`` is a positive traded quantity."""

    instrument_id: str
    venue: str
    asset_class: AssetClass
    side: OrderSide
    quantity: float
    price: float
    multiplier: float
    currency: str
    base_currency: str
    as_of: datetime
    fx_rate: float = 1.0
    adv: float | None = None
    volatility: float | None = None
    spread_bps: float | None = None
    fee_bps: float | None = None
    funding_rate_annual: float | None = None
    borrow_rate_annual: float | None = None
    holding_days: float = 0.0
    is_derivative: bool = False

    def __post_init__(self) -> None:
        for name in ("instrument_id", "venue", "currency", "base_currency"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        if not isinstance(self.side, OrderSide):
            object.__setattr__(self, "side", OrderSide(self.side))
        require_positive(self.quantity, "quantity")
        require_positive(self.price, "price")
        require_positive(self.multiplier, "multiplier")
        require_positive(self.fx_rate, "fx_rate")
        require_non_negative(self.holding_days, "holding_days")
        object.__setattr__(self, "as_of", require_aware(self.as_of, "as_of"))
        for name in (
            "adv",
            "volatility",
            "spread_bps",
            "fee_bps",
            "funding_rate_annual",
            "borrow_rate_annual",
        ):
            value = getattr(self, name)
            if value is not None:
                require_finite(value, name)
                if value < 0:
                    raise ValueError(f"{name} must be non-negative")

    @property
    def notional_base(self) -> float:
        return abs(self.quantity) * self.multiplier * self.price * self.fx_rate

    @property
    def units(self) -> float:
        return abs(self.quantity) * self.multiplier


def estimate_trade_cost(
    trade: CostInput,
    config: CostModelConfig = DEFAULT_COST_MODEL,
    *,
    now: datetime | None = None,
) -> CostEstimate:
    """Estimate all applicable cost components for one trade."""
    current = require_aware(now or utc_now(), "now")
    notional = trade.notional_base
    warnings: list[str] = []
    coverage = DataQuality.VALID

    spread_bps = trade.spread_bps if trade.spread_bps is not None else config.default_spread_bps
    if trade.spread_bps is None:
        warnings.append("spread_bps missing; default spread used")
        coverage = worst_quality(coverage, DataQuality.DEGRADED)
    fee_bps = trade.fee_bps if trade.fee_bps is not None else config.default_fee_bps
    if trade.fee_bps is None:
        warnings.append("fee_bps missing; default fee used")
        coverage = worst_quality(coverage, DataQuality.DEGRADED)

    spread_cost = notional * (spread_bps / 10_000.0) * 0.5
    fee_cost = notional * (fee_bps / 10_000.0)
    slippage_cost = notional * (config.default_slippage_bps / 10_000.0)

    if trade.adv is not None and trade.adv > 0:
        participation = trade.units / trade.adv
    else:
        participation = config.max_participation_rate
        warnings.append("ADV missing or zero; conservative maximum participation assumed")
        coverage = worst_quality(coverage, DataQuality.DEGRADED)
    if participation > config.max_participation_rate:
        warnings.append(
            f"participation {participation:.4f} exceeds validated capacity "
            f"{config.max_participation_rate:.4f}"
        )
        coverage = worst_quality(coverage, DataQuality.DEGRADED)

    volatility = trade.volatility
    if volatility is None:
        volatility = config.default_volatility
        warnings.append("volatility missing; default volatility used for impact")
        coverage = worst_quality(coverage, DataQuality.DEGRADED)
    impact_cost = (
        config.impact_coefficient
        * volatility
        * (max(participation, 0.0) ** config.impact_exponent)
        * notional
    )

    funding_rate = (
        trade.funding_rate_annual
        if trade.funding_rate_annual is not None
        else config.default_funding_rate_annual
    )
    borrow_rate = (
        trade.borrow_rate_annual
        if trade.borrow_rate_annual is not None
        else config.default_borrow_rate_annual
    )
    funding_cost = notional * funding_rate * (trade.holding_days / 365.0)
    borrow_cost = notional * borrow_rate * (trade.holding_days / 365.0)
    conversion_cost = (
        notional * (config.conversion_bps / 10_000.0)
        if trade.currency != trade.base_currency
        else 0.0
    )
    settlement_cost = notional * (config.settlement_bps / 10_000.0)

    total = (
        spread_cost
        + fee_cost
        + slippage_cost
        + impact_cost
        + funding_cost
        + borrow_cost
        + conversion_cost
        + settlement_cost
    )
    limitations = (
        "Impact is a power-law approximation; parameters require execution-data calibration.",
        "Funding and borrow use simple annualized rates, not venue-specific schedules.",
        "Costs are estimates, not execution guarantees.",
    )
    return CostEstimate(
        instrument_id=trade.instrument_id,
        venue=trade.venue,
        asset_class=trade.asset_class,
        expected_notional=notional,
        spread_cost=spread_cost,
        fee_cost=fee_cost,
        slippage_cost=slippage_cost,
        impact_cost=impact_cost,
        funding_cost=funding_cost,
        borrow_cost=borrow_cost,
        conversion_cost=conversion_cost,
        settlement_cost=settlement_cost,
        total_cost=total,
        stress_cost=total * config.stress_multiplier,
        participation_rate=participation,
        model_version=config.version,
        as_of=current,
        coverage=coverage,
        warnings=tuple(dict.fromkeys(warnings)),
        limitations=limitations,
    )


def estimate_rebalance_cost(
    trades: Sequence[CostInput],
    config: CostModelConfig = DEFAULT_COST_MODEL,
    *,
    now: datetime | None = None,
) -> CostSummary:
    """Aggregate trade costs into a proposal-level summary."""
    if not trades:
        return CostSummary(
            total_cost=0.0,
            stress_cost=0.0,
            by_component={},
            by_instrument={},
            model_version=config.version,
            coverage=DataQuality.VALID,
            warnings=("no trades supplied",),
        )
    estimates = [estimate_trade_cost(trade, config, now=now) for trade in trades]
    by_component: dict[str, float] = {
        "spread": 0.0,
        "fees": 0.0,
        "slippage": 0.0,
        "impact": 0.0,
        "funding": 0.0,
        "borrow": 0.0,
        "conversion": 0.0,
        "settlement": 0.0,
    }
    by_instrument: dict[str, float] = {}
    warnings: list[str] = []
    limitations: list[str] = []
    coverage = DataQuality.VALID
    for estimate in estimates:
        by_component["spread"] += estimate.spread_cost
        by_component["fees"] += estimate.fee_cost
        by_component["slippage"] += estimate.slippage_cost
        by_component["impact"] += estimate.impact_cost
        by_component["funding"] += estimate.funding_cost
        by_component["borrow"] += estimate.borrow_cost
        by_component["conversion"] += estimate.conversion_cost
        by_component["settlement"] += estimate.settlement_cost
        by_instrument[estimate.instrument_id] = (
            by_instrument.get(estimate.instrument_id, 0.0) + estimate.total_cost
        )
        warnings.extend(estimate.warnings)
        limitations.extend(estimate.limitations)
        coverage = worst_quality(coverage, estimate.coverage)
    return CostSummary(
        total_cost=sum(estimate.total_cost for estimate in estimates),
        stress_cost=sum(estimate.stress_cost for estimate in estimates),
        by_component=by_component,
        by_instrument=by_instrument,
        model_version=config.version,
        coverage=coverage,
        warnings=tuple(dict.fromkeys(warnings)),
        limitations=tuple(dict.fromkeys(limitations)),
    )


def turnover(current_weights: Sequence[float], proposed_weights: Sequence[float]) -> float:
    """One-way turnover ``sum |w_i - w0_i|`` under the stated convention."""
    if len(current_weights) != len(proposed_weights):
        raise CostModelError("weight vectors must have equal length")
    for value in list(current_weights) + list(proposed_weights):
        require_finite(value, "weight")
    return sum(
        abs(proposed - current)
        for current, proposed in zip(current_weights, proposed_weights, strict=True)
    )


def cost_as_return(cost: float, portfolio_value: float) -> float:
    require_non_negative(cost, "cost")
    require_positive(portfolio_value, "portfolio_value")
    return cost / portfolio_value


def net_expected_return(
    gross_return: float, cost: float, portfolio_value: float
) -> float:
    require_finite(gross_return, "gross_return")
    return gross_return - cost_as_return(cost, portfolio_value)


def component_shares(summary: CostSummary) -> Mapping[str, float]:
    total = summary.total_cost
    if total <= 0:
        return {component: 0.0 for component in summary.by_component}
    return {
        component: value / total for component, value in summary.by_component.items()
    }


def cost_is_material(summary: CostSummary, portfolio_value: float, threshold_bps: float = 25.0) -> bool:
    if portfolio_value <= 0:
        return True
    return summary.total_cost / portfolio_value * 10_000.0 >= threshold_bps


__all__ = [
    "DEFAULT_COST_MODEL",
    "CostInput",
    "CostModelConfig",
    "CostModelError",
    "component_shares",
    "cost_as_return",
    "cost_is_material",
    "estimate_rebalance_cost",
    "estimate_trade_cost",
    "net_expected_return",
    "turnover",
]
