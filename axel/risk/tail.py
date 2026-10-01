"""
Ex-ante tail-risk engine.

Directly implements Phase R1 of ``docs/AXEL_quant_research_pack.md``: VaR,
Expected Shortfall, marginal risk, stress testing, position limits and a portfolio
risk budget (Jorion 2007; Cassar & Gerakos 2017; StressVaR; Syriopoulos).

Three design points that follow from the research rather than convenience:

1. **Ex-ante, from current positions.** Risk is computed from the portfolio as
   it stands plus the proposed trade, not from the trade alone. Cassar &
   Gerakos found VaR-based funds predicted bear-market performance better, while
   bare position limits did not reduce left-tail risk -- so the binding
   constraint here is marginal portfolio tail risk.
2. **No fixed percentage.** ``RiskBudget.risk_fraction`` is a *cap* on risk per
   trade, not a target. The Jorion pipeline (signal -> position -> exposure ->
   volatility -> correlation -> VaR/ES -> budget -> max position) is implemented
   in that order in ``size_position``.
3. **Normal and extreme risk are separate layers.** ``TailRiskReport`` keeps
   ``portfolio_var`` / ``expected_shortfall`` (normal) apart from
   ``stress_loss`` / ``stress_var`` (extreme), so a strategy cannot pass by
   looking good only in ordinary conditions.

All estimates are deterministic, pure and dependency-free. Loss magnitudes are
positive numbers meaning "money at risk".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from axel.util.stats import (
    correlation,
    covariance,
    log_returns,
    mean,
    quantile,
    sample_std,
)

DEFAULT_CONFIDENCE = 0.95


@dataclass(frozen=True)
class StressScenario:
    """A named shock applied to prices/returns for tail testing."""

    name: str
    equity_shock: float
    volatility_multiplier: float = 1.0
    correlation_shift: float = 0.0
    single_name_shock: float = 0.0
    note: str = ""


SCENARIOS: dict[str, StressScenario] = {
    "equity_crash_2008": StressScenario(
        name="equity_crash_2008",
        equity_shock=-0.40,
        volatility_multiplier=2.5,
        correlation_shift=0.35,
        note="GFC-style broad selloff with correlation convergence.",
    ),
    "covid_2020": StressScenario(
        name="covid_2020",
        equity_shock=-0.34,
        volatility_multiplier=3.0,
        correlation_shift=0.40,
        note="Fastest one-month drawdown in the modern record.",
    ),
    "rates_shock_2022": StressScenario(
        name="rates_shock_2022",
        equity_shock=-0.22,
        volatility_multiplier=1.8,
        correlation_shift=0.15,
        note="Duration-sensitive drawdown with weaker single-name dispersion.",
    ),
    "single_name_default": StressScenario(
        name="single_name_default",
        equity_shock=0.0,
        volatility_multiplier=1.2,
        correlation_shift=0.0,
        single_name_shock=-1.0,
        note="Total loss of one position (delisting/insolvency).",
    ),
}


@dataclass(frozen=True)
class RiskBudget:
    """
    Portfolio-level risk limits.

    Every field is a *ceiling*. ``risk_fraction`` caps risk per trade rather
    than dictating a target size, which is the pack's rejection of a universal
    fixed percentage.
    """

    risk_fraction: float = 0.01
    max_portfolio_var: float = 0.02
    max_expected_shortfall: float = 0.03
    max_stress_loss: float = 0.10
    max_asset_exposure: float = 0.05
    max_strategy_exposure: float = 1.00
    max_sector_exposure: float = 0.20
    max_total_exposure: float = 1.00
    max_leverage: float = 1.00
    confidence: float = DEFAULT_CONFIDENCE
    min_history: int = 20
    scenarios: tuple[str, ...] = ("equity_crash_2008", "covid_2020", "single_name_default")

    def __post_init__(self) -> None:
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("risk_fraction must be in (0, 1]")
        for name in (
            "max_portfolio_var",
            "max_expected_shortfall",
            "max_stress_loss",
            "max_asset_exposure",
            "max_strategy_exposure",
            "max_sector_exposure",
            "max_total_exposure",
            "max_leverage",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if not 0.5 < self.confidence < 1:
            raise ValueError("confidence must be in (0.5, 1)")
        if self.min_history < 2:
            raise ValueError("min_history must be at least 2")
        for scenario in self.scenarios:
            if scenario not in SCENARIOS:
                raise ValueError(f"unknown stress scenario: {scenario}")


@dataclass(frozen=True)
class Position:
    """A holding the risk engine measures."""

    symbol: str
    quantity: float
    price: float
    sector: str = "unknown"

    @property
    def notional(self) -> float:
        return self.quantity * self.price

    @property
    def weight(self) -> float:
        return self.notional


@dataclass(frozen=True)
class ProposedTrade:
    symbol: str
    quantity: float
    price: float
    sector: str = "unknown"
    signal_confidence: float = 1.0

    @property
    def notional(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True)
class SizingDecision:
    """Result of the Jorion position-sizing pipeline."""

    approved_quantity: float
    approved_notional: float
    binding_constraint: str
    requested_notional: float
    realized_risk: float
    risk_fraction: float
    checks: tuple[tuple[str, bool, str], ...] = ()

    @property
    def approved(self) -> bool:
        return self.approved_quantity != 0.0


@dataclass(frozen=True)
class TailRiskReport:
    """Normal-risk and extreme-risk layers, kept separate on purpose."""

    portfolio_var: float
    expected_shortfall: float
    portfolio_volatility: float
    stress_loss: float
    worst_stress_loss: float
    worst_scenario: str
    total_exposure: float
    concentration: float
    average_correlation: float
    marginal_var: float = 0.0
    observations: int = 0

    def breaches(
        self, budget: RiskBudget
    ) -> tuple[tuple[str, float, float], ...]:
        """Every budget ceiling exceeded, as ``(name, limit, actual)``."""
        checks = (
            ("max_portfolio_var", budget.max_portfolio_var, self.portfolio_var),
            (
                "max_expected_shortfall",
                budget.max_expected_shortfall,
                self.expected_shortfall,
            ),
            ("max_stress_loss", budget.max_stress_loss, self.worst_stress_loss),
            ("max_total_exposure", budget.max_total_exposure, self.total_exposure),
        )
        return tuple(item for item in checks if item[2] > item[1])

    def within_budget(self, budget: RiskBudget) -> bool:
        return not self.breaches(budget)


def historical_var(returns: Sequence[float], confidence: float = DEFAULT_CONFIDENCE) -> float:
    """
    Historical-simulation VaR as a positive loss fraction.

    Uses the empirical lower tail quantile, so it makes no distributional
    assumption and reflects the sample's actual fat tails.
    """
    if not returns:
        return 0.0
    tail = quantile(returns, 1.0 - confidence)
    return max(0.0, -tail)


def expected_shortfall(
    returns: Sequence[float], confidence: float = DEFAULT_CONFIDENCE
) -> float:
    """
    Mean loss beyond the VaR threshold (expected shortfall / CVaR).

    Averaging the tail rather than reporting the threshold alone, because the
    threshold understates the loss you actually take in the bad states.
    """
    if not returns:
        return 0.0
    threshold = -historical_var(returns, confidence)
    tail = [value for value in returns if value <= threshold]
    if not tail:
        return max(0.0, -threshold)
    return max(0.0, -mean(tail))


def portfolio_var_from_weights(
    asset_returns: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
    confidence: float = DEFAULT_CONFIDENCE,
) -> float:
    """
    Historical VaR of a weighted portfolio, resampled onto a common date index.

    Assets with different observation counts must be aligned before combining,
    otherwise the portfolio return series is fabricated from mismatched points.
    """
    aligned = _align(asset_returns, weights)
    if not aligned:
        return 0.0
    length = min(len(series) for series in aligned.values())
    if length == 0:
        return 0.0
    return historical_var(_portfolio_returns(aligned, weights), confidence)


def expected_shortfall_from_weights(
    asset_returns: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
    confidence: float = DEFAULT_CONFIDENCE,
) -> float:
    aligned = _align(asset_returns, weights)
    if not aligned:
        return 0.0
    return expected_shortfall(_portfolio_returns(aligned, weights), confidence)


def _align(
    asset_returns: Mapping[str, Sequence[float]], weights: Mapping[str, float]
) -> dict[str, list[float]]:
    aligned: dict[str, list[float]] = {}
    for name, series in asset_returns.items():
        weight = weights.get(name, 0.0)
        if weight == 0 or not series:
            continue
        aligned[name] = list(series)
    return aligned


def stress_loss(
    position: Position | ProposedTrade,
    scenario: StressScenario,
    equity: float,
) -> float:
    """Loss under one scenario, as a positive fraction of equity."""
    if equity <= 0:
        return 0.0
    gross_exposure = abs(position.quantity)
    if gross_exposure == 0:
        return 0.0
    weight = position.notional / equity
    if scenario.single_name_shock:
        return min(1.0, abs(weight) * abs(scenario.single_name_shock))
    return min(1.0, abs(weight) * abs(scenario.equity_shock))


def marginal_var(
    portfolio_returns: Sequence[float],
    asset_returns: Sequence[float],
    confidence: float = DEFAULT_CONFIDENCE,
) -> float:
    """
    First-order marginal VaR: the extra portfolio VaR from a unit-weight
    position in an asset.

    Uses the standard delta form ``beta(asset, portfolio) * VaR(asset)`` where
    ``beta = cov(asset, portfolio) / var(portfolio)``. The beta weighting is the
    whole point: an asset uncorrelated with the book contributes far less than
    one that amplifies it, so ``calculate_position_limit`` can reject a trade
    that is small in isolation but concentrated in tail risk.
    """
    if len(portfolio_returns) < 2 or len(asset_returns) < 2:
        return 0.0
    length = min(len(portfolio_returns), len(asset_returns))
    asset = list(asset_returns[-length:])
    portfolio = list(portfolio_returns[-length:])
    portfolio_variance = sample_std(portfolio) ** 2
    if portfolio_variance == 0:
        return 0.0
    beta = covariance(asset, portfolio) / portfolio_variance
    asset_var = historical_var(asset, confidence)
    return max(0.0, abs(beta) * asset_var)


class TailRiskEngine:
    """
    Ex-ante risk measurement and position limiting.

    Stateless: every input is passed explicitly so the engine stays a pure
    function of (positions, history, budget, trade). No I/O, no LLM, no state.
    """

    def __init__(self, budget: RiskBudget | None = None) -> None:
        self.budget = budget or RiskBudget()

    def calculate_var(
        self,
        returns: Sequence[float],
        *,
        confidence: float | None = None,
    ) -> float:
        """Portfolio VaR from a return series (positive loss fraction)."""
        return historical_var(returns, confidence or self.budget.confidence)

    def calculate_expected_shortfall(
        self,
        returns: Sequence[float],
        *,
        confidence: float | None = None,
    ) -> float:
        return expected_shortfall(returns, confidence or self.budget.confidence)

    def calculate_stress_loss(
        self, position: Position | ProposedTrade, scenario: str, *, equity: float
    ) -> float:
        return stress_loss(position, SCENARIOS[scenario], equity)

    def calculate_marginal_risk(
        self,
        portfolio_returns: Sequence[float],
        asset_returns: Sequence[float],
        *,
        confidence: float | None = None,
    ) -> float:
        return marginal_var(portfolio_returns, asset_returns, confidence or self.budget.confidence)

    def calculate_volatility(
        self, prices: Sequence[float], *, horizon_bars: int = 1
    ) -> float:
        """Per-bar log-return volatility scaled to a horizon."""
        series = log_returns(list(prices))
        if len(series) < 2:
            return 0.0
        return sample_std(series) * horizon_bars**0.5

    def calculate_portfolio_volatility(
        self, asset_returns: Mapping[str, Sequence[float]], weights: Mapping[str, float]
    ) -> float:
        aligned = _align(asset_returns, weights)
        if not aligned:
            return 0.0
        return sample_std(_portfolio_returns(aligned, weights))

    def calculate_position_limit(
        self,
        trade: ProposedTrade,
        portfolio: Sequence[Position],
        *,
        equity: float,
        asset_returns: Mapping[str, Sequence[float]],
    ) -> SizingDecision:
        """
        The Jorion pipeline, in order.

        Applies, from most binding to least: risk-per-trade cap scaled by
        volatility, single-name exposure, total exposure, concentration,
        marginal portfolio VaR and stress loss. The tightest wins and is
        reported as ``binding_constraint`` so the rejection reason is legible.
        """
        budget = self.budget
        if equity <= 0 or trade.price <= 0:
            return SizingDecision(0.0, 0.0, "INVALID_INPUTS", trade.notional, 0.0, 0.0)

        requested = abs(trade.notional)
        asset_series = list(asset_returns.get(trade.symbol, ()))
        # asset_returns are already returns, so volatility is their std directly.
        volatility = (
            sample_std(asset_series[-budget.min_history :]) if len(asset_series) >= 2 else 0.0
        )
        reference_vol = max(volatility, 1e-6)

        total_exposure = sum(abs(position.notional) for position in portfolio)
        equity = max(equity, 1e-9)
        weights = {
            position.symbol: position.notional / equity for position in portfolio
        }
        # Every cap below is a ceiling on the resulting *position*, so each one is
        # charged for whatever the book already holds in that name first. Without
        # this netting, repeated small adds would compound past the ceiling.
        existing_signed = sum(
            position.notional
            for position in portfolio
            if position.symbol == trade.symbol
        )
        existing_name = abs(existing_signed)

        # Reducing a position never increases risk, so it is never blocked. A gate
        # that could veto an exit would trap the strategy in the riskiest holding
        # it owns, which is the opposite of what a risk limit is for.
        if existing_signed and (existing_signed > 0) != (trade.notional > 0):
            return SizingDecision(
                approved_quantity=trade.quantity,
                approved_notional=requested,
                binding_constraint="RISK_REDUCTION",
                requested_notional=requested,
                realized_risk=requested * reference_vol,
                risk_fraction=budget.risk_fraction,
            )

        # Volatility targeting: hold the notional whose one-sigma move equals the
        # risk budget, so a 4%-vol name gets a smaller size than a 40%-vol name.
        risk_budget_money = equity * budget.risk_fraction
        volatility_adjusted = risk_budget_money / reference_vol
        candidates: list[tuple[str, float]] = [
            ("RISK_FRACTION_CAP", volatility_adjusted),
            (
                "ASSET_EXPOSURE_CAP",
                max(0.0, equity * budget.max_asset_exposure - existing_name),
            ),
            (
                "TOTAL_EXPOSURE_CAP",
                max(0.0, equity * budget.max_total_exposure - total_exposure),
            ),
        ]

        # Sector concentration: an asset adds tail risk to its peers, so the cap
        # applies to the sector's total exposure including this name's own.
        same_sector = [position for position in portfolio if position.sector == trade.sector]
        sector_exposure = sum(abs(position.notional) for position in same_sector)
        candidates.append(
            (
                "SECTOR_EXPOSURE_CAP",
                max(0.0, equity * budget.max_sector_exposure - sector_exposure),
            )
        )
        gross_leverage = total_exposure
        candidates.append(
            (
                "LEVERAGE_CAP",
                max(0.0, equity * budget.max_leverage - gross_leverage),
            )
        )

        if asset_series:
            aligned = min(
                (len(series) for name, series in asset_returns.items() if weights.get(name)),
                default=0,
            )
            if aligned >= budget.min_history:
                portfolio_returns = _portfolio_returns(asset_returns, weights)
                marginal = marginal_var(
                    portfolio_returns,
                    asset_series,
                    budget.confidence,
                )
                if marginal > 0:
                    # marginal and headroom are both VaR *fractions*, so the
                    # ratio is a permissible portfolio weight, which converts to
                    # notional through equity -- not through the share price.
                    headroom = max(
                        0.0,
                        budget.max_portfolio_var
                        - self.calculate_var(portfolio_returns),
                    )
                    candidates.append(
                        ("MARGINAL_VAR_CAP", headroom / marginal * equity)
                    )

                marginal_es = _marginal_expected_shortfall(
                    asset_returns,
                    weights,
                    trade.symbol,
                    budget.confidence,
                    requested / equity,
                )
                if marginal_es > 0:
                    current_es = expected_shortfall(
                        portfolio_returns, budget.confidence
                    )
                    headroom_es = max(0.0, budget.max_expected_shortfall - current_es)
                    # marginal_es is ES per unit of weight, so the headroom
                    # converts straight into a weight and then a notional.
                    candidates.append(
                        ("EXPECTED_SHORTFALL_CAP", headroom_es / marginal_es * equity)
                    )

        stress_cap = self._stress_loss_cap(trade, portfolio, equity)
        if stress_cap is not None:
            candidates.append(stress_cap)

        binding, permitted_notional = min(candidates, key=lambda item: item[1])
        permitted_notional = max(0.0, min(permitted_notional, requested))
        quantity = permitted_notional / trade.price
        # Dollar risk = permitted notional x per-bar sigma (the same quantity the
        # RISK_FRACTION_CAP equalises against the budget, so the two agree).
        realized_risk = permitted_notional * reference_vol
        checks = tuple(
            (name, permitted >= requested - 1e-9, f"{name}: permitted={permitted:.2f} of requested={requested:.2f}")
            for name, permitted in candidates
        )
        return SizingDecision(
            approved_quantity=quantity,
            approved_notional=permitted_notional,
            binding_constraint=binding,
            requested_notional=requested,
            realized_risk=realized_risk,
            risk_fraction=budget.risk_fraction,
            checks=checks,
        )

    def _stress_loss_cap(
        self,
        trade: ProposedTrade,
        portfolio: Sequence[Position],
        equity: float,
    ) -> tuple[str, float] | None:
        """
        Notional that keeps the worst-scenario loss inside ``max_stress_loss``.

        Stress loss is linear in weight, so the existing book's loss plus the
        trade's incremental loss gives the cap in closed form: the worst scenario
        is the one that hurts the new position most, once the book's own worst
        loss is already spent. Returns ``None`` when scenarios cannot be applied.
        """
        if equity <= 0 or not trade.symbol:
            return None
        book = [
            position
            for position in portfolio
            if position.quantity != 0 and position.notional != 0
        ]
        worst_book_loss = max(
            (_scenario_loss(book, SCENARIOS[name], equity) for name in self.budget.scenarios),
            default=0.0,
        )
        # A default of the name alone is the harshest shock the trade can face,
        # so sizing against it keeps every scenario inside the ceiling.
        harshest = max(
            (
                abs(SCENARIOS[name].single_name_shock or SCENARIOS[name].equity_shock)
                for name in self.budget.scenarios
            ),
            default=0.0,
        )
        if harshest <= 0:
            return None
        headroom = max(0.0, self.budget.max_stress_loss - worst_book_loss)
        return ("STRESS_LOSS_CAP", headroom / harshest * equity)

    def evaluate_portfolio(
        self,
        positions: Sequence[Position],
        *,
        equity: float,
        asset_returns: Mapping[str, Sequence[float]],
    ) -> TailRiskReport:
        """Full normal + extreme risk picture for the current book."""
        if not positions:
            return TailRiskReport(
                portfolio_var=0.0,
                expected_shortfall=0.0,
                portfolio_volatility=0.0,
                stress_loss=0.0,
                worst_stress_loss=0.0,
                worst_scenario="",
                total_exposure=0.0,
                concentration=0.0,
                average_correlation=0.0,
            )
        weights = {position.symbol: position.notional / equity for position in positions}
        portfolio_returns = _portfolio_returns(asset_returns, weights)
        per_scenario = {
            name: _scenario_loss(positions, SCENARIOS[name], equity)
            for name in self.budget.scenarios
        }
        worst_scenario = max(per_scenario, key=per_scenario.get) if per_scenario else ""
        worst = per_scenario.get(worst_scenario, 0.0)
        mean_loss = mean(list(per_scenario.values())) if per_scenario else 0.0
        notionals = [abs(position.notional) for position in positions]
        total = sum(notionals)
        return TailRiskReport(
            portfolio_var=historical_var(portfolio_returns, self.budget.confidence),
            expected_shortfall=expected_shortfall(
                portfolio_returns, self.budget.confidence
            ),
            portfolio_volatility=sample_std(portfolio_returns),
            stress_loss=mean_loss,
            worst_stress_loss=worst,
            worst_scenario=worst_scenario,
            total_exposure=total / equity if equity > 0 else 0.0,
            concentration=max(notionals) / total if total > 0 else 0.0,
            average_correlation=_average_correlation(asset_returns, weights),
            observations=len(portfolio_returns),
        )


def _scenario_loss(
    positions: Sequence[Position], scenario: StressScenario, equity: float
) -> float:
    """
    Portfolio loss under one scenario, as a fraction of equity.

    The two scenario kinds aggregate differently on purpose. A broad equity
    shock hits every name, so losses add. ``single_name_default`` is one issuer
    failing, so only the largest single loss applies -- summing it across the
    book would imply the whole portfolio defaulted at once and would wildly
    overstate the risk. Capped at 1.0 because equity cannot be lost twice.
    """
    if equity <= 0 or not positions:
        return 0.0
    if scenario.single_name_shock:
        return min(1.0, max(stress_loss(p, scenario, equity) for p in positions))
    total = sum(stress_loss(p, scenario, equity) for p in positions)
    return min(1.0, total)


def _marginal_expected_shortfall(
    asset_returns: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
    symbol: str,
    confidence: float,
    added_weight: float,
) -> float:
    """
    Expected-shortfall increment per unit of added portfolio weight.

    A first-order measure: revalue the book with the candidate name's weight
    added, and divide the ES difference by that weight. ES is not linear, so this
    is a local slope at the current book -- which is where a sizing decision is
    actually being made. Returns 0 when the name has no history to revalue with.
    """
    if added_weight <= 0 or not asset_returns.get(symbol):
        return 0.0
    base = expected_shortfall(_portfolio_returns(asset_returns, weights), confidence)
    combined = dict(weights)
    combined[symbol] = weights.get(symbol, 0.0) + added_weight
    with_candidate = expected_shortfall(
        _portfolio_returns(asset_returns, combined), confidence
    )
    return max(0.0, (with_candidate - base) / added_weight)


def _portfolio_returns(
    asset_returns: Mapping[str, Sequence[float]], weights: Mapping[str, float]
) -> list[float]:
    aligned = _align(asset_returns, weights)
    if not aligned:
        return []
    length = min(len(series) for series in aligned.values())
    if length == 0:
        return []
    return [
        sum(weights.get(name, 0.0) * series[-length:][index] for name, series in aligned.items())
        for index in range(length)
    ]


def _average_correlation(
    asset_returns: Mapping[str, Sequence[float]], weights: Mapping[str, float]
) -> float:
    aligned = _align(asset_returns, weights)
    if len(aligned) < 2:
        return 0.0
    length = min(len(series) for series in aligned.values())
    if length < 2:
        return 0.0
    names = sorted(aligned)
    values: list[float] = []
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            values.append(
                correlation(
                    list(aligned[left][-length:]), list(aligned[right][-length:])
                )
            )
    return mean(values) if values else 0.0


__all__ = [
    "DEFAULT_CONFIDENCE",
    "SCENARIOS",
    "Position",
    "ProposedTrade",
    "RiskBudget",
    "SizingDecision",
    "StressScenario",
    "TailRiskEngine",
    "TailRiskReport",
    "expected_shortfall",
    "expected_shortfall_from_weights",
    "historical_var",
    "marginal_var",
    "portfolio_var_from_weights",
    "stress_loss",
]