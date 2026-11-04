"""Tests for dividend and delisting prediction models (quant research pack §4.2–4.3)."""

from __future__ import annotations

import pytest

from axel.data.ml.dividend_model import (
    BaselineDividendModel,
    DividendFeatures,
    DividendModel,
    DividendPrediction,
)
from axel.data.ml.delisting_model import (
    BaselineDelistingModel,
    DelistingFeatures,
    DelistingModel,
    DelistingPrediction,
    StressResult,
)


# --- DividendModel ---


class TestDividendFeatures:
    def test_default_features(self):
        f = DividendFeatures()
        assert f.market_cap == 0.0
        assert f.beta == 1.0

    def test_custom_features(self):
        f = DividendFeatures(market_cap=1e9, net_margin=0.15, consecutive_payments=20)
        assert f.market_cap == 1e9
        assert f.consecutive_payments == 20


class TestDividendPrediction:
    def test_valid_prediction(self):
        p = DividendPrediction(
            probability_payment=0.8,
            probability_increase=0.3,
            probability_unchanged=0.4,
            probability_decrease=0.1,
            probability_omission=0.05,
            expected_dividend_growth=0.02,
            confidence=0.6,
        )
        assert p.probability_payment == 0.8

    def test_invalid_probability_rejected(self):
        with pytest.raises(ValueError, match="probability_payment"):
            DividendPrediction(
                probability_payment=1.5,
                probability_increase=0.3,
                probability_unchanged=0.4,
                probability_decrease=0.1,
                probability_omission=0.05,
                expected_dividend_growth=0.02,
                confidence=0.6,
            )

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            DividendPrediction(
                probability_payment=0.8,
                probability_increase=0.3,
                probability_unchanged=0.4,
                probability_decrease=0.1,
                probability_omission=0.05,
                expected_dividend_growth=0.02,
                confidence=-0.1,
            )


class TestDividendModelAbstract:
    def test_base_class_raises_not_implemented(self):
        model = DividendModel()
        f = DividendFeatures()
        with pytest.raises(NotImplementedError):
            model.predict_payment(f)
        with pytest.raises(NotImplementedError):
            model.predict_increase(f)
        with pytest.raises(NotImplementedError):
            model.predict_decrease(f)
        with pytest.raises(NotImplementedError):
            model.predict_omission(f)
        with pytest.raises(NotImplementedError):
            model.explain(f)
        with pytest.raises(NotImplementedError):
            model.predict(f)


class TestBaselineDividendModel:
    def test_healthy_payer_has_high_payment_probability(self):
        model = BaselineDividendModel()
        f = DividendFeatures(
            net_margin=0.15,
            consecutive_payments=15,
            payout_ratio=0.4,
            leverage=1.0,
        )
        assert model.predict_payment(f) > 0.7

    def test_unprofitable_firm_has_higher_omission(self):
        model = BaselineDividendModel()
        healthy = DividendFeatures(net_margin=0.15, consecutive_payments=10)
        distressed = DividendFeatures(net_margin=-0.15, omission_count=3)
        assert model.predict_omission(distressed) > model.predict_omission(healthy)

    def test_growing_firm_has_higher_increase(self):
        model = BaselineDividendModel()
        growing = DividendFeatures(
            net_margin=0.20,
            dividend_growth_3y=0.10,
            payout_ratio=0.3,
        )
        stagnant = DividendFeatures(
            net_margin=0.05,
            dividend_growth_3y=-0.02,
            payout_ratio=0.8,
        )
        assert model.predict_increase(growing) > model.predict_increase(stagnant)

    def test_high_leverage_increases_decrease_probability(self):
        model = BaselineDividendModel()
        low_leverage = DividendFeatures(leverage=0.5, net_margin=0.10)
        high_leverage = DividendFeatures(leverage=4.0, net_margin=-0.05)
        assert model.predict_decrease(high_leverage) > model.predict_decrease(low_leverage)

    def test_predict_returns_valid_prediction(self):
        model = BaselineDividendModel()
        f = DividendFeatures(
            net_margin=0.12,
            consecutive_payments=8,
            payout_ratio=0.5,
        )
        pred = model.predict(f)
        assert isinstance(pred, DividendPrediction)
        assert 0.0 <= pred.probability_payment <= 1.0
        assert 0.0 <= pred.probability_increase <= 1.0
        assert 0.0 <= pred.probability_unchanged <= 1.0
        assert 0.0 <= pred.probability_decrease <= 1.0
        assert 0.0 <= pred.probability_omission <= 1.0
        assert pred.model_version == "baseline-v1"

    def test_explain_returns_importances(self):
        model = BaselineDividendModel()
        f = DividendFeatures(consecutive_payments=12)
        imp = model.explain(f)
        assert isinstance(imp, dict)
        assert len(imp) > 0
        assert all(v >= 0 for v in imp.values())

    def test_all_probabilities_clamped_to_unit_interval(self):
        model = BaselineDividendModel()
        # Extreme distressed case
        f = DividendFeatures(
            net_margin=-0.30,
            leverage=5.0,
            omission_count=5,
            current_ratio=0.2,
            consecutive_payments=0,
        )
        pred = model.predict(f)
        assert 0.0 <= pred.probability_payment <= 1.0
        assert 0.0 <= pred.probability_omission <= 1.0

    def test_warning_on_high_payout_ratio(self):
        model = BaselineDividendModel()
        f = DividendFeatures(payout_ratio=1.5, consecutive_payments=5)
        pred = model.predict(f)
        assert any("payout_ratio" in w for w in pred.warnings)


# --- DelistingModel ---


class TestDelistingFeatures:
    def test_default_features(self):
        f = DelistingFeatures()
        assert f.pe_ratio == 0.0
        assert f.roe == 0.0

    def test_custom_features(self):
        f = DelistingFeatures(roe=-0.25, debt_equity=6.0, market_cap=1e7)
        assert f.roe == -0.25
        assert f.debt_equity == 6.0


class TestDelistingPrediction:
    def test_valid_prediction(self):
        p = DelistingPrediction(probability=0.5, horizon_months=12)
        assert p.probability == 0.5

    def test_invalid_probability_rejected(self):
        with pytest.raises(ValueError, match="probability"):
            DelistingPrediction(probability=1.5, horizon_months=12)

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            DelistingPrediction(probability=0.5, horizon_months=12, confidence=-0.1)


class TestDelistingModelAbstract:
    def test_base_class_raises_not_implemented(self):
        model = DelistingModel()
        f = DelistingFeatures()
        with pytest.raises(NotImplementedError):
            model.predict_probability(f)
        with pytest.raises(NotImplementedError):
            model.predict_horizon(f)
        with pytest.raises(NotImplementedError):
            model.explain(f)
        with pytest.raises(NotImplementedError):
            model.stress_test(f)
        with pytest.raises(NotImplementedError):
            model.predict(f)


class TestBaselineDelistingModel:
    def test_healthy_company_has_low_probability(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(
            roe=0.15,
            operating_profit_margin=0.12,
            roa=0.08,
            current_ratio=2.0,
            cash_ratio=0.5,
            debt_equity=0.5,
            interest_coverage=8.0,
            market_cap=5e9,
        )
        assert model.predict_probability(f) < 0.20

    def test_distressed_company_has_high_probability(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(
            roe=-0.30,
            operating_profit_margin=-0.20,
            roa=-0.15,
            current_ratio=0.3,
            cash_ratio=0.02,
            debt_equity=7.0,
            interest_coverage=0.5,
            market_cap=1e7,
            pe_ratio=-5.0,
            roe_trend=-0.10,
            profit_margin_trend=-0.08,
            leverage_trend=0.15,
            market_performance_12m=-0.60,
        )
        assert model.predict_probability(f) > 0.50

    def test_distressed_has_higher_probability_than_healthy(self):
        model = BaselineDelistingModel()
        healthy = DelistingFeatures(roe=0.15, roa=0.10, current_ratio=2.0)
        distressed = DelistingFeatures(roe=-0.25, roa=-0.12, current_ratio=0.3)
        assert model.predict_probability(distressed) > model.predict_probability(healthy)

    def test_predict_horizon_none_for_low_risk(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(roe=0.15, roa=0.10, current_ratio=2.0, market_cap=1e9)
        assert model.predict_horizon(f) is None

    def test_predict_horizon_shorter_for_high_risk(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(
            roe=-0.30,
            operating_profit_margin=-0.20,
            roa=-0.15,
            current_ratio=0.3,
            cash_ratio=0.02,
            debt_equity=7.0,
            interest_coverage=0.5,
            market_cap=1e7,
            pe_ratio=-5.0,
            roe_trend=-0.10,
            profit_margin_trend=-0.08,
            leverage_trend=0.15,
            market_performance_12m=-0.60,
        )
        horizon = model.predict_horizon(f)
        assert horizon is not None
        assert horizon <= 12

    def test_explain_returns_importances(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(roe=-0.10, debt_equity=4.0)
        imp = model.explain(f)
        assert isinstance(imp, dict)
        assert len(imp) > 0
        assert all(v >= 0 for v in imp.values())

    def test_stress_test_produces_results(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(
            roe=0.10,
            roa=0.05,
            current_ratio=1.5,
            cash_ratio=0.3,
            interest_coverage=5.0,
            market_performance_12m=0.05,
        )
        results = model.stress_test(f)
        assert len(results) > 0
        assert all(isinstance(r, StressResult) for r in results)

    def test_stress_test_increases_probability(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(
            roe=0.10,
            roa=0.05,
            current_ratio=1.5,
            cash_ratio=0.3,
            interest_coverage=5.0,
        )
        results = model.stress_test(f)
        # At least one scenario should increase delisting probability
        assert any(r.delta > 0 for r in results)

    def test_stress_test_with_custom_scenarios(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(roe=0.10)
        custom = {"total_collapse": {"roe": -0.50, "roa": -0.30}}
        results = model.stress_test(f, scenarios=custom)
        assert len(results) == 1
        assert results[0].scenario_name == "total_collapse"

    def test_predict_returns_valid_prediction(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(roe=0.10, roa=0.05, market_cap=1e9)
        pred = model.predict(f)
        assert isinstance(pred, DelistingPrediction)
        assert 0.0 <= pred.probability <= 1.0
        assert 0.0 <= pred.confidence <= 1.0
        assert pred.model_version == "baseline-v1"

    def test_predict_warns_on_zero_features(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures()  # all zeros
        pred = model.predict(f)
        assert any("missing data" in w for w in pred.warnings)

    def test_predict_top_risk_factors_populated(self):
        model = BaselineDelistingModel()
        f = DelistingFeatures(roe=-0.20, debt_equity=5.0)
        pred = model.predict(f)
        assert len(pred.top_risk_factors) > 0

    def test_probability_clamped_to_unit_interval(self):
        model = BaselineDelistingModel()
        # Extreme case with all negative indicators
        f = DelistingFeatures(
            roe=-0.50,
            operating_profit_margin=-0.30,
            roa=-0.25,
            current_ratio=0.1,
            cash_ratio=0.01,
            debt_equity=10.0,
            interest_coverage=0.1,
            market_cap=1e6,
            pe_ratio=-10.0,
            roe_trend=-0.20,
            profit_margin_trend=-0.15,
            leverage_trend=0.20,
            market_performance_12m=-0.70,
        )
        prob = model.predict_probability(f)
        assert 0.0 <= prob <= 1.0
