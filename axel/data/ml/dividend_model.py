"""Dividend prediction model interface and baseline for Phase 10.

Implements §4.2 of ``docs/AXEL_quant_research_pack.md``.
The interface follows the spec's recommended multi-output design:
payment, increase, decrease, omission probabilities plus explainability.

The ``BaselineDividendModel`` provides a simple rule-based implementation
for testing and comparison.  Production ML models should subclass
``DividendModel`` and override the prediction methods.

Architectural note: this module produces advisory predictions only.
It has no execution or trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DividendFeatures:
    """Feature vector for dividend prediction (§2.1 feature families)."""
    # Firm characteristics
    market_cap: float = 0.0
    total_assets: float = 0.0
    revenue: float = 0.0
    net_margin: float = 0.0         # profitability proxy
    roe: float = 0.0
    leverage: float = 0.0           # debt/equity
    current_ratio: float = 0.0      # liquidity
    beta: float = 1.0               # risk proxy
    # Dividend history
    previous_dividend: float = 0.0
    payout_ratio: float = 0.0
    dividend_growth_3y: float = 0.0
    omission_count: int = 0
    consecutive_payments: int = 0
    # Market variables
    volatility_60d: float = 0.0
    returns_12m: float = 0.0
    pe_ratio: float = 0.0
    # Macro variables
    risk_free_rate: float = 0.0
    inflation_rate: float = 0.0
    gdp_growth: float = 0.0


@dataclass(frozen=True)
class DividendPrediction:
    """Multi-output dividend prediction (§2.1 target design)."""
    probability_payment: float
    probability_increase: float
    probability_unchanged: float
    probability_decrease: float
    probability_omission: float
    expected_dividend_growth: float
    confidence: float
    feature_importance: dict[str, float] = field(default_factory=dict)
    model_version: str = ""
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in ("probability_payment", "probability_increase",
                      "probability_unchanged", "probability_decrease",
                      "probability_omission", "confidence"):
            val = getattr(self, name)
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {val}")


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class DividendModel:
    """Abstract dividend prediction model (§4.2).

    Subclass this and override prediction methods to plug in
    ML implementations.  The base class raises NotImplementedError.
    """

    def predict_payment(self, features: DividendFeatures) -> float:
        raise NotImplementedError

    def predict_increase(self, features: DividendFeatures) -> float:
        raise NotImplementedError

    def predict_decrease(self, features: DividendFeatures) -> float:
        raise NotImplementedError

    def predict_omission(self, features: DividendFeatures) -> float:
        raise NotImplementedError

    def explain(self, features: DividendFeatures) -> dict[str, float]:
        raise NotImplementedError

    def predict(self, features: DividendFeatures) -> DividendPrediction:
        raise NotImplementedError


class BaselineDividendModel(DividendModel):
    """Simple rule-based baseline for testing and comparison.

    Uses heuristics derived from the research literature:
    - Larger, more profitable firms with long payment histories
      are more likely to continue paying dividends.
    - High leverage, high volatility, and past omissions increase
      omission probability.
    """

    MODEL_VERSION = "baseline-v1"

    def predict_payment(self, features: DividendFeatures) -> float:
        score = 0.5
        # Consecutive payments strongly predict continuation
        if features.consecutive_payments > 10:
            score += 0.25
        elif features.consecutive_payments > 5:
            score += 0.15
        elif features.consecutive_payments > 0:
            score += 0.05
        # Profitability supports dividends
        if features.net_margin > 0.10:
            score += 0.10
        elif features.net_margin > 0.0:
            score += 0.05
        elif features.net_margin < 0.0:
            score -= 0.20
        # High leverage is a risk factor
        if features.leverage > 3.0:
            score -= 0.15
        elif features.leverage > 2.0:
            score -= 0.05
        # Past omissions
        if features.omission_count > 2:
            score -= 0.15
        elif features.omission_count > 0:
            score -= 0.05
        return _clamp(score)

    def predict_increase(self, features: DividendFeatures) -> float:
        score = 0.3
        if features.dividend_growth_3y > 0.05:
            score += 0.15
        if features.net_margin > 0.15:
            score += 0.10
        if features.payout_ratio < 0.5 and features.net_margin > 0.05:
            score += 0.10
        if features.leverage > 3.0:
            score -= 0.10
        return _clamp(score)

    def predict_decrease(self, features: DividendFeatures) -> float:
        score = 0.15
        if features.net_margin < 0.0:
            score += 0.20
        if features.payout_ratio > 0.9:
            score += 0.15
        if features.leverage > 3.0:
            score += 0.10
        if features.dividend_growth_3y < -0.05:
            score += 0.10
        return _clamp(score)

    def predict_omission(self, features: DividendFeatures) -> float:
        score = 0.10
        if features.net_margin < -0.10:
            score += 0.25
        elif features.net_margin < 0.0:
            score += 0.15
        if features.omission_count > 2:
            score += 0.20
        elif features.omission_count > 0:
            score += 0.10
        if features.current_ratio < 0.5:
            score += 0.10
        if features.leverage > 4.0:
            score += 0.10
        if features.consecutive_payments == 0:
            score += 0.15
        return _clamp(score)

    def explain(self, features: DividendFeatures) -> dict[str, float]:
        """Return feature importance scores (simplified rule-based)."""
        return {
            "consecutive_payments": 0.25 if features.consecutive_payments > 5 else 0.10,
            "net_margin": 0.20,
            "leverage": 0.15,
            "payout_ratio": 0.12,
            "omission_count": 0.10 if features.omission_count > 0 else 0.03,
            "current_ratio": 0.08,
            "dividend_growth_3y": 0.05,
            "volatility_60d": 0.03,
            "market_cap": 0.02,
        }

    def predict(self, features: DividendFeatures) -> DividendPrediction:
        p_payment = self.predict_payment(features)
        p_increase = self.predict_increase(features)
        p_decrease = self.predict_decrease(features)
        p_omission = self.predict_omission(features)

        # Derive unchanged as residual
        p_change = p_increase + p_decrease + p_omission
        p_unchanged = _clamp(1.0 - p_change) if p_change < 1.0 else 0.0

        # Simple expected growth estimate
        growth = (
            p_increase * max(features.dividend_growth_3y, 0.03)
            - p_decrease * 0.10
            - p_omission * features.previous_dividend
        )

        warnings: list[str] = []
        if features.consecutive_payments == 0 and features.previous_dividend > 0:
            warnings.append("inconsistent: has previous_dividend but zero consecutive_payments")
        if features.payout_ratio > 1.0:
            warnings.append("payout_ratio exceeds 100%")

        importance = self.explain(features)

        return DividendPrediction(
            probability_payment=p_payment,
            probability_increase=p_increase,
            probability_unchanged=p_unchanged,
            probability_decrease=p_decrease,
            probability_omission=p_omission,
            expected_dividend_growth=growth,
            confidence=0.4,  # low confidence for a baseline model
            feature_importance=importance,
            model_version=self.MODEL_VERSION,
            warnings=warnings,
        )
