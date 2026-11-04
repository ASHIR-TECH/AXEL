"""Delisting prediction model interface and baseline for Phase 10.

Implements §4.3 of ``docs/AXEL_quant_research_pack.md``.
The interface follows the spec's recommended design: probability,
horizon, explanation, and stress testing.

The ``BaselineDelistingModel`` provides a heuristic baseline using
financial-distress indicators from the research literature
(Neuhäusler et al. 2025, Endri et al. 2020).

Architectural note: this module produces advisory predictions only.
It has no execution or trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DelistingFeatures:
    """Feature vector for delisting prediction (§3.1–3.3, §5)."""
    # Financial ratios
    pe_ratio: float = 0.0
    roe: float = 0.0
    operating_profit_margin: float = 0.0
    cash_ratio: float = 0.0
    debt_equity: float = 0.0
    roic: float = 0.0
    asset_turnover: float = 0.0
    quick_ratio: float = 0.0
    current_ratio: float = 0.0
    roa: float = 0.0
    leverage: float = 0.0
    long_term_debt: float = 0.0
    interest_coverage: float = 0.0
    # Trend features (slope of ratio over recent quarters, §3.1)
    roe_trend: float = 0.0
    profit_margin_trend: float = 0.0
    cash_ratio_trend: float = 0.0
    leverage_trend: float = 0.0
    # Market
    market_performance_12m: float = 0.0
    market_cap: float = 0.0
    # Macro
    inflation: float = 0.0
    gdp_growth: float = 0.0
    interest_rate: float = 0.0


@dataclass(frozen=True)
class DelistingPrediction:
    """Delisting prediction output (§4.3)."""
    probability: float
    horizon_months: int | None
    top_risk_factors: list[str] = field(default_factory=list)
    confidence: float = 0.0
    feature_importance: dict[str, float] = field(default_factory=dict)
    model_version: str = ""
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError(f"probability must be in [0, 1], got {self.probability}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass(frozen=True)
class StressResult:
    """Result of a stress test on delisting prediction."""
    scenario_name: str
    base_probability: float
    stressed_probability: float
    delta: float
    stressed_features: dict[str, float] = field(default_factory=dict)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# Default stress scenarios derived from §9.4
DEFAULT_STRESS_SCENARIOS: dict[str, dict[str, float]] = {
    "equity_crash": {
        "market_performance_12m": -0.40,
        "roe": -0.15,
        "roa": -0.10,
    },
    "credit_crunch": {
        "interest_rate": 0.05,
        "interest_coverage": -5.0,
        "leverage": 1.5,
    },
    "recession": {
        "gdp_growth": -0.04,
        "revenue_decline": -0.20,
        "operating_profit_margin": -0.10,
    },
    "liquidity_crisis": {
        "cash_ratio": -0.30,
        "current_ratio": -0.50,
        "quick_ratio": -0.40,
    },
}


class DelistingModel:
    """Abstract delisting prediction model (§4.3).

    Subclass this and override methods to plug in ML implementations.
    """

    def predict_probability(self, features: DelistingFeatures) -> float:
        raise NotImplementedError

    def predict_horizon(self, features: DelistingFeatures) -> int | None:
        raise NotImplementedError

    def explain(self, features: DelistingFeatures) -> dict[str, float]:
        raise NotImplementedError

    def stress_test(
        self,
        features: DelistingFeatures,
        scenarios: dict[str, dict[str, float]] | None = None,
    ) -> list[StressResult]:
        raise NotImplementedError

    def predict(self, features: DelistingFeatures) -> DelistingPrediction:
        raise NotImplementedError


class BaselineDelistingModel(DelistingModel):
    """Heuristic baseline using financial-distress indicators.

    Derived from research factors in Neuhäusler et al. (2025) and
    Endri et al. (2020).  This is a testing/comparison baseline,
    NOT a production prediction model.
    """

    MODEL_VERSION = "baseline-v1"

    def predict_probability(self, features: DelistingFeatures) -> float:
        score = 0.05  # base rate

        # Profitability deterioration (most informative per research)
        if features.roe < -0.20:
            score += 0.20
        elif features.roe < 0.0:
            score += 0.10
        elif features.roe < 0.05:
            score += 0.03

        if features.operating_profit_margin < -0.15:
            score += 0.15
        elif features.operating_profit_margin < 0.0:
            score += 0.08

        if features.roa < -0.10:
            score += 0.10
        elif features.roa < 0.0:
            score += 0.05

        # Leverage risk
        if features.debt_equity > 5.0:
            score += 0.15
        elif features.debt_equity > 3.0:
            score += 0.08

        if features.interest_coverage < 1.0 and features.interest_coverage != 0.0:
            score += 0.12
        elif features.interest_coverage < 2.0 and features.interest_coverage > 0.0:
            score += 0.05

        # Liquidity
        if features.cash_ratio < 0.05:
            score += 0.10
        if features.current_ratio < 0.5:
            score += 0.08

        # Negative trend signals
        if features.roe_trend < -0.05:
            score += 0.08
        if features.profit_margin_trend < -0.05:
            score += 0.06
        if features.leverage_trend > 0.10:
            score += 0.05

        # Market signal
        if features.market_performance_12m < -0.50:
            score += 0.10
        elif features.market_performance_12m < -0.30:
            score += 0.05

        # Very small companies are more at risk
        if features.market_cap > 0 and features.market_cap < 50_000_000:
            score += 0.08

        # PE ratio: negative PE or very high PE
        if features.pe_ratio < 0:
            score += 0.06

        return _clamp(score)

    def predict_horizon(self, features: DelistingFeatures) -> int | None:
        prob = self.predict_probability(features)
        if prob < 0.20:
            return None
        if prob > 0.60:
            return 6
        if prob > 0.40:
            return 9
        return 12

    def explain(self, features: DelistingFeatures) -> dict[str, float]:
        """Return feature importance scores."""
        importance: dict[str, float] = {}

        # Score each feature's contribution
        factors = {
            "roe": abs(min(features.roe, 0.0)) * 2.0,
            "operating_profit_margin": abs(min(features.operating_profit_margin, 0.0)) * 1.5,
            "roa": abs(min(features.roa, 0.0)) * 1.5,
            "debt_equity": max(features.debt_equity - 2.0, 0.0) * 0.3,
            "interest_coverage": max(2.0 - features.interest_coverage, 0.0) * 0.3 if features.interest_coverage > 0 else 0.2,
            "cash_ratio": max(0.2 - features.cash_ratio, 0.0) * 1.5,
            "current_ratio": max(1.0 - features.current_ratio, 0.0) * 0.8,
            "roe_trend": abs(min(features.roe_trend, 0.0)) * 1.0,
            "profit_margin_trend": abs(min(features.profit_margin_trend, 0.0)) * 0.8,
            "market_performance_12m": abs(min(features.market_performance_12m, 0.0)) * 0.5,
            "pe_ratio": 0.2 if features.pe_ratio < 0 else 0.0,
            "leverage_trend": max(features.leverage_trend, 0.0) * 0.5,
        }

        total = sum(factors.values()) or 1.0
        importance = {k: round(v / total, 4) for k, v in factors.items() if v > 0}
        return importance

    def stress_test(
        self,
        features: DelistingFeatures,
        scenarios: dict[str, dict[str, float]] | None = None,
    ) -> list[StressResult]:
        if scenarios is None:
            scenarios = DEFAULT_STRESS_SCENARIOS

        base_prob = self.predict_probability(features)
        results: list[StressResult] = []

        for name, shocks in scenarios.items():
            # Apply shocks additively to feature values
            stressed_values: dict[str, Any] = {}
            for feat_name, shock in shocks.items():
                if hasattr(features, feat_name):
                    current = getattr(features, feat_name)
                    stressed_values[feat_name] = current + shock

            # Create stressed features
            feature_dict = {
                f.name: getattr(features, f.name)
                for f in features.__dataclass_fields__.values()
            }
            feature_dict.update(stressed_values)
            stressed_features = DelistingFeatures(**feature_dict)
            stressed_prob = self.predict_probability(stressed_features)

            results.append(StressResult(
                scenario_name=name,
                base_probability=base_prob,
                stressed_probability=stressed_prob,
                delta=stressed_prob - base_prob,
                stressed_features=stressed_values,
            ))

        return results

    def predict(self, features: DelistingFeatures) -> DelistingPrediction:
        prob = self.predict_probability(features)
        horizon = self.predict_horizon(features)
        importance = self.explain(features)

        # Top risk factors
        sorted_factors = sorted(importance.items(), key=lambda x: x[1], reverse=True)
        top_factors = [name for name, _ in sorted_factors[:5]] if sorted_factors else []

        warnings: list[str] = []
        if features.pe_ratio == 0.0 and features.roe == 0.0 and features.roa == 0.0:
            warnings.append("all profitability features are zero — possible missing data")
        if features.market_cap == 0.0:
            warnings.append("market_cap is zero — may affect prediction quality")

        return DelistingPrediction(
            probability=prob,
            horizon_months=horizon,
            top_risk_factors=top_factors,
            confidence=0.35,  # low for baseline
            feature_importance=importance,
            model_version=self.MODEL_VERSION,
            warnings=warnings,
        )
