"""
Phase 7 End-to-End Acceptance Test (PRD Acceptance Criteria).

Verifies the complete Phase 7 acceptance sequence:
1. Load fixed multi-venue market/funding/on-chain fixtures.
2. A funding spread is accepted only when synchronized, cost-adjusted edge is positive.
3. A deliberately unprofitable spread is rejected.
4. A one-leg failure enters the defined incident path.
5. Any leverage request is rejected.
6. The rolling 24-hour stop halts new Crypto risk when breached.
7. Paper orders reconcile correctly without capital movement.
8. No agent process can submit an exchange order directly.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from axel.core.contracts import Order, Proposal, RiskDecision
from axel.core.types import OrderSide, RiskVerdict, Section
from axel.crypto.adapters.mock_exchange import MockCryptoExchange
from axel.crypto.analysts.funding import FundingAnalyst
from axel.crypto.paper.paper_engine import CryptoPaperEngine
from axel.crypto.risk.no_leverage import NoLeverageEnforcer, NoLeverageViolationError
from axel.crypto.risk.rolling_utc_stop import PnLEvent, Rolling24HourUtcStop
from axel.crypto.schemas import (
    FundingRateRecord,
)
from axel.crypto.strategies.cross_exchange_arbitrage import (
    ArbitrageLegExecution,
    OneLegFailureHandler,
)
from axel.crypto.strategies.funding_arbitrage import (
    FundingArbCostModel,
    FundingArbitrageStrategy,
)
from axel.crypto.types import ArbitrageLegStatus, OneLegFailureAction
from axel.data.schemas import Provenance
from axel.risk.engine import AccountState, SectionRiskAgent, SectionRiskState


def test_phase_7_acceptance_test(tmp_path: Path):
    now = datetime.now(UTC)
    prov = Provenance(source="fixture", source_id="acceptance", source_hash="acc_hash", ingestion_timestamp=now)

    # ── 1. Load fixed multi-venue fixtures ──────────────────────────────────
    exchange_a = MockCryptoExchange(venue_name="venue_alpha")
    exchange_b = MockCryptoExchange(venue_name="venue_beta")

    f_alpha_profitable = FundingRateRecord(
        venue="venue_alpha",
        symbol="BTC/USDT:USDT",
        event_time=now,
        available_at=now,
        rate=Decimal("0.0001"),  # 1 bps
        mark_price=Decimal("50000.0"),
        provenance=prov,
    )
    f_beta_profitable = FundingRateRecord(
        venue="venue_beta",
        symbol="BTC/USDT:USDT",
        event_time=now,
        available_at=now,
        rate=Decimal("0.0008"),  # 8 bps -> diff = 7 bps (21 bps/day)
        mark_price=Decimal("50020.0"),
        provenance=prov,
    )

    # ── 2. Funding spread accepted ONLY when cost-adjusted edge is positive ──
    cost_model = FundingArbCostModel(
        taker_fee_bps=Decimal("2.0"),
        half_spread_bps=Decimal("1.0"),
        slippage_bps=Decimal("1.0"),
        settlement_cost_bps=Decimal("0.5"),
        venue_risk_haircut_bps=Decimal("1.0"),
    )
    # Total round trip cost = 17.5 bps. Expected funding = 21 bps. Net edge = +3.5 bps.
    funding_strat = FundingArbitrageStrategy(cost_model=cost_model)

    opp_profitable = funding_strat.evaluate_spread(
        f_alpha_profitable,
        f_beta_profitable,
        available_liquidity_usd=Decimal("50000.0"),
    )
    assert opp_profitable.is_profitable
    assert opp_profitable.net_edge_bps > Decimal("0.0")

    # ── 3. A deliberately unprofitable spread is rejected ───────────────────
    f_beta_unprofitable = FundingRateRecord(
        venue="venue_beta",
        symbol="BTC/USDT:USDT",
        event_time=now,
        available_at=now,
        rate=Decimal("0.0002"),  # 2 bps -> diff = 1 bps (3 bps/day) < 17.5 bps cost
        provenance=prov,
    )
    opp_unprofitable = funding_strat.evaluate_spread(
        f_alpha_profitable,
        f_beta_unprofitable,
        available_liquidity_usd=Decimal("50000.0"),
    )
    assert not opp_unprofitable.is_profitable
    assert "Net edge" in (opp_unprofitable.rejection_reason or "")

    # ── 4. One-leg failure enters defined incident path ─────────────────────
    leg_a = ArbitrageLegExecution(
        venue="venue_alpha",
        symbol="BTC/USDT",
        side="buy",
        requested_qty=Decimal("1.0"),
        filled_qty=Decimal("1.0"),
        price=Decimal("50000.0"),
        status=ArbitrageLegStatus.FILLED,
    )
    leg_b_failed = ArbitrageLegExecution(
        venue="venue_beta",
        symbol="BTC/USDT",
        side="sell",
        requested_qty=Decimal("1.0"),
        filled_qty=Decimal("0.0"),
        price=Decimal("50200.0"),
        status=ArbitrageLegStatus.FAILED,
        error="Exchange connection reset",
    )
    incident = OneLegFailureHandler.handle_failure(leg_a, leg_b_failed, now=now)
    assert incident.incident_severity == "CRITICAL"
    assert incident.unhedged_qty == Decimal("1.0")
    assert incident.recommended_action == OneLegFailureAction.MARKET_HEDGE

    # ── 5. Any leverage request is rejected ─────────────────────────────────
    with pytest.raises(NoLeverageViolationError, match="NO-LEVERAGE RULE VIOLATION"):
        NoLeverageEnforcer.validate_leverage(Decimal("2.0"))

    order = Order(
        client_order_id="test_lev_ord",
        proposal_id="p1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        qty=1.0,
    )
    with pytest.raises(NoLeverageViolationError):
        NoLeverageEnforcer.validate_order(order, leverage=Decimal("1.5"))

    # Adapter itself blocks leverage
    risk_dec = RiskDecision(
        proposal_id="p1",
        approved=True,
        verdict=RiskVerdict.APPROVED,
        approved_qty=1.0,
        approved_notional_usd=50000.0,
    )
    with pytest.raises(ValueError, match="NO-LEVERAGE RULE VIOLATION"):
        exchange_a.submit_order(order, risk_dec, leverage=2)

    # ── 6. Rolling 24-hour stop halts new Crypto risk when breached ──────────
    stop_file = tmp_path / "acceptance_stop.json"
    rolling_stop = Rolling24HourUtcStop(stop_threshold_pct=Decimal("0.03"), state_file=stop_file)
    severe_losses = [
        PnLEvent(timestamp=now - timedelta(hours=5), realized_pnl=Decimal("-4000.0"), fee_paid=Decimal("100.0")),
    ]
    stop_state = rolling_stop.evaluate(
        reference_equity=Decimal("100000.0"),
        unrealized_pnl=Decimal("0.0"),
        pnl_events=severe_losses,
        now=now,
    )
    assert stop_state.is_halted

    # SectionRiskAgent evaluates with halted rolling stop -> HALTED
    risk_agent = SectionRiskAgent(rolling_utc_stop=rolling_stop)
    crypto_prop = Proposal(
        section=Section.CRYPTO,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        entry=50000.0,
        stop=49000.0,
        target=52000.0,
        strategy_id="crypto:funding_arbitrage",
        panel_confidence=0.85,
    )
    account_state = AccountState(total_equity=100000.0, cash=50000.0, global_drawdown_pct=0.01)
    section_state = SectionRiskState(section=Section.CRYPTO, section_nav=50000.0, start_of_day_equity=50000.0)

    decision = risk_agent.evaluate(crypto_prop, account_state, section_state)
    assert not decision.approved
    assert decision.verdict == RiskVerdict.HALTED
    assert "ROLLING_24H_UTC_STOP" in decision.binding_limit

    # ── 7. Paper orders reconcile correctly without capital movement ─────────
    paper_engine = CryptoPaperEngine(adapters={"venue_alpha": exchange_a, "venue_beta": exchange_b})
    res = paper_engine.execute_paper_arbitrage(
        opp_profitable,
        qty=Decimal("0.5"),
        risk_decision=risk_dec,
    )
    assert res.is_fully_hedged
    assert res.net_pnl_usd > Decimal("0.0")

    # ── 8. No agent process can submit an exchange order ─────────────────────
    analyst = FundingAnalyst()
    sig = analyst.analyze(f_alpha_profitable, as_of=now)
    # The analyst only has access to analysis logic and returns a Signal contract.
    # It has no adapter, no broker credentials, and no execution methods.
    assert isinstance(sig.strength, float)
    assert not hasattr(analyst, "submit_order")
    assert not hasattr(analyst, "api_key")
