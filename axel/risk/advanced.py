"""
Advanced Risk Intelligence for Phase 13.

Implements Section 9 of the Developer 2 Technical Research Specification:
- Volatility estimation: EWMA and GARCH(1,1) recursive variance (§9.2).
- Correlation stress, rolling correlation, and breakdown detection (§9.3).
- Multi-scenario stress testing: equity crash, interest rate shock, currency shock,
  crypto exchange failure, liquidity collapse, volatility spike, broker outage,
  stablecoin depeg, and prediction-market settlement failure (§9.4).
- Severity-graded risk alerts: warning, elevated_risk, limit_breach, emergency_halt (§9.5).

Zero LLM imports. All limits and calculations are deterministic and auditable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from axel.util.stats import correlation, variance

# ---------------------------------------------------------------------------
# 1. Volatility Estimation: EWMA & GARCH(1,1) (§9.2)
# ---------------------------------------------------------------------------


def ewma_variance(
    returns: Sequence[float],
    decay: float = 0.94,
    initial_variance: float | None = None,
) -> list[float]:
    """Compute EWMA recursive variance series: sigma_t^2 = lambda*sigma_{t-1}^2 + (1-lambda)*r_{t-1}^2.

    Parameters
    ----------
    returns : period-over-period return series.
    decay : decay parameter lambda in [0, 1) (RiskMetrics standard = 0.94).
    initial_variance : optional prior variance; if None, uses sample variance.

    Returns
    -------
    List of conditional variances for each period.
    """
    if not returns:
        return []
    if not 0.0 < decay < 1.0:
        raise ValueError(f"decay parameter must be in (0, 1), got {decay}")

    if initial_variance is not None and initial_variance > 0:
        current_var = initial_variance
    else:
        current_var = variance(returns) if len(returns) >= 2 else (returns[0] ** 2 or 1e-6)

    series: list[float] = []
    for r in returns:
        current_var = decay * current_var + (1.0 - decay) * (r**2)
        series.append(current_var)
    return series


def ewma_volatility(
    returns: Sequence[float],
    decay: float = 0.94,
    initial_variance: float | None = None,
) -> list[float]:
    """Compute EWMA volatility (standard deviation) series."""
    vars_series = ewma_variance(returns, decay=decay, initial_variance=initial_variance)
    return [math.sqrt(max(v, 0.0)) for v in vars_series]


@dataclass(frozen=True)
class GARCHParameters:
    """Parameters for GARCH(1,1) model: sigma_t^2 = omega + alpha*eps_{t-1}^2 + beta*sigma_{t-1}^2."""

    omega: float
    alpha: float
    beta: float

    def __post_init__(self) -> None:
        if self.omega <= 0.0:
            raise ValueError(f"omega must be positive, got {self.omega}")
        if self.alpha < 0.0 or self.beta < 0.0:
            raise ValueError(f"alpha and beta must be non-negative, got alpha={self.alpha}, beta={self.beta}")
        if self.alpha + self.beta >= 1.0:
            raise ValueError(
                f"alpha + beta must be strictly less than 1.0 for covariance stationarity, "
                f"got {self.alpha + self.beta}"
            )

    @property
    def persistence(self) -> float:
        """Persistence of volatility shocks (alpha + beta)."""
        return self.alpha + self.beta

    @property
    def unconditional_variance(self) -> float:
        """Long-run unconditional variance V_L = omega / (1 - alpha - beta)."""
        return self.omega / (1.0 - self.alpha - self.beta)

    @property
    def unconditional_volatility(self) -> float:
        """Long-run unconditional volatility sqrt(V_L)."""
        return math.sqrt(self.unconditional_variance)


def garch_variance(
    residuals: Sequence[float],
    params: GARCHParameters,
    initial_variance: float | None = None,
) -> list[float]:
    """Calculate GARCH(1,1) conditional variance series for a sequence of shock residuals.

    sigma_t^2 = omega + alpha * eps_{t-1}^2 + beta * sigma_{t-1}^2
    """
    if not residuals:
        return []

    current_var = initial_variance if initial_variance is not None else params.unconditional_variance
    series: list[float] = []

    for eps in residuals:
        current_var = params.omega + params.alpha * (eps**2) + params.beta * current_var
        series.append(current_var)
    return series


def garch_forecast(
    last_variance: float,
    params: GARCHParameters,
    horizon: int,
) -> list[float]:
    """Forecast future conditional variance over k steps ahead.

    ``last_variance`` is the *current* filtered variance ``sigma_t^2`` (for
    example the final value from :func:`garch_variance`), so the first
    returned element is the one-step forecast ``sigma_{t+1}^2``:

        E[sigma_{t+k}^2] = V_L + (alpha + beta)^k * (sigma_t^2 - V_L)
    """
    if horizon <= 0:
        return []
    v_l = params.unconditional_variance
    pers = params.persistence
    forecasts: list[float] = []
    for k in range(1, horizon + 1):
        f = v_l + (pers**k) * (last_variance - v_l)
        forecasts.append(max(f, 1e-8))
    return forecasts


# ---------------------------------------------------------------------------
# 2. Correlation Stress & Breakdown Detection (§9.3)
# ---------------------------------------------------------------------------


def rolling_correlation(
    series_a: Sequence[float],
    series_b: Sequence[float],
    window: int = 30,
) -> list[float]:
    """Compute rolling Pearson correlation between two return series over a rolling window."""
    n = min(len(series_a), len(series_b))
    if n < window or window < 2:
        return []
    results: list[float] = []
    for i in range(window, n + 1):
        window_a = series_a[i - window : i]
        window_b = series_b[i - window : i]
        results.append(correlation(window_a, window_b))
    return results


class RiskAlertSeverity(StrEnum):
    """Severity classification for risk events (§9.5)."""

    WARNING = "warning"
    ELEVATED_RISK = "elevated_risk"
    LIMIT_BREACH = "limit_breach"
    EMERGENCY_HALT = "emergency_halt"


@dataclass(frozen=True)
class CorrelationBreakdownResult:
    """Result of correlation breakdown test between two asset series."""

    pair: tuple[str, str]
    historical_correlation: float
    recent_correlation: float
    correlation_shift: float
    is_breakdown: bool
    severity: RiskAlertSeverity
    notes: str = ""


def detect_correlation_breakdown(
    pair: tuple[str, str],
    historical_a: Sequence[float],
    historical_b: Sequence[float],
    recent_a: Sequence[float],
    recent_b: Sequence[float],
    breakdown_threshold: float = 0.35,
) -> CorrelationBreakdownResult:
    """Detect correlation breakdown or contagion between two assets.

    In crisis regimes, previously uncorrelated or negatively correlated assets
    often become strongly correlated, destroying diversification benefits.
    """
    hist_corr = correlation(historical_a, historical_b)
    rec_corr = correlation(recent_a, recent_b)
    shift = abs(rec_corr - hist_corr)

    # Contagion: both correlation shifting up sharply or flipping sign
    is_breakdown = shift >= breakdown_threshold

    if shift >= 0.60:
        severity = RiskAlertSeverity.LIMIT_BREACH
        notes = f"Severe correlation breakdown: shifted by {shift:.2f} ({hist_corr:.2f} -> {rec_corr:.2f})"
    elif is_breakdown:
        severity = RiskAlertSeverity.ELEVATED_RISK
        notes = f"Elevated correlation shift: shifted by {shift:.2f} ({hist_corr:.2f} -> {rec_corr:.2f})"
    else:
        severity = RiskAlertSeverity.WARNING
        notes = "Correlation within acceptable drift boundaries"

    return CorrelationBreakdownResult(
        pair=pair,
        historical_correlation=round(hist_corr, 4),
        recent_correlation=round(rec_corr, 4),
        correlation_shift=round(shift, 4),
        is_breakdown=is_breakdown,
        severity=severity,
        notes=notes,
    )


def stress_correlation_matrix(
    base_corr_matrix: list[list[float]],
    stress_factor: float = 0.50,
) -> list[list[float]]:
    """Adjust a correlation matrix towards crisis levels (increasing correlations towards 1.0).

    corr_stressed[i][j] = corr[i][j] + stress_factor * (1.0 - corr[i][j]) for i != j.
    """
    n = len(base_corr_matrix)
    stressed: list[list[float]] = []
    for i in range(n):
        row: list[float] = []
        for j in range(n):
            if i == j:
                row.append(1.0)
            else:
                c = base_corr_matrix[i][j]
                c_stressed = c + stress_factor * (1.0 - c)
                row.append(max(-1.0, min(1.0, c_stressed)))
        stressed.append(row)
    return stressed


# ---------------------------------------------------------------------------
# 3. Explicit Crisis Stress Scenarios (§9.4)
# ---------------------------------------------------------------------------


class StressScenarioType(StrEnum):
    """The 9 explicit stress testing scenarios defined in spec §9.4."""

    EQUITY_CRASH = "equity_market_crash"
    INTEREST_RATE_SHOCK = "interest_rate_shock"
    CURRENCY_SHOCK = "currency_shock"
    CRYPTO_EXCHANGE_FAILURE = "crypto_exchange_failure"
    LIQUIDITY_COLLAPSE = "liquidity_collapse"
    VOLATILITY_SPIKE = "volatility_spike"
    BROKER_OUTAGE = "broker_outage"
    STABLECOIN_DEPEG = "stablecoin_depeg"
    PREDICTION_SETTLEMENT_FAILURE = "prediction_market_settlement_failure"


@dataclass(frozen=True)
class AssetPosition:
    """An asset position evaluated for stress risk."""

    symbol: str
    asset_class: str  # "stocks", "crypto", "options", "forex", "prediction"
    market_value: float  # signed (+ for long, - for short)
    beta: float = 1.0
    liquidity_score: float = 1.0  # 0.0 to 1.0


@dataclass(frozen=True)
class StressScenarioSpec:
    """Parameters and shock factors for an explicit stress scenario."""

    scenario_type: StressScenarioType
    name: str
    description: str
    equity_shock: float = 0.0
    crypto_shock: float = 0.0
    forex_shock: float = 0.0
    options_vol_shock: float = 0.0
    prediction_loss_ratio: float = 0.0
    liquidity_haircut: float = 0.0
    assumptions: list[str] = field(default_factory=list)


DEFAULT_ADVANCED_SCENARIOS: dict[StressScenarioType, StressScenarioSpec] = {
    StressScenarioType.EQUITY_CRASH: StressScenarioSpec(
        scenario_type=StressScenarioType.EQUITY_CRASH,
        name="Equity Market Crash",
        description="Global equity selloff with high market beta transmission",
        equity_shock=-0.35,
        crypto_shock=-0.25,
        forex_shock=-0.05,
        options_vol_shock=1.00,
        assumptions=["Beta scales equity shock", "Correlations spike towards 1"],
    ),
    StressScenarioType.INTEREST_RATE_SHOCK: StressScenarioSpec(
        scenario_type=StressScenarioType.INTEREST_RATE_SHOCK,
        name="Interest Rate Shock",
        description="Rapid 250bps central bank rate increase impacting growth equities and duration",
        equity_shock=-0.15,
        crypto_shock=-0.20,
        forex_shock=-0.08,
        assumptions=["Growth equities underperform", "Leverage costs rise"],
    ),
    StressScenarioType.CURRENCY_SHOCK: StressScenarioSpec(
        scenario_type=StressScenarioType.CURRENCY_SHOCK,
        name="Currency Devaluation Shock",
        description="Major currency dislocation with FX volatility surge",
        equity_shock=-0.05,
        forex_shock=-0.18,
        assumptions=["Cross-currency basis widens", "Emerging FX collapses"],
    ),
    StressScenarioType.CRYPTO_EXCHANGE_FAILURE: StressScenarioSpec(
        scenario_type=StressScenarioType.CRYPTO_EXCHANGE_FAILURE,
        name="Crypto Exchange Insolvency",
        description="Major crypto venue freeze and cross-exchange contagion",
        crypto_shock=-0.50,
        liquidity_haircut=0.30,
        assumptions=["Venue balances frozen", "Severe crypto discount"],
    ),
    StressScenarioType.LIQUIDITY_COLLAPSE: StressScenarioSpec(
        scenario_type=StressScenarioType.LIQUIDITY_COLLAPSE,
        name="Market Liquidity Collapse",
        description="Bid-ask spreads blow out across all sections",
        equity_shock=-0.10,
        crypto_shock=-0.20,
        forex_shock=-0.05,
        liquidity_haircut=0.25,
        assumptions=["Illiquid names suffer high haircut", "Exits forced at penalty"],
    ),
    StressScenarioType.VOLATILITY_SPIKE: StressScenarioSpec(
        scenario_type=StressScenarioType.VOLATILITY_SPIKE,
        name="Volatility Spike Shock",
        description="Implied volatility doubles in a single trading session",
        equity_shock=-0.12,
        options_vol_shock=1.20,
        crypto_shock=-0.15,
        assumptions=["Short volatility trades severely penalized"],
    ),
    StressScenarioType.BROKER_OUTAGE: StressScenarioSpec(
        scenario_type=StressScenarioType.BROKER_OUTAGE,
        name="Primary Broker Outage",
        description="Orders cannot be modified or routed for multiple hours during adverse move",
        equity_shock=-0.08,
        crypto_shock=-0.12,
        liquidity_haircut=0.15,
        assumptions=["Unhedged slippage during downtime"],
    ),
    StressScenarioType.STABLECOIN_DEPEG: StressScenarioSpec(
        scenario_type=StressScenarioType.STABLECOIN_DEPEG,
        name="Stablecoin Depeg Event",
        description="Major reserve stablecoin drops to 0.80 on secondary markets",
        crypto_shock=-0.25,
        liquidity_haircut=0.20,
        assumptions=["Crypto dollar pairs trade at deep discount"],
    ),
    StressScenarioType.PREDICTION_SETTLEMENT_FAILURE: StressScenarioSpec(
        scenario_type=StressScenarioType.PREDICTION_SETTLEMENT_FAILURE,
        name="Prediction Market Dispute Failure",
        description="Contested oracle resolution or settlement contract deadlock",
        prediction_loss_ratio=1.00,
        assumptions=["Total loss of capital allocated to contested contracts"],
    ),
}


@dataclass(frozen=True)
class ScenarioEvaluationResult:
    """Stress loss evaluation for a single scenario."""

    scenario: StressScenarioType
    portfolio_value_before: float
    portfolio_value_after: float
    stress_loss_usd: float
    stress_loss_pct: float
    severity: RiskAlertSeverity


def evaluate_scenario_stress(
    positions: Sequence[AssetPosition],
    scenario_spec: StressScenarioSpec,
    portfolio_equity: float,
) -> ScenarioEvaluationResult:
    """Calculate PortfolioStressLoss = PortfolioValue_before - PortfolioValue_after (§9.4)."""
    val_before = sum(p.market_value for p in positions)
    val_after = 0.0

    for p in positions:
        mv = p.market_value
        shock = 0.0
        ac = p.asset_class.lower()

        if ac == "stocks":
            shock = scenario_spec.equity_shock * p.beta
        elif ac in ("crypto", "cryptocurrency"):
            shock = scenario_spec.crypto_shock
        elif ac == "forex":
            shock = scenario_spec.forex_shock
        elif ac == "options":
            # Options suffer equity shock plus volatility shock
            shock = scenario_spec.equity_shock * p.beta + scenario_spec.options_vol_shock * 0.10
        elif ac in ("bets", "prediction", "prediction_market"):
            shock = -scenario_spec.prediction_loss_ratio
        else:
            shock = scenario_spec.equity_shock * 0.5

        # Apply liquidity haircut to illiquid positions
        haircut = scenario_spec.liquidity_haircut * (1.0 - max(0.0, min(1.0, p.liquidity_score)))
        net_multiplier = 1.0 + shock - haircut

        # Long positions lose when multiplier < 1, short positions gain when underlying falls
        if mv >= 0:
            val_after += mv * max(net_multiplier, 0.0)
        else:
            # Short position: inverse shock
            val_after += mv * (1.0 - shock + haircut)

    loss_usd = max(0.0, val_before - val_after)
    equity = portfolio_equity if portfolio_equity > 0 else (val_before or 1.0)
    loss_pct = loss_usd / equity

    # Classify severity per §9.5
    if loss_pct >= 0.20:
        sev = RiskAlertSeverity.EMERGENCY_HALT
    elif loss_pct >= 0.10:
        sev = RiskAlertSeverity.LIMIT_BREACH
    elif loss_pct >= 0.05:
        sev = RiskAlertSeverity.ELEVATED_RISK
    else:
        sev = RiskAlertSeverity.WARNING

    return ScenarioEvaluationResult(
        scenario=scenario_spec.scenario_type,
        portfolio_value_before=round(val_before, 2),
        portfolio_value_after=round(val_after, 2),
        stress_loss_usd=round(loss_usd, 2),
        stress_loss_pct=round(loss_pct, 4),
        severity=sev,
    )


# ---------------------------------------------------------------------------
# 4. Comprehensive Advanced Risk Assessment (§9.1–9.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdvancedRiskAssessment:
    """Full cross-asset risk assessment across volatility, correlation, and stress testing."""

    portfolio_equity: float
    current_volatility_ewma: float
    current_volatility_garch: float
    worst_scenario: StressScenarioType
    worst_stress_loss_usd: float
    worst_stress_loss_pct: float
    scenario_results: list[ScenarioEvaluationResult]
    correlation_breakdowns: list[CorrelationBreakdownResult]
    overall_severity: RiskAlertSeverity
    recommended_action: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AdvancedRiskEngine:
    """Phase 13 Advanced Risk Intelligence Engine.

    Operates in advisory and risk-intelligence mode. Does NOT place orders or
    bypass deterministic execution gates.
    """

    def __init__(
        self,
        garch_params: GARCHParameters | None = None,
        ewma_decay: float = 0.94,
        max_stress_loss_pct: float = 0.15,
    ) -> None:
        self.ewma_decay = ewma_decay
        self.garch_params = garch_params or GARCHParameters(omega=0.00001, alpha=0.09, beta=0.88)
        self.max_stress_loss_pct = max_stress_loss_pct

    def assess_portfolio(
        self,
        positions: Sequence[AssetPosition],
        portfolio_equity: float,
        portfolio_returns: Sequence[float],
        pair_returns: dict[tuple[str, str], tuple[Sequence[float], Sequence[float], Sequence[float], Sequence[float]]]
        | None = None,
    ) -> AdvancedRiskAssessment:
        """Run complete Phase 13 risk assessment across volatility, correlations, and 9 crisis scenarios."""
        # 1. Volatility estimation
        if portfolio_returns:
            ewma_vols = ewma_volatility(portfolio_returns, decay=self.ewma_decay)
            cur_ewma_vol = ewma_vols[-1] if ewma_vols else 0.0
            garch_vars = garch_variance(portfolio_returns, self.garch_params)
            cur_garch_vol = math.sqrt(garch_vars[-1]) if garch_vars else 0.0
        else:
            cur_ewma_vol = 0.0
            cur_garch_vol = self.garch_params.unconditional_volatility

        # 2. Crisis scenario stress testing (§9.4)
        scenario_results: list[ScenarioEvaluationResult] = []
        for scen_spec in DEFAULT_ADVANCED_SCENARIOS.values():
            res = evaluate_scenario_stress(positions, scen_spec, portfolio_equity)
            scenario_results.append(res)

        worst = max(scenario_results, key=lambda r: r.stress_loss_usd) if scenario_results else None
        worst_scen = worst.scenario if worst else StressScenarioType.EQUITY_CRASH
        worst_usd = worst.stress_loss_usd if worst else 0.0
        worst_pct = worst.stress_loss_pct if worst else 0.0

        # 3. Correlation breakdown detection (§9.3)
        breakdowns: list[CorrelationBreakdownResult] = []
        if pair_returns:
            for pair, (h_a, h_b, r_a, r_b) in pair_returns.items():
                b_res = detect_correlation_breakdown(pair, h_a, h_b, r_a, r_b)
                if b_res.is_breakdown:
                    breakdowns.append(b_res)

        # 4. Synthesize severity per §9.5
        severities = [r.severity for r in scenario_results] + [b.severity for b in breakdowns]
        if RiskAlertSeverity.EMERGENCY_HALT in severities or worst_pct >= 0.20:
            overall_sev = RiskAlertSeverity.EMERGENCY_HALT
            action = "Recommend triggering emergency halt: stress loss exceeds 20% capital threshold."
        elif RiskAlertSeverity.LIMIT_BREACH in severities or worst_pct > self.max_stress_loss_pct:
            overall_sev = RiskAlertSeverity.LIMIT_BREACH
            action = f"Limit breach detected in scenario '{worst_scen.value}': reduce exposure to stay within limits."
        elif RiskAlertSeverity.ELEVATED_RISK in severities or cur_ewma_vol > 0.35:
            overall_sev = RiskAlertSeverity.ELEVATED_RISK
            action = "Elevated risk regime: scale down position sizing and pause new strategy allocations."
        else:
            overall_sev = RiskAlertSeverity.WARNING
            action = "Portfolio risk parameters within normal tolerances."

        return AdvancedRiskAssessment(
            portfolio_equity=portfolio_equity,
            current_volatility_ewma=round(cur_ewma_vol, 4),
            current_volatility_garch=round(cur_garch_vol, 4),
            worst_scenario=worst_scen,
            worst_stress_loss_usd=worst_usd,
            worst_stress_loss_pct=worst_pct,
            scenario_results=scenario_results,
            correlation_breakdowns=breakdowns,
            overall_severity=overall_sev,
            recommended_action=action,
        )
