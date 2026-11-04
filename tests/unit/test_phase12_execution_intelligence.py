"""Unit tests for Phase 12 Execution Intelligence and Almgren-Chriss Scheduling."""

from __future__ import annotations

import pytest

from axel.execution.intelligence import (
    ExecutionSchedule,
    ImplementationShortfallBreakdown,
    ScheduleComparisonReport,
    ScheduleType,
    calculate_implementation_shortfall,
    compare_execution_policies,
    estimate_square_root_impact,
    generate_almgren_chriss_schedule,
    generate_twap_schedule,
    generate_vwap_schedule,
)

# --- Implementation Shortfall Tests (§8.2) ---


class TestImplementationShortfall:
    def test_buy_shortfall_calculation(self):
        # Buy 100 shares: decision $100, arrival $101, execution $102, final $104
        # 80 filled, 20 unfilled, $5 fees
        # price impact: +1 * 80 * (102 - 101) = 80
        # delay cost: +1 * 80 * (101 - 100) = 80
        # opportunity cost: +1 * 20 * (104 - 100) = 80
        # total: 80 + 80 + 80 + 5 = 245
        res = calculate_implementation_shortfall(
            decision_price=100.0,
            arrival_price=101.0,
            execution_price=102.0,
            final_price=104.0,
            requested_quantity=100.0,
            filled_quantity=80.0,
            side="buy",
            fees=5.0,
        )
        assert isinstance(res, ImplementationShortfallBreakdown)
        assert res.price_impact_shortfall_usd == 80.0
        assert res.delay_cost_usd == 80.0
        assert res.opportunity_cost_usd == 80.0
        assert res.total_implementation_shortfall_usd == 245.0
        # initial notional = 100 * 100 = 10,000 -> 245 / 10,000 = 2.45% = 245 bps
        assert res.shortfall_bps == pytest.approx(245.0, abs=1e-3)

    def test_sell_shortfall_sign_convention(self):
        # Sell 50 shares: decision $100, arrival $99, execution $98, final $96
        # side_sign = -1
        # price impact: -1 * 50 * (98 - 99) = +50 (execution worse than arrival)
        # delay cost: -1 * 50 * (99 - 100) = +50 (arrival worse than decision)
        # opportunity cost: 0 (fully filled)
        res = calculate_implementation_shortfall(
            decision_price=100.0,
            arrival_price=99.0,
            execution_price=98.0,
            final_price=96.0,
            requested_quantity=50.0,
            filled_quantity=50.0,
            side="sell",
            fees=2.0,
        )
        assert res.price_impact_shortfall_usd == 50.0
        assert res.delay_cost_usd == 50.0
        assert res.opportunity_cost_usd == 0.0
        assert res.total_implementation_shortfall_usd == 102.0


# --- Square-Root Impact Model (§8.3) ---


class TestSquareRootImpact:
    def test_zero_parameters_return_zero(self):
        assert estimate_square_root_impact(0, 1000, 0.02) == 0.0
        assert estimate_square_root_impact(100, 0, 0.02) == 0.0
        assert estimate_square_root_impact(100, 1000, 0.0) == 0.0

    def test_square_root_scaling(self):
        # 4x volume participation should double impact
        impact_1 = estimate_square_root_impact(100.0, 10_000.0, 0.02, eta=0.2)
        impact_4 = estimate_square_root_impact(400.0, 10_000.0, 0.02, eta=0.2)
        assert impact_4 == pytest.approx(impact_1 * 2.0, rel=1e-4)


# --- Almgren-Chriss Optimal Schedule (§5.3, §8.4) ---


class TestAlmgrenChrissSchedule:
    def test_schedule_liquidates_entire_order(self):
        total_shares = 10_000.0
        intervals = 10
        sched = generate_almgren_chriss_schedule(
            total_quantity=total_shares,
            num_intervals=intervals,
            initial_price=100.0,
            volatility=0.015,
            daily_volume=1_000_000.0,
            risk_aversion_lambda=1e-6,
        )
        assert isinstance(sched, ExecutionSchedule)
        assert sched.schedule_type == ScheduleType.ALMGREN_CHRISS
        assert len(sched.steps) == intervals

        # Sum of shares to trade must equal initial total
        total_traded = sum(s.shares_to_trade for s in sched.steps)
        assert total_traded == pytest.approx(total_shares, rel=1e-3)

        # Remaining shares at end must be 0
        assert sched.steps[-1].shares_remaining == 0.0

    def test_higher_risk_aversion_trades_faster(self):
        total_shares = 10_000.0
        intervals = 10
        # Low risk aversion (trades more passively to reduce impact)
        sched_patient = generate_almgren_chriss_schedule(
            total_quantity=total_shares,
            num_intervals=intervals,
            initial_price=100.0,
            volatility=0.02,
            daily_volume=1_000_000.0,
            risk_aversion_lambda=1e-8,
        )
        # High risk aversion (trades urgently to eliminate variance)
        sched_urgent = generate_almgren_chriss_schedule(
            total_quantity=total_shares,
            num_intervals=intervals,
            initial_price=100.0,
            volatility=0.02,
            daily_volume=1_000_000.0,
            risk_aversion_lambda=1e-4,
        )

        # Urgent trader executes more in interval 1 than patient trader
        assert sched_urgent.steps[0].shares_to_trade > sched_patient.steps[0].shares_to_trade
        # Half life is shorter for urgent trader
        assert sched_urgent.half_life < sched_patient.half_life


# --- TWAP & VWAP Schedules ---


class TestTWAPandVWAP:
    def test_twap_uniform_distribution(self):
        total_shares = 5_000.0
        intervals = 5
        twap = generate_twap_schedule(
            total_quantity=total_shares,
            num_intervals=intervals,
            initial_price=50.0,
            volatility=0.01,
            daily_volume=500_000.0,
        )
        assert twap.schedule_type == ScheduleType.TWAP
        # Every step has exactly 1,000 shares
        for s in twap.steps:
            assert s.shares_to_trade == pytest.approx(1000.0, abs=1e-3)

    def test_vwap_tracks_volume_profile(self):
        profile = [10.0, 20.0, 30.0, 40.0]  # total = 100
        total_shares = 10_000.0
        vwap = generate_vwap_schedule(
            total_quantity=total_shares,
            volume_profile=profile,
            initial_price=100.0,
            volatility=0.02,
            daily_volume=1_000_000.0,
        )
        assert vwap.schedule_type == ScheduleType.VWAP
        assert vwap.steps[0].shares_to_trade == pytest.approx(1000.0, abs=1e-3)
        assert vwap.steps[1].shares_to_trade == pytest.approx(2000.0, abs=1e-3)
        assert vwap.steps[2].shares_to_trade == pytest.approx(3000.0, abs=1e-3)
        assert vwap.steps[3].shares_to_trade == pytest.approx(4000.0, abs=1e-3)


# --- Policy Comparison Tests (§8.5) ---


class TestPolicyComparison:
    def test_compare_execution_policies_returns_report(self):
        report = compare_execution_policies(
            symbol="NVDA",
            total_quantity=10_000.0,
            num_intervals=6,
            initial_price=120.0,
            volatility=0.025,
            daily_volume=20_000_000.0,
            volume_profile=[0.3, 0.1, 0.1, 0.1, 0.1, 0.3],  # U-shaped intraday curve
            risk_aversion_lambda=1e-5,
        )
        assert isinstance(report, ScheduleComparisonReport)
        assert report.symbol == "NVDA"
        assert len(report.schedules) == 3
        assert report.recommended_policy in ScheduleType
        assert len(report.rationale) > 0
