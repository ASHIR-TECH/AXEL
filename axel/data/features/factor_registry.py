"""
Machine-readable factor registry.

Implements sections 5 and 10 of ``docs/AXEL_quant_research_pack.md``: every
factor imported from research is registered with its provenance, and a factor
only leaves ``UNVALIDATED`` after independent point-in-time out-of-sample testing
inside AXEL.

The pack's rule -- "never mark a factor PROVEN merely because a paper found it
useful" -- is enforced by ``FactorSpec.__post_init__``, which raises if a factor
claims ``PROVEN`` without the retest evidence fields filled in. That makes
promotion a data requirement, not a judgment call.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

REQUIRED_PROVENANCE = (
    "source_paper",
    "source_year",
    "source_market",
    "source_period",
    "feature_definition",
)


class RetestStatus(str, Enum):
    """AXEL's own validation state, independent of what any paper reported."""

    UNVALIDATED = "UNVALIDATED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    PIT_RETESTED = "PIT_RETESTED"
    REJECTED = "REJECTED"

    @property
    def is_usable_live(self) -> bool:
        return self is RetestStatus.PIT_RETESTED


class FactorFamily(str, Enum):
    RISK = "risk"
    DIVIDEND = "dividend"
    DELISTING = "delisting"


@dataclass(frozen=True)
class FactorSpec:
    """One research-derived factor with full provenance."""

    factor_id: str
    family: FactorFamily
    source_paper: str
    source_year: int
    source_market: str
    source_period: str
    source_population: str
    feature_definition: str
    target_definition: str
    prediction_horizon: str
    validation_method: str
    reported_metric: str
    known_limitations: str
    point_in_time_requirement: str
    retest_status: RetestStatus = RetestStatus.UNVALIDATED
    reported_importance: str = ""
    axel_retest_date: date | None = None
    axel_oos_trades: int = 0
    axel_deflated_sharpe: float | None = None
    axel_retest_fingerprint: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.factor_id:
            raise ValueError("factor_id is required")
        for name in REQUIRED_PROVENANCE:
            value = getattr(self, name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"{self.factor_id}: {name} is required")
        if self.retest_status is RetestStatus.PIT_RETESTED:
            self._require_retest_evidence()

    def _require_retest_evidence(self) -> None:
        """A factor cannot claim PIT_RETESTED without the AXEL evidence."""
        missing = [
            name
            for name, value in (
                ("axel_retest_date", self.axel_retest_date),
                ("axel_retest_fingerprint", self.axel_retest_fingerprint),
                ("axel_deflated_sharpe", self.axel_deflated_sharpe),
            )
            if not value
        ]
        if self.axel_oos_trades <= 0:
            missing.append("axel_oos_trades")
        if missing:
            raise ValueError(
                f"{self.factor_id}: {RetestStatus.PIT_RETESTED.value} requires "
                f"{', '.join(missing)}. Reported paper metrics are not evidence."
            )

    def record_retest(
        self,
        *,
        retest_date: date,
        oos_trades: int,
        deflated_sharpe: float,
        fingerprint: str,
    ) -> FactorSpec:
        """Promote the factor only when AXEL's own retest supplies the evidence."""
        from dataclasses import replace

        return replace(
            self,
            retest_status=RetestStatus.PIT_RETESTED,
            axel_retest_date=retest_date,
            axel_oos_trades=oos_trades,
            axel_deflated_sharpe=deflated_sharpe,
            axel_retest_fingerprint=fingerprint,
        )

    def reject(self, *, reason: str) -> FactorSpec:
        from dataclasses import replace

        return replace(self, retest_status=RetestStatus.REJECTED, notes=reason)

    @property
    def usable_live(self) -> bool:
        return self.retest_status.is_usable_live

    def to_dict(self) -> dict[str, object]:
        return {
            "factor_id": self.factor_id,
            "family": self.family.value,
            "source_paper": self.source_paper,
            "source_year": self.source_year,
            "source_market": self.source_market,
            "source_period": self.source_period,
            "source_population": self.source_population,
            "feature_definition": self.feature_definition,
            "target_definition": self.target_definition,
            "prediction_horizon": self.prediction_horizon,
            "validation_method": self.validation_method,
            "reported_metric": self.reported_metric,
            "known_limitations": self.known_limitations,
            "point_in_time_requirement": self.point_in_time_requirement,
            "retest_status": self.retest_status.value,
            "usable_live": self.usable_live,
            "axel_oos_trades": self.axel_oos_trades,
            "axel_deflated_sharpe": self.axel_deflated_sharpe,
            "axel_retest_fingerprint": self.axel_retest_fingerprint,
        }


@dataclass(frozen=True)
class FactorRegistry:
    """Immutable collection of factor specs keyed by ``factor_id``."""

    factors: tuple[FactorSpec, ...] = field(default_factory=tuple)

    @classmethod
    def from_specs(cls, specs: Iterable[FactorSpec]) -> FactorRegistry:
        by_id: dict[str, FactorSpec] = {}
        for spec in specs:
            if spec.factor_id in by_id:
                raise ValueError(f"duplicate factor_id: {spec.factor_id}")
            by_id[spec.factor_id] = spec
        return cls(tuple(sorted(by_id.values(), key=lambda item: item.factor_id)))

    def __len__(self) -> int:
        return len(self.factors)

    def __iter__(self) -> Iterator[FactorSpec]:
        return iter(self.factors)

    def __contains__(self, factor_id: object) -> bool:
        return any(spec.factor_id == factor_id for spec in self.factors)

    def get(self, factor_id: str) -> FactorSpec:
        for spec in self.factors:
            if spec.factor_id == factor_id:
                return spec
        raise KeyError(factor_id)

    def by_family(self, family: FactorFamily) -> tuple[FactorSpec, ...]:
        return tuple(spec for spec in self.factors if spec.family is family)

    def live_ready(self) -> tuple[FactorSpec, ...]:
        return tuple(spec for spec in self.factors if spec.usable_live)

    def unvalidated(self) -> tuple[FactorSpec, ...]:
        return tuple(
            spec
            for spec in self.factors
            if spec.retest_status
            in (RetestStatus.UNVALIDATED, RetestStatus.RESEARCH_ONLY)
        )

    def summary(self) -> dict[str, int]:
        counts = {status.value: 0 for status in RetestStatus}
        for spec in self.factors:
            counts[spec.retest_status.value] += 1
        return counts


# --- Seeds from the research pack ------------------------------------------
# Every seed is UNVALIDATED by construction: a paper's reported importance is
# recorded as ``reported_metric`` and never as AXEL evidence.

_PIT = "available_at <= decision_time; no revision lookahead"

RISK_FACTORS: Sequence[FactorSpec] = (
    FactorSpec(
        factor_id="risk_portfolio_var",
        family=FactorFamily.RISK,
        source_paper="Jorion, Risk Management for Hedge Funds with Position Information",
        source_year=2007,
        source_market="US hedge funds",
        source_period="2006-2007",
        source_population="hedge funds using derivatives",
        feature_definition="Historical-simulation Value at Risk of current positions",
        target_definition="tail loss over horizon",
        prediction_horizon="short-term (frequent remeasurement)",
        validation_method="institutional backtest with position data",
        reported_metric="better VaR calibration, not a trading alpha",
        known_limitations="derivative-heavy funds; not directly generalisable to equities",
        point_in_time_requirement=_PIT,
        reported_importance="core risk control",
        retest_status=RetestStatus.RESEARCH_ONLY,
    ),
    FactorSpec(
        factor_id="risk_expected_shortfall",
        family=FactorFamily.RISK,
        source_paper="Jorion; StressVaR (Coste, Douady & Zovko)",
        source_year=2007,
        source_market="US hedge funds",
        source_period="2006-2007",
        source_population="multi-asset funds",
        feature_definition="Mean return beyond the VaR threshold",
        target_definition="expected tail loss",
        prediction_horizon="1-period",
        validation_method="empirical tail estimation",
        reported_metric="tail severity measure, not a predictor",
        known_limitations="requires an adequate tail sample; ES > 0 needs few effective observations",
        point_in_time_requirement=_PIT,
        reported_importance="tail-risk layer",
    ),
    FactorSpec(
        factor_id="risk_portfolio_volatility",
        family=FactorFamily.RISK,
        source_paper="Jorion",
        source_year=2007,
        source_market="US hedge funds",
        source_period="2006-2007",
        source_population="dynamic strategies",
        feature_definition="Realised volatility of portfolio returns, vol-targeted",
        target_definition="position size from risk budget",
        prediction_horizon="1-period",
        validation_method="risk-budget sizing",
        reported_metric="normal-risk layer",
        known_limitations="assumes returns are informative about the future",
        point_in_time_requirement=_PIT,
        reported_importance="normal-risk layer",
    ),
    FactorSpec(
        factor_id="risk_stress_loss",
        family=FactorFamily.RISK,
        source_paper="StressVaR (Coste, Douady & Zovko); Cassar & Gerakos",
        source_year=2005,
        source_market="global",
        source_period="2005-2017",
        source_population="funds",
        feature_definition="Loss under named historical/scenario shocks",
        target_definition="extreme loss under stress",
        prediction_horizon="event",
        validation_method="scenario analysis",
        reported_metric="extreme-risk layer; VaR models better in 2008 down months",
        known_limitations="scenario set is chosen by the analyst",
        point_in_time_requirement=_PIT,
        reported_importance="extreme-risk layer",
    ),
    FactorSpec(
        factor_id="risk_concentration",
        family=FactorFamily.RISK,
        source_paper="Cassar & Gerakos",
        source_year=2017,
        source_market="US hedge funds",
        source_period="2007-2009 crisis",
        source_population="hedge funds",
        feature_definition="Sector and single-name exposure as a fraction of equity",
        target_definition="position limit",
        prediction_horizon="1-period",
        validation_method="empirical cross-section",
        reported_metric="bare position limits did NOT reduce left-tail risk",
        known_limitations="paper finds limits alone insufficient; formal VaR models mattered",
        point_in_time_requirement=_PIT,
        reported_importance="control, not predictor",
    ),
)

DIVIDEND_FACTORS: Sequence[FactorSpec] = (
    FactorSpec(
        factor_id="dividend_firm_size",
        family=FactorFamily.DIVIDEND,
        source_paper="Ivascu, Understanding Dividend Puzzle Using Machine Learning",
        source_year=2023,
        source_market="reported study market",
        source_period="2023",
        source_population="listed firms",
        feature_definition="Market capitalisation and total assets",
        target_definition="dividend-paying propensity",
        prediction_horizon="next fiscal year",
        validation_method="PCA + multiple ML models + SHAP",
        reported_metric="size reported as most informative determinant",
        known_limitations="association only; reported accuracy is not a production guarantee",
        point_in_time_requirement=_PIT,
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="dividend_payout_ratio",
        family=FactorFamily.DIVIDEND,
        source_paper="Ivascu; Bhat, Predicting Dividend Omission Behaviour",
        source_year=2022,
        source_market="India (Bhat)",
        source_period="2013-2018",
        source_population="12,942 firm-year observations, manufacturing and non-financial services",
        feature_definition="Dividends paid over trailing earnings, plus omission history",
        target_definition="probability of dividend omission",
        prediction_horizon="next fiscal year",
        validation_method="multiple classifiers, 55% base rate of omission",
        reported_metric="MLP accuracy 82.36%, ROC AUC 0.901 (reported, unverified)",
        known_limitations="class imbalance; paper explicitly warns importance != causation",
        point_in_time_requirement=_PIT,
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="dividend_profitability",
        family=FactorFamily.DIVIDEND,
        source_paper="Bhat; Ivascu",
        source_year=2022,
        source_market="India",
        source_period="2013-2018",
        source_population="listed non-financial firms",
        feature_definition="ROA, ROE, operating margin and cash flow",
        target_definition="dividend omission / payment",
        prediction_horizon="next fiscal year",
        validation_method="logistic regression, RF, GBM, SVM, ANN",
        reported_metric="profitability among top reported feature groups",
        known_limitations="firm-level accounting timing; must use filing availability",
        point_in_time_requirement=_PIT,
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="dividend_dividend_history",
        family=FactorFamily.DIVIDEND,
        source_paper="Bhat; Ivascu",
        source_year=2022,
        source_market="India",
        source_period="2013-2018",
        source_population="listed non-financial firms",
        feature_definition="Consecutive payment history and growth rate",
        target_definition="P(unchanged), P(increase), P(decrease), P(omission)",
        prediction_horizon="next fiscal year",
        validation_method="multi-class framing rather than a single binary target",
        reported_metric="not separately reported",
        known_limitations="long histories required; sparse for recent listings",
        point_in_time_requirement=_PIT,
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="dividend_leverage",
        family=FactorFamily.DIVIDEND,
        source_paper="Bhat",
        source_year=2022,
        source_market="India",
        source_period="2013-2018",
        source_population="listed non-financial firms",
        feature_definition="Debt/equity and interest coverage",
        target_definition="dividend omission",
        prediction_horizon="next fiscal year",
        validation_method="classical classifiers",
        reported_metric="financial risk among informative groups",
        known_limitations="financial-sector behaviour differs and is usually excluded",
        point_in_time_requirement=_PIT,
        reported_importance="medium",
    ),
)

DELISTING_FACTORS: Sequence[FactorSpec] = (
    FactorSpec(
        factor_id="delisting_pe_ratio",
        family=FactorFamily.DELISTING,
        source_paper="Neuhausler, Yoon, Levine & Fan, Prediction of Delisting Using an ML Ensemble",
        source_year=2025,
        source_market="US (NYSE, NYSE American, NASDAQ)",
        source_period="1970-2022",
        source_population="8,870 companies; 6,652 train / 2,218 test",
        feature_definition="Price/Earnings ratio from point-in-time financial statements",
        target_definition="delisting within one year",
        prediction_horizon="1 year",
        validation_method="10-fold CV + held-out test, MCC/F1 thresholding",
        reported_metric="accuracy 0.8363, MCC 0.4566 (reported, unverified)",
        known_limitations="survivorship-critical: delisted names must be retained in training",
        point_in_time_requirement=_PIT + "; universe must retain delisted securities",
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="delisting_roe",
        family=FactorFamily.DELISTING,
        source_paper="Neuhausler et al.",
        source_year=2025,
        source_market="US",
        source_period="1970-2022",
        source_population="8,870 listed companies",
        feature_definition="Return on equity",
        target_definition="delisting within one year",
        prediction_horizon="1 year",
        validation_method="ensemble of LR, RF, GBM, SVM, NN with meta-learner",
        reported_metric="among most informative predictors",
        known_limitations="negative-equity firms make ROE undefined",
        point_in_time_requirement=_PIT,
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="delisting_operating_profit_margin",
        family=FactorFamily.DELISTING,
        source_paper="Neuhausler et al.",
        source_year=2025,
        source_market="US",
        source_period="1970-2022",
        source_population="8,870 listed companies",
        feature_definition="Operating profit margin",
        target_definition="delisting within one year",
        prediction_horizon="1 year",
        validation_method="financial-ratio feature engine + ensemble",
        reported_metric="among most informative predictors",
        known_limitations="ratio can be negative and unstable for distressed firms",
        point_in_time_requirement=_PIT,
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="delisting_cash_ratio",
        family=FactorFamily.DELISTING,
        source_paper="Neuhausler et al.",
        source_year=2025,
        source_market="US",
        source_period="1970-2022",
        source_population="8,870 listed companies",
        feature_definition="Cash ratio (cash and equivalents over current liabilities)",
        target_definition="delisting within one year",
        prediction_horizon="1 year",
        validation_method="financial-ratio feature engine + ensemble",
        reported_metric="among most informative predictors",
        known_limitations="negative cash flow can make the ratio undefined",
        point_in_time_requirement=_PIT,
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="delisting_financial_ratio_trends",
        family=FactorFamily.DELISTING,
        source_paper="Neuhausler et al.",
        source_year=2025,
        source_market="US",
        source_period="1970-2022",
        source_population="8,870 listed companies",
        feature_definition="Slope over up to 20 previous quarterly reports with a one-quarter gap",
        target_definition="delisting within one year",
        prediction_horizon="1 year",
        validation_method="trend features into ensemble",
        reported_metric="deterioration trend emphasised by authors",
        known_limitations="requires 20 quarters of history; unusable for recent listings",
        point_in_time_requirement=_PIT + "; one-quarter gap before the target window",
        reported_importance="high",
    ),
    FactorSpec(
        factor_id="delisting_inflation",
        family=FactorFamily.DELISTING,
        source_paper="Neuhausler et al.",
        source_year=2025,
        source_market="US",
        source_period="1970-2022",
        source_population="8,870 listed companies",
        feature_definition="CPI inflation",
        target_definition="delisting within one year",
        prediction_horizon="1 year",
        validation_method="macro features in ensemble",
        reported_metric="among most informative predictors",
        known_limitations="macro series are revised; use FRED vintage, not latest",
        point_in_time_requirement=_PIT + "; macro must use release vintage",
        reported_importance="medium",
    ),
    FactorSpec(
        factor_id="delisting_svm_configurations",
        family=FactorFamily.DELISTING,
        source_paper="Endri, Kasmir & Dewi, Delisting Sharia Stock Prediction Model Based on Financial Information",
        source_year=2020,
        source_market="Indonesia (Sharia)",
        source_period="2012-2017",
        source_population="102 companies of 335 Sharia stocks",
        feature_definition="Debt/equity, ROIC, asset turnover, quick and current ratio, ROA, ROE, leverage, interest coverage",
        target_definition="delisting",
        prediction_horizon="1 year",
        validation_method="SVM with multiple configurations",
        reported_metric="one configuration reports 100% accuracy",
        known_limitations="THE PAPER'S 100% IS SMALL-SAMPLE OVERFITTING; explicitly not an AXEL target",
        point_in_time_requirement=_PIT,
        reported_importance="feature engineering only",
        retest_status=RetestStatus.RESEARCH_ONLY,
    ),
    FactorSpec(
        factor_id="delisting_post_merger",
        family=FactorFamily.DELISTING,
        source_paper="Thompson & Kim, On Modeling Acquirer Delisting Post-Merger Using ML",
        source_year=2024,
        source_market="reported study market",
        source_period="2024",
        source_population="post-merger acquirers only",
        feature_definition="Post-merger characteristics with RF + SHAP",
        target_definition="acquirer delisting post-merger",
        prediction_horizon="post-merger",
        validation_method="tuned Random Forest with SHAP",
        reported_metric="not general-market evidence",
        known_limitations="narrow population; not transferable to general delisting",
        point_in_time_requirement=_PIT,
        reported_importance="narrow",
    ),
)

RESEARCH_REGISTRY = FactorRegistry.from_specs(
    [*RISK_FACTORS, *DIVIDEND_FACTORS, *DELISTING_FACTORS]
)


__all__ = [
    "DELISTING_FACTORS",
    "DIVIDEND_FACTORS",
    "REQUIRED_PROVENANCE",
    "RESEARCH_REGISTRY",
    "RISK_FACTORS",
    "FactorFamily",
    "FactorRegistry",
    "FactorSpec",
    "RetestStatus",
]