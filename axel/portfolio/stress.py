"""
P11-STRESS: versioned scenario stress testing.

Scenarios are hypothetical unless an explicit, evidenced probability is
supplied. A hypothetical scenario is never assigned an invented probability.

Linear instruments use first-order scenario sensitivities. Options use a full
Black-Scholes revaluation when contract inputs and a matching position are
available; otherwise they fall back to local Greeks and record the limitation.
Large option shocks that require full revaluation are not silently treated as
precise.

The stress suite is a gate, not a return forecast. A proposal cannot bypass a
failed stress scenario because its expected return is high.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite

from axel.core.types import Section
from axel.options.pricing import PricingInputs, black_scholes_price
from axel.options.types import ExerciseStyle, OptionRight
from axel.portfolio.serialization import fingerprint
from axel.portfolio.types import (
    AssetClass,
    ConstraintResult,
    ConstraintSeverity,
    ExposureSnapshot,
    NormalizedExposure,
    PortfolioSnapshot,
    PositionRecord,
    RiskBudgetPolicy,
    as_section,
    require_aware,
    require_finite,
    utc_now,
)

STRESS_MODEL_VERSION = "p11-stress-1"


class ScenarioFamily(str, Enum):
    EQUITY_SELLOFF = "equity_selloff"
    SECTOR = "sector"
    RATES = "rates"
    FX = "fx"
    CRYPTO = "crypto"
    VOLATILITY = "volatility"
    LIQUIDITY = "liquidity"
    SPREADS = "spreads"
    FUNDING_MARGIN = "funding_margin"
    OUTAGE = "outage"
    COUNTERPARTY = "counterparty"
    PREDICTION = "prediction"
    SETTLEMENT = "settlement"
    CORRELATION = "correlation"
    COMBINED = "combined"
    HYPOTHETICAL = "hypothetical"


@dataclass(frozen=True)
class StressScenario:
    """A versioned set of factor shocks and model assumptions."""

    scenario_id: str
    version: str
    name: str
    family: ScenarioFamily
    description: str
    section_returns: Mapping[str, float] = field(default_factory=dict)
    factor_returns: Mapping[str, float] = field(default_factory=dict)
    instrument_returns: Mapping[str, float] = field(default_factory=dict)
    currency_returns: Mapping[str, float] = field(default_factory=dict)
    volatility_shock: float = 0.0
    rate_shock: float = 0.0
    time_decay_years: float = 0.0
    spread_multiplier: float = 1.0
    liquidity_multiplier: float = 1.0
    liquidity_cost_bps: float = 0.0
    funding_rate_shock: float = 0.0
    funding_horizon_days: float = 1.0
    margin_multiplier: float = 1.0
    prediction_loss_fraction: float = 0.0
    correlation_increase: float = 0.0
    probability: float | None = None
    hypothetical: bool = True
    requires_full_revaluation: bool = True
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("scenario_id", "version", "name", "description"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        if not isinstance(self.family, ScenarioFamily):
            object.__setattr__(self, "family", ScenarioFamily(self.family))
        for section in self.section_returns:
            as_section(section)
        for mapping_name in (
            "section_returns",
            "factor_returns",
            "instrument_returns",
            "currency_returns",
        ):
            for key, value in getattr(self, mapping_name).items():
                if not key:
                    raise ValueError(f"{mapping_name} keys must be non-empty")
                require_finite(value, f"{mapping_name}.{key}")
                if mapping_name in {"section_returns", "instrument_returns"} and value <= -1.0:
                    raise ValueError(f"{mapping_name}.{key} cannot be <= -100%")
        for name in (
            "volatility_shock",
            "rate_shock",
            "time_decay_years",
            "spread_multiplier",
            "liquidity_multiplier",
            "liquidity_cost_bps",
            "funding_rate_shock",
            "funding_horizon_days",
            "margin_multiplier",
            "prediction_loss_fraction",
            "correlation_increase",
        ):
            require_finite(getattr(self, name), name)
        if self.time_decay_years < 0:
            raise ValueError("time_decay_years must be non-negative")
        if self.spread_multiplier < 1.0 or self.liquidity_multiplier < 1.0:
            raise ValueError("spread and liquidity multipliers must be >= 1")
        if self.margin_multiplier < 1.0:
            raise ValueError("margin_multiplier must be >= 1")
        if not 0.0 <= self.prediction_loss_fraction <= 1.0:
            raise ValueError("prediction_loss_fraction must be in [0, 1]")
        if self.probability is not None:
            if not 0.0 <= self.probability <= 1.0:
                raise ValueError("probability must be in [0, 1]")
        elif not self.hypothetical:
            object.__setattr__(self, "hypothetical", True)
        if self.hypothetical and self.probability is not None:
            # A hypothetical scenario may carry a probability for weighting,
            # but it remains labelled hypothetical.
            pass


@dataclass(frozen=True)
class StressResult:
    scenario_id: str
    scenario_version: str
    name: str
    family: ScenarioFamily
    hypothetical: bool
    base_value: float
    stressed_value: float
    market_pnl: float
    funding_cost: float
    liquidity_cost: float
    total_pnl: float
    total_loss: float
    section_pnl: Mapping[str, float]
    section_loss: Mapping[str, float]
    margin_requirement: float | None
    breaches: tuple[ConstraintResult, ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    full_revaluation_count: int = 0
    greeks_count: int = 0

    @property
    def passed(self) -> bool:
        return not any(check.hard and not check.passed for check in self.breaches)


@dataclass(frozen=True)
class StressReport:
    report_id: str
    portfolio_snapshot_id: str
    exposure_snapshot_id: str
    model_version: str
    results: tuple[StressResult, ...]
    worst_case_loss: float
    worst_scenario_id: str | None
    passed: bool
    created_at: datetime
    limitations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def result_for(self, scenario_id: str) -> StressResult | None:
        return next(
            (result for result in self.results if result.scenario_id == scenario_id),
            None,
        )


def _check(
    name: str,
    passed: bool,
    actual: float | None,
    limit: float | None,
    unit: str,
    message: str,
) -> ConstraintResult:
    return ConstraintResult(
        name=name,
        severity=ConstraintSeverity.HARD,
        passed=passed,
        actual=actual,
        limit=limit,
        unit=unit,
        message=message,
    )


def _instrument_return(
    scenario: StressScenario,
    instrument_id: str,
    section: Section,
) -> float | None:
    if instrument_id in scenario.instrument_returns:
        return scenario.instrument_returns[instrument_id]
    return scenario.section_returns.get(section.value)


def _underlying_return(
    scenario: StressScenario,
    underlying_id: str,
    section: Section,
) -> float:
    if underlying_id in scenario.instrument_returns:
        return scenario.instrument_returns[underlying_id]
    if underlying_id in scenario.section_returns:
        return scenario.section_returns[underlying_id]
    return scenario.section_returns.get(section.value, 0.0)


def _option_intrinsic(spot: float, strike: float, right: OptionRight) -> float:
    return max(spot - strike, 0.0) if right == OptionRight.CALL else max(strike - spot, 0.0)


def _option_model_price(
    *,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    volatility: float,
    right: OptionRight,
    exercise_style: ExerciseStyle,
) -> tuple[float, tuple[str, ...]]:
    limitations: list[str] = []
    if spot <= 0:
        return 0.0, ("non-positive stressed underlying price",)
    if time_to_expiry <= 0:
        return _option_intrinsic(spot, strike, right), ()
    if volatility <= 0:
        return _option_intrinsic(spot, strike, right), ()
    if exercise_style == ExerciseStyle.AMERICAN:
        limitations.append(
            "American option revalued with a European Black-Scholes approximation"
        )
    price, _ = black_scholes_price(
        PricingInputs(
            spot=spot,
            strike=strike,
            time_to_expiry_years=max(1e-8, time_to_expiry),
            risk_free_rate=max(0.0, rate),
            volatility=max(1e-8, volatility),
            option_right=right,
            exercise_style=exercise_style,
        )
    )
    return price, tuple(limitations)


def _option_pnl(
    position: PositionRecord,
    exposure: NormalizedExposure,
    scenario: StressScenario,
) -> tuple[float, bool, tuple[str, ...]]:
    option = position.option
    if option is None:
        return 0.0, False, ("option position is missing contract inputs",)
    underlying_return = _underlying_return(scenario, option.underlying_id, exposure.section)
    volatility = max(1e-8, option.volatility + scenario.volatility_shock)
    rate = option.risk_free_rate + scenario.rate_shock
    try:
        right = OptionRight(option.option_right)
        style = ExerciseStyle(option.exercise_style)
    except ValueError:
        return 0.0, False, ("option right or exercise style is invalid",)

    fx = 1.0
    if position.market_value != 0.0 and exposure.market_value != 0.0:
        fx = exposure.market_value / position.market_value
    if not isfinite(fx) or fx <= 0:
        fx = 1.0

    base_price, base_limitations = _option_model_price(
        spot=option.underlying_price,
        strike=option.strike,
        time_to_expiry=option.time_to_expiry_years,
        rate=option.risk_free_rate,
        volatility=option.volatility,
        right=right,
        exercise_style=style,
    )
    stressed_price, stressed_limitations = _option_model_price(
        spot=option.underlying_price * (1.0 + underlying_return),
        strike=option.strike,
        time_to_expiry=option.time_to_expiry_years - scenario.time_decay_years,
        rate=rate,
        volatility=volatility,
        right=right,
        exercise_style=style,
    )
    pnl = (
        (stressed_price - base_price)
        * position.quantity
        * position.multiplier
        * fx
    )
    limitations = tuple(dict.fromkeys(base_limitations + stressed_limitations))
    if abs(underlying_return) >= 0.20:
        limitations = limitations + (
            "large underlying shock uses model revaluation; verify with venue-specific repricing",
        )
    return pnl, True, limitations


def _option_greeks_pnl(
    exposure: NormalizedExposure,
    scenario: StressScenario,
) -> tuple[float, tuple[str, ...]]:
    underlying_return = _underlying_return(
        scenario, exposure.instrument_id, exposure.section
    )
    underlying_price = exposure.underlying_price
    if underlying_price is None:
        return 0.0, ("option exposure is missing underlying price",)
    delta_s = underlying_price * underlying_return
    pnl = (
        (exposure.delta or 0.0) * delta_s
        + 0.5 * (exposure.gamma or 0.0) * delta_s * delta_s
        + (exposure.vega or 0.0) * scenario.volatility_shock
        + (exposure.theta or 0.0) * scenario.time_decay_years
    )
    return pnl, (
        "option stress used local Greeks; full revaluation inputs were unavailable",
    )


def _position_for(
    positions: Mapping[str, PositionRecord],
    instrument_id: str,
) -> PositionRecord | None:
    return positions.get(instrument_id)


def apply_scenario(
    exposure: ExposureSnapshot,
    scenario: StressScenario,
    *,
    snapshot: PortfolioSnapshot | None = None,
    policy: RiskBudgetPolicy | None = None,
    now: datetime | None = None,
) -> StressResult:
    """Apply one scenario to instrument exposures and return a typed result."""
    if not isinstance(scenario, StressScenario):
        raise TypeError("scenario must be a StressScenario")
    positions: Mapping[str, PositionRecord] = {}
    if snapshot is not None:
        positions = {position.instrument_id: position for position in snapshot.positions}

    section_pnl: dict[str, float] = {}
    section_gross: dict[str, float] = {}
    funding_cost = 0.0
    liquidity_cost = 0.0
    margin_total = 0.0
    margin_known = True
    option_positions = 0
    full_revaluation_count = 0
    greeks_count = 0
    limitations: list[str] = list(scenario.limitations)
    assumptions: list[str] = [
        "Linear instruments use first-order scenario returns.",
        "Option P&L uses full Black-Scholes revaluation when contract inputs are available.",
        "Funding and liquidity costs are added after market P&L.",
    ]
    prediction_unknown_max_loss = False
    margin_unknown_for_margined_asset = False

    for item in exposure.instrument_exposures:
        section = item.section.value
        gross = item.gross_market_value
        section_gross[section] = section_gross.get(section, 0.0) + gross
        pnl = 0.0
        if item.asset_class == AssetClass.OPTION:
            option_positions += 1
            position = _position_for(positions, item.instrument_id)
            if position is not None and position.option is not None:
                pnl, used_full, option_limitations = _option_pnl(position, item, scenario)
                if used_full:
                    full_revaluation_count += 1
                else:
                    greeks_count += 1
                limitations.extend(option_limitations)
            else:
                pnl, option_limitations = _option_greeks_pnl(item, scenario)
                greeks_count += 1
                limitations.extend(option_limitations)
        elif item.asset_class == AssetClass.PREDICTION:
            if item.max_loss is None:
                prediction_unknown_max_loss = True
                limitations.append(
                    f"prediction {item.instrument_id} has no defined maximum loss"
                )
            else:
                pnl = -item.max_loss * scenario.prediction_loss_fraction
        elif item.asset_class == AssetClass.FOREX:
            if scenario.currency_returns:
                pnl = sum(
                    value * scenario.currency_returns.get(currency, 0.0)
                    for currency, value in item.currency_exposures.items()
                )
            else:
                instrument_return = _instrument_return(
                    scenario, item.instrument_id, item.section
                )
                pnl = item.market_value * (instrument_return or 0.0)
        else:
            instrument_return = _instrument_return(
                scenario, item.instrument_id, item.section
            )
            if instrument_return is not None:
                pnl = item.market_value * instrument_return
            elif scenario.factor_returns and item.factor_exposures:
                pnl = sum(
                    item.factor_exposures.get(factor_id, 0.0) * factor_return
                    for factor_id, factor_return in scenario.factor_returns.items()
                )
            else:
                pnl = 0.0

        section_pnl[section] = section_pnl.get(section, 0.0) + pnl
        if item.asset_class in {AssetClass.CRYPTO, AssetClass.FOREX}:
            funding_cost += (
                gross
                * max(0.0, scenario.funding_rate_shock)
                * scenario.funding_horizon_days
                / 365.0
            )
        if item.asset_class in {AssetClass.OPTION, AssetClass.FOREX}:
            if item.margin_requirement is None:
                margin_known = False
                margin_unknown_for_margined_asset = True
            else:
                margin_total += item.margin_requirement

    total_gross = sum(section_gross.values())
    liquidity_cost = total_gross * (scenario.liquidity_cost_bps / 10_000.0)
    liquidity_cost *= scenario.liquidity_multiplier
    if total_gross > 0:
        for section, gross in section_gross.items():
            share = gross / total_gross
            section_pnl[section] = (
                section_pnl.get(section, 0.0)
                - funding_cost * share
                - liquidity_cost * share
            )

    market_pnl = sum(section_pnl.values()) + funding_cost + liquidity_cost
    total_pnl = market_pnl - funding_cost - liquidity_cost
    total_loss = max(0.0, -total_pnl)
    base_value = sum(item.market_value for item in exposure.instrument_exposures)
    stressed_value = base_value + total_pnl
    margin_requirement = margin_total * scenario.margin_multiplier if margin_known else None

    breaches: list[ConstraintResult] = []
    if policy is not None and policy.global_max_stress_loss is not None:
        breaches.append(
            _check(
                "global_max_stress_loss",
                total_loss <= policy.global_max_stress_loss + 1e-6,
                total_loss,
                policy.global_max_stress_loss,
                "currency",
                f"stress scenario {scenario.scenario_id} exceeds the approved loss limit",
            )
        )
    if prediction_unknown_max_loss and scenario.prediction_loss_fraction > 0:
        breaches.append(
            _check(
                "prediction_max_loss_defined",
                False,
                None,
                None,
                "boolean",
                "prediction maximum loss is undefined under an event-loss scenario",
            )
        )
    if (
        scenario.requires_full_revaluation
        and option_positions > 0
        and full_revaluation_count < option_positions
    ):
        breaches.append(
            _check(
                "option_full_revaluation_available",
                False,
                float(full_revaluation_count),
                float(option_positions),
                "count",
                "not every option could be fully revalued for this scenario",
            )
        )
    if scenario.margin_multiplier > 1.0 and margin_unknown_for_margined_asset:
        breaches.append(
            _check(
                "stress_margin_defined",
                False,
                None,
                None,
                "boolean",
                "margin is unknown for a margined asset under a margin-increase scenario",
            )
        )
    if scenario.correlation_increase > 0:
        limitations.append(
            "correlation increase is represented through joint shocks; the covariance "
            "matrix is not re-estimated in this scenario"
        )

    section_loss = {
        section: max(0.0, -value) for section, value in section_pnl.items()
    }
    return StressResult(
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        name=scenario.name,
        family=scenario.family,
        hypothetical=scenario.hypothetical,
        base_value=base_value,
        stressed_value=stressed_value,
        market_pnl=market_pnl,
        funding_cost=funding_cost,
        liquidity_cost=liquidity_cost,
        total_pnl=total_pnl,
        total_loss=total_loss,
        section_pnl=section_pnl,
        section_loss=section_loss,
        margin_requirement=margin_requirement,
        breaches=tuple(breaches),
        assumptions=tuple(dict.fromkeys(assumptions)),
        limitations=tuple(dict.fromkeys(limitations)),
        full_revaluation_count=full_revaluation_count,
        greeks_count=greeks_count,
    )


def default_scenarios() -> tuple[StressScenario, ...]:
    """Versioned default suite. All defaults are explicitly hypothetical."""
    common = {
        "version": "1.0",
        "hypothetical": True,
        "requires_full_revaluation": True,
    }
    return (
        StressScenario(
            scenario_id="eq_selloff_20",
            name="Broad equity sell-off",
            family=ScenarioFamily.EQUITY_SELLOFF,
            description="Equities fall 20%, crypto falls 15%, volatility rises.",
            section_returns={
                Section.STOCKS.value: -0.20,
                Section.OPTIONS.value: -0.20,
                Section.CRYPTO.value: -0.15,
            },
            volatility_shock=0.10,
            liquidity_cost_bps=25.0,
            spread_multiplier=2.0,
            margin_multiplier=1.5,
            **common,
        ),
        StressScenario(
            scenario_id="sector_shock_15",
            name="Sector shock",
            family=ScenarioFamily.SECTOR,
            description="A concentrated sector falls 15% while broad equities fall 5%.",
            section_returns={Section.STOCKS.value: -0.05, Section.OPTIONS.value: -0.05},
            factor_returns={"sector": -0.15},
            volatility_shock=0.05,
            liquidity_cost_bps=15.0,
            **common,
        ),
        StressScenario(
            scenario_id="rates_up_100bp",
            name="Rates +100bp",
            family=ScenarioFamily.RATES,
            description="Rates rise 100bp; equities and crypto decline modestly.",
            section_returns={
                Section.STOCKS.value: -0.05,
                Section.CRYPTO.value: -0.05,
                Section.OPTIONS.value: -0.05,
            },
            rate_shock=0.01,
            **common,
        ),
        StressScenario(
            scenario_id="fx_shock",
            name="Currency shock",
            family=ScenarioFamily.FX,
            description="EUR and GBP weaken 5%, JPY strengthens 3% against reporting currency.",
            section_returns={Section.FOREX.value: -0.05},
            currency_returns={"EUR": -0.05, "GBP": -0.04, "JPY": 0.03, "USD": 0.0},
            liquidity_cost_bps=10.0,
            **common,
        ),
        StressScenario(
            scenario_id="crypto_crash_35",
            name="Crypto crash",
            family=ScenarioFamily.CRYPTO,
            description="Crypto falls 35% with higher funding and wider spreads.",
            section_returns={Section.CRYPTO.value: -0.35, Section.STOCKS.value: -0.05},
            funding_rate_shock=0.10,
            funding_horizon_days=7.0,
            liquidity_cost_bps=100.0,
            spread_multiplier=3.0,
            margin_multiplier=1.5,
            **common,
        ),
        StressScenario(
            scenario_id="vol_spike_20",
            name="Volatility spike",
            family=ScenarioFamily.VOLATILITY,
            description="Implied volatility rises 20 points with a modest equity decline.",
            section_returns={Section.STOCKS.value: -0.05, Section.OPTIONS.value: -0.05},
            volatility_shock=0.20,
            liquidity_cost_bps=20.0,
            **common,
        ),
        StressScenario(
            scenario_id="liquidity_collapse",
            name="Liquidity collapse",
            family=ScenarioFamily.LIQUIDITY,
            description="Spreads widen and impact rises sharply across sections.",
            section_returns={
                Section.STOCKS.value: -0.05,
                Section.CRYPTO.value: -0.10,
                Section.OPTIONS.value: -0.05,
            },
            liquidity_cost_bps=150.0,
            liquidity_multiplier=2.0,
            spread_multiplier=4.0,
            **common,
        ),
        StressScenario(
            scenario_id="funding_margin_shock",
            name="Funding and margin increase",
            family=ScenarioFamily.FUNDING_MARGIN,
            description="Funding rises 15 points and margin requirements double.",
            section_returns={Section.CRYPTO.value: -0.10, Section.FOREX.value: -0.02},
            funding_rate_shock=0.15,
            funding_horizon_days=7.0,
            margin_multiplier=2.0,
            liquidity_cost_bps=30.0,
            **common,
        ),
        StressScenario(
            scenario_id="prediction_event_loss",
            name="Prediction event loss",
            family=ScenarioFamily.PREDICTION,
            description="Prediction contracts settle at their maximum loss.",
            prediction_loss_fraction=1.0,
            liquidity_cost_bps=25.0,
            **common,
        ),
        StressScenario(
            scenario_id="settlement_delay",
            name="Settlement delay",
            family=ScenarioFamily.SETTLEMENT,
            description="Settlement is delayed and liquidity costs rise without a market move.",
            prediction_loss_fraction=0.0,
            liquidity_cost_bps=50.0,
            liquidity_multiplier=2.0,
            **common,
        ),
        StressScenario(
            scenario_id="combined_selloff_vol",
            name="Sell-off plus volatility spike",
            family=ScenarioFamily.COMBINED,
            description="Equities fall 20%, crypto falls 15%, volatility rises 15 points.",
            section_returns={
                Section.STOCKS.value: -0.20,
                Section.CRYPTO.value: -0.15,
                Section.OPTIONS.value: -0.20,
            },
            volatility_shock=0.15,
            liquidity_cost_bps=50.0,
            spread_multiplier=3.0,
            margin_multiplier=1.5,
            **common,
        ),
        StressScenario(
            scenario_id="combined_crypto_outage",
            name="Crypto decline plus venue outage",
            family=ScenarioFamily.COMBINED,
            description="Crypto falls 35%, funding rises, and liquidity deteriorates.",
            section_returns={Section.CRYPTO.value: -0.35},
            funding_rate_shock=0.10,
            funding_horizon_days=7.0,
            liquidity_cost_bps=200.0,
            liquidity_multiplier=2.0,
            spread_multiplier=5.0,
            margin_multiplier=2.0,
            limitations=("venue outage is represented by liquidity and funding costs",),
            **common,
        ),
        StressScenario(
            scenario_id="combined_fx_funding",
            name="Currency shock plus financing increase",
            family=ScenarioFamily.COMBINED,
            description="EUR and GBP weaken while funding rises.",
            section_returns={Section.FOREX.value: -0.08},
            currency_returns={"EUR": -0.08, "GBP": -0.06, "JPY": 0.04, "USD": 0.0},
            funding_rate_shock=0.10,
            funding_horizon_days=7.0,
            margin_multiplier=1.5,
            **common,
        ),
        StressScenario(
            scenario_id="combined_event_platform",
            name="Event loss plus withdrawal restriction",
            family=ScenarioFamily.COMBINED,
            description="Prediction contracts lose maximum value and liquidity costs rise.",
            prediction_loss_fraction=1.0,
            liquidity_cost_bps=100.0,
            liquidity_multiplier=2.0,
            limitations=("platform withdrawal restriction is represented by liquidity cost",),
            **common,
        ),
        StressScenario(
            scenario_id="correlation_increase",
            name="Correlation increase",
            family=ScenarioFamily.CORRELATION,
            description="Joint declines with a correlation increase assumption.",
            section_returns={Section.STOCKS.value: -0.05, Section.CRYPTO.value: -0.10},
            correlation_increase=0.30,
            liquidity_cost_bps=25.0,
            **common,
        ),
    )


DEFAULT_STRESS_SCENARIOS = default_scenarios()


def run_stress_suite(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    *,
    scenarios: Sequence[StressScenario] | None = None,
    policy: RiskBudgetPolicy | None = None,
    now: datetime | None = None,
) -> StressReport:
    """Run the approved suite and fail closed if it is empty or incomplete."""
    current = require_aware(now or utc_now(), "now")
    if exposure.portfolio_snapshot_id != snapshot.snapshot_id:
        raise ValueError("exposure snapshot does not match portfolio snapshot")
    selected = tuple(scenarios) if scenarios is not None else DEFAULT_STRESS_SCENARIOS
    if not selected:
        return StressReport(
            report_id=fingerprint("stress", snapshot.snapshot_id, exposure.exposure_snapshot_id, "empty"),
            portfolio_snapshot_id=snapshot.snapshot_id,
            exposure_snapshot_id=exposure.exposure_snapshot_id,
            model_version=STRESS_MODEL_VERSION,
            results=(),
            worst_case_loss=0.0,
            worst_scenario_id=None,
            passed=False,
            created_at=current,
            limitations=("no stress scenarios were supplied",),
            warnings=("empty stress suite is not a pass",),
        )
    results = tuple(
        apply_scenario(
            exposure,
            scenario,
            snapshot=snapshot,
            policy=policy,
            now=current,
        )
        for scenario in selected
    )
    worst = max(results, key=lambda result: result.total_loss)
    limitations: list[str] = [
        "Historical and hypothetical scenarios do not enumerate all future states.",
        "Scenario probabilities are absent unless explicitly evidenced.",
    ]
    warnings: list[str] = []
    for result in results:
        limitations.extend(result.limitations)
        if result.hypothetical:
            warnings.append(
                f"scenario {result.scenario_id} is hypothetical and has no calibrated probability"
            )
    passed = all(result.passed for result in results)
    report_id = fingerprint(
        "stress",
        STRESS_MODEL_VERSION,
        snapshot.snapshot_id,
        exposure.exposure_snapshot_id,
        tuple(selected),
        results,
    )
    return StressReport(
        report_id=report_id,
        portfolio_snapshot_id=snapshot.snapshot_id,
        exposure_snapshot_id=exposure.exposure_snapshot_id,
        model_version=STRESS_MODEL_VERSION,
        results=results,
        worst_case_loss=worst.total_loss,
        worst_scenario_id=worst.scenario_id,
        passed=passed,
        created_at=current,
        limitations=tuple(dict.fromkeys(limitations)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


__all__ = [
    "DEFAULT_STRESS_SCENARIOS",
    "STRESS_MODEL_VERSION",
    "ScenarioFamily",
    "StressReport",
    "StressResult",
    "StressScenario",
    "apply_scenario",
    "default_scenarios",
    "run_stress_suite",
]
