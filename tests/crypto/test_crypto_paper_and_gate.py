"""
Tests for Phase 7 Crypto Paper Trading Engine, Promotion Gate, and Observability.
"""

from datetime import UTC, datetime
from decimal import Decimal

from axel.core.contracts import RiskDecision
from axel.core.types import RiskVerdict
from axel.crypto.adapters.mock_exchange import MockCryptoExchange
from axel.crypto.observability.metrics import CryptoMetricsCollector
from axel.crypto.paper.paper_engine import CryptoPaperEngine
from axel.crypto.paper.promotion_gate import (
    CryptoPromotionEvidence,
    CryptoPromotionGate,
)
from axel.crypto.schemas import ArbitrageSpreadOpportunity


def test_crypto_paper_trading_engine():
    exchange_a = MockCryptoExchange(venue_name="binance_mock")
    exchange_b = MockCryptoExchange(venue_name="bybit_mock")

    engine = CryptoPaperEngine(adapters={"binance_mock": exchange_a, "bybit_mock": exchange_b})

    now = datetime.now(UTC)
    opp = ArbitrageSpreadOpportunity(
        symbol="BTC/USDT",
        venue_buy="binance_mock",
        venue_sell="bybit_mock",
        price_buy=Decimal("50000.0"),
        price_sell=Decimal("50200.0"),
        timestamp_buy=now,
        timestamp_sell=now,
        raw_spread_bps=Decimal("40.0"),
        net_edge_bps=Decimal("20.0"),
        available_liquidity_usd=Decimal("50000.0"),
        estimated_fees_bps=Decimal("8.0"),
        estimated_slippage_bps=Decimal("4.0"),
        is_profitable=True,
    )

    risk_decision = RiskDecision(
        proposal_id="prop_test_1",
        approved=True,
        verdict=RiskVerdict.APPROVED,
        approved_qty=0.5,
        approved_notional_usd=25000.0,
    )

    # 1. Successful paper execution (both legs fill)
    res = engine.execute_paper_arbitrage(opp, qty=Decimal("0.5"), risk_decision=risk_decision)
    assert res.is_fully_hedged
    assert res.net_pnl_usd > Decimal("0.0")
    assert engine.get_completion_rate() == 1.0

    # 2. Injected leg failure on exchange B
    exchange_b.fail_next_order = True
    res_fail = engine.execute_paper_arbitrage(opp, qty=Decimal("0.5"), risk_decision=risk_decision)
    assert not res_fail.is_fully_hedged
    assert engine.state.one_leg_failures == 1
    assert engine.get_completion_rate() == 0.5


def test_crypto_promotion_gate():
    # 1. Passing evidence bundle
    passing_evidence = CryptoPromotionEvidence(
        strategy_id="crypto:funding_arbitrage",
        venue="binance",
        oos_backtest_passed=True,
        oos_sharpe=1.25,
        trade_count=150,
        parameter_robustness_passed=True,
        no_leakage_audit_passed=True,
        paper_trade_count=80,
        paper_net_edge_positive=True,
        arbitrage_leg_completion_rate=0.98,
        unresolved_reconciliation_breaks=0,
        venue_uptime_pct=0.999,
        venue_avg_latency_ms=45.0,
        no_leverage_verified=True,
        rolling_24h_utc_stop_passed=True,
        killswitch_watchdog_passed=True,
        human_signoff_recorded=True,
        human_reviewer="lead_quant_operator",
        signoff_notes="Production verification complete",
    )

    decision = CryptoPromotionGate.evaluate(passing_evidence)
    assert decision.promoted
    assert len(decision.checks_failed) == 0

    # 2. Missing human review sign-off fails closed
    unapproved_evidence = CryptoPromotionEvidence(
        strategy_id="crypto:funding_arbitrage",
        venue="binance",
        oos_backtest_passed=True,
        oos_sharpe=1.25,
        trade_count=150,
        parameter_robustness_passed=True,
        no_leakage_audit_passed=True,
        paper_trade_count=80,
        paper_net_edge_positive=True,
        arbitrage_leg_completion_rate=0.98,
        unresolved_reconciliation_breaks=0,
        venue_uptime_pct=0.999,
        venue_avg_latency_ms=45.0,
        no_leverage_verified=True,
        rolling_24h_utc_stop_passed=True,
        killswitch_watchdog_passed=True,
        human_signoff_recorded=False,  # NOT signed off!
    )
    decision2 = CryptoPromotionGate.evaluate(unapproved_evidence)
    assert not decision2.promoted
    assert "HUMAN_SIGNOFF_MISSING" in decision2.checks_failed

    # 3. Leg completion rate below 95% fails promotion
    leaky_evidence = CryptoPromotionEvidence(
        strategy_id="crypto:cross_exchange_arbitrage",
        venue="bybit",
        oos_backtest_passed=True,
        oos_sharpe=1.1,
        trade_count=120,
        parameter_robustness_passed=True,
        no_leakage_audit_passed=True,
        paper_trade_count=60,
        paper_net_edge_positive=True,
        arbitrage_leg_completion_rate=0.91,  # 91% < 95%
        unresolved_reconciliation_breaks=0,
        venue_uptime_pct=0.995,
        venue_avg_latency_ms=50.0,
        no_leverage_verified=True,
        rolling_24h_utc_stop_passed=True,
        killswitch_watchdog_passed=True,
        human_signoff_recorded=True,
        human_reviewer="lead_quant_operator",
    )
    decision3 = CryptoPromotionGate.evaluate(leaky_evidence)
    assert not decision3.promoted
    assert "LEG_COMPLETION_FAILED" in decision3.checks_failed


def test_crypto_observability_snapshot():
    collector = CryptoMetricsCollector()
    collector.record_opportunity(is_accepted=True)
    collector.record_opportunity(is_accepted=False, rejection_reason="Insufficient liquidity")
    collector.record_paper_trade(net_pnl=Decimal("150.0"), costs=Decimal("20.0"), leg_completed=True)

    snap = collector.snapshot(rolling_24h_loss_pct=-0.005, rolling_stop_halted=False, section_exposure_usd=25000.0)
    assert snap.spread_opportunities_detected_24h == 2
    assert snap.spread_opportunities_accepted_24h == 1
    assert snap.rejected_reasons.get("Insufficient liquidity") == 1
    assert snap.hypothetical_net_pnl_usd == 150.0
    assert snap.arbitrage_leg_completion_rate == 1.0

    d = snap.to_dict()
    assert "timestamp" in d
    assert d["rolling_24h_stop_halted"] is False
