"""Unit tests for Phase 13 Advanced Risk Intelligence."""

from __future__ import annotations

import pytest

from axel.risk.advanced import (
    DEFAULT_ADVANCED_SCENARIOS,
    AdvancedRiskAssessment,
    AdvancedRiskEngine,
    AssetPosition,
    GARCHParameters,
    RiskAlertSeverity,
    StressScenarioType,
    detect_correlation_breakdown,
    evaluate_scenario_stress,
    ewma_variance,
    ewma_volatility,
    garch_forecast,
    garch_variance,
    rolling_correlation,
    stress_correlation_matrix,
)

# --- EWMA Volatility Tests ---


class TestEWMAVolatility:
    def test_empty_returns_returns_empty(self):
        assert ewma_variance([]) == []
        assert ewma_volatility([]) == []

    def test_invalid_decay_raises(self):
        with pytest.raises(ValueError, match="decay parameter"):
            ewma_variance([0.01, 0.02], decay=1.5)
        with pytest.raises(ValueError, match="decay parameter"):
            ewma_variance([0.01, 0.02], decay=-0.1)
        with pytest.raises(ValueError, match="decay parameter"):
            ewma_variance([0.01, 0.02], decay=0.0)

    def test_ewma_recursive_step(self):
        returns = [0.02, -0.03, 0.01]
        decay = 0.90
        init_var = 0.0004
        vars_res = ewma_variance(returns, decay=decay, initial_variance=init_var)
        assert len(vars_res) == 3

        step1 = decay * init_var + (1.0 - decay) * (0.02**2)
        assert vars_res[0] == pytest.approx(step1, rel=1e-6)

        step2 = decay * step1 + (1.0 - decay) * ((-0.03) ** 2)
        assert vars_res[1] == pytest.approx(step2, rel=1e-6)

    def test_ewma_reacts_to_large_shock(self):
        calm = [0.001] * 20
        shock = calm + [0.08]
        vols_calm = ewma_volatility(calm)
        vols_shock = ewma_volatility(shock)
        assert vols_shock[-1] > vols_calm[-1] * 2.0


# --- GARCH(1,1) Tests ---


class TestGARCH:
    def test_invalid_parameters_raise(self):
        # omega <= 0
        with pytest.raises(ValueError, match="omega must be positive"):
            GARCHParameters(omega=0.0, alpha=0.1, beta=0.8)

        # alpha or beta < 0
        with pytest.raises(ValueError, match="non-negative"):
            GARCHParameters(omega=0.0001, alpha=-0.1, beta=0.8)

        # non-stationary (alpha + beta >= 1)
        with pytest.raises(ValueError, match="covariance stationarity"):
            GARCHParameters(omega=0.0001, alpha=0.3, beta=0.75)

    def test_unconditional_variance_and_volatility(self):
        params = GARCHParameters(omega=0.00001, alpha=0.09, beta=0.88)
        assert params.persistence == pytest.approx(0.97, rel=1e-6)
        expected_v_l = 0.00001 / (1.0 - 0.97)
        assert params.unconditional_variance == pytest.approx(expected_v_l, rel=1e-6)
        assert params.unconditional_volatility == pytest.approx(expected_v_l**0.5, rel=1e-6)

    def test_garch_variance_calculation(self):
        params = GARCHParameters(omega=0.00001, alpha=0.10, beta=0.85)
        residuals = [0.01, -0.02, 0.03]
        init_var = 0.0004
        g_vars = garch_variance(residuals, params, initial_variance=init_var)
        assert len(g_vars) == 3

        v1 = params.omega + params.alpha * (0.01**2) + params.beta * init_var
        assert g_vars[0] == pytest.approx(v1, rel=1e-6)

        v2 = params.omega + params.alpha * ((-0.02) ** 2) + params.beta * v1
        assert g_vars[1] == pytest.approx(v2, rel=1e-6)

    def test_garch_forecast_converges_to_unconditional(self):
        params = GARCHParameters(omega=0.00001, alpha=0.09, beta=0.88)
        high_init = params.unconditional_variance * 5.0
        forecasts = garch_forecast(high_init, params, horizon=200)
        assert len(forecasts) == 200
        # Over long horizon, forecast should mean-revert towards V_L
        assert forecasts[-1] < high_init
        assert forecasts[-1] == pytest.approx(params.unconditional_variance, rel=0.02)


# --- Correlation Stress & Breakdown Tests ---


class TestCorrelationStress:
    def test_rolling_correlation(self):
        a = [float(i) for i in range(100)]
        b = [float(i * 2) for i in range(100)]
        rc = rolling_correlation(a, b, window=20)
        assert len(rc) == 81
        assert all(c == pytest.approx(1.0, abs=1e-5) for c in rc)

    def test_detect_correlation_breakdown(self):
        # In history, negatively correlated
        hist_a = [0.01, -0.02, 0.03, -0.01, 0.02] * 10
        hist_b = [-0.01, 0.02, -0.03, 0.01, -0.02] * 10

        # In recent crisis regime, both plunge together (contagion)
        rec_a = [-0.03, -0.04, -0.05, -0.02, -0.06] * 5
        rec_b = [-0.03, -0.04, -0.05, -0.02, -0.06] * 5

        res = detect_correlation_breakdown(("STOCK_A", "STOCK_B"), hist_a, hist_b, rec_a, rec_b)
        assert res.is_breakdown is True
        assert res.correlation_shift >= 0.50
        assert res.severity in (RiskAlertSeverity.LIMIT_BREACH, RiskAlertSeverity.ELEVATED_RISK)

    def test_stress_correlation_matrix(self):
        base_matrix = [
            [1.0, 0.2, -0.1],
            [0.2, 1.0, 0.3],
            [-0.1, 0.3, 1.0],
        ]
        stressed = stress_correlation_matrix(base_matrix, stress_factor=0.50)
        assert len(stressed) == 3
        # Diagonal remains 1.0
        assert stressed[0][0] == 1.0
        assert stressed[1][1] == 1.0
        # Off-diagonals shift closer to 1.0
        assert stressed[0][1] > base_matrix[0][1]
        assert stressed[0][2] > base_matrix[0][2]


# --- Stress Scenarios Tests (§9.4) ---


class TestStressScenarios:
    def test_all_nine_scenarios_configured(self):
        assert len(DEFAULT_ADVANCED_SCENARIOS) == 9
        for scen_type in StressScenarioType:
            assert scen_type in DEFAULT_ADVANCED_SCENARIOS

    def test_evaluate_equity_crash_scales_with_beta(self):
        low_beta = [AssetPosition("DEF", "stocks", 100_000.0, beta=0.5)]
        high_beta = [AssetPosition("GROWTH", "stocks", 100_000.0, beta=1.5)]
        spec = DEFAULT_ADVANCED_SCENARIOS[StressScenarioType.EQUITY_CRASH]

        res_low = evaluate_scenario_stress(low_beta, spec, portfolio_equity=100_000.0)
        res_high = evaluate_scenario_stress(high_beta, spec, portfolio_equity=100_000.0)

        assert res_high.stress_loss_usd > res_low.stress_loss_usd
        assert res_high.stress_loss_pct > res_low.stress_loss_pct

    def test_crypto_exchange_failure_scenario(self):
        positions = [
            AssetPosition("BTC", "crypto", 50_000.0, liquidity_score=0.9),
            AssetPosition("ALT", "crypto", 50_000.0, liquidity_score=0.2),  # illiquid
        ]
        spec = DEFAULT_ADVANCED_SCENARIOS[StressScenarioType.CRYPTO_EXCHANGE_FAILURE]
        res = evaluate_scenario_stress(positions, spec, portfolio_equity=100_000.0)
        assert res.stress_loss_usd >= 50_000.0  # crypto shock -50% plus liquidity haircut

    def test_prediction_settlement_failure_scenario(self):
        positions = [
            AssetPosition("AAPL", "stocks", 80_000.0),
            AssetPosition("ELECTION_YES", "prediction", 20_000.0),
        ]
        spec = DEFAULT_ADVANCED_SCENARIOS[StressScenarioType.PREDICTION_SETTLEMENT_FAILURE]
        res = evaluate_scenario_stress(positions, spec, portfolio_equity=100_000.0)
        # Total loss of prediction contract: 20k loss
        assert res.stress_loss_usd == pytest.approx(20_000.0, abs=1.0)


# --- AdvancedRiskEngine Assessment ---


class TestAdvancedRiskEngine:
    def test_multi_asset_portfolio_assessment(self):
        engine = AdvancedRiskEngine(max_stress_loss_pct=0.15)
        positions = [
            AssetPosition("SPY", "stocks", 40_000.0, beta=1.0),
            AssetPosition("BTC", "crypto", 20_000.0),
            AssetPosition("EURUSD", "forex", 20_000.0),
            AssetPosition("PRED", "prediction", 5_000.0),
        ]
        equity = 100_000.0
        returns = [0.01, -0.015, 0.02, -0.005, 0.01, -0.02] * 10

        assessment = engine.assess_portfolio(positions, equity, returns)
        assert isinstance(assessment, AdvancedRiskAssessment)
        assert assessment.portfolio_equity == 100_000.0
        assert assessment.current_volatility_ewma > 0
        assert assessment.current_volatility_garch > 0
        assert len(assessment.scenario_results) == 9
        assert assessment.worst_stress_loss_usd > 0
        assert assessment.overall_severity in RiskAlertSeverity
        assert len(assessment.recommended_action) > 0

    def test_extreme_stress_triggers_emergency_halt_severity(self):
        engine = AdvancedRiskEngine(max_stress_loss_pct=0.15)
        # 90% in crypto will lose >45% in crypto exchange failure
        positions = [AssetPosition("BTC", "crypto", 90_000.0)]
        assessment = engine.assess_portfolio(positions, portfolio_equity=100_000.0, portfolio_returns=[])
        assert assessment.worst_stress_loss_pct >= 0.20
        assert assessment.overall_severity == RiskAlertSeverity.EMERGENCY_HALT
        assert "emergency halt" in assessment.recommended_action.lower()
