"""
Tests for Phase 7 Crypto risk controls:
- No-leverage rule enforcement
- Rolling 24-hour UTC stop boundary
- Independent venue risk and circuit breaking
- Integration with SectionRiskAgent
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from axel.core.contracts import Proposal
from axel.core.types import OrderSide, RiskVerdict, Section
from axel.crypto.risk.no_leverage import (
    NoLeverageEnforcer,
    NoLeverageViolationError,
)
from axel.crypto.risk.rolling_utc_stop import (
    PnLEvent,
    Rolling24HourUtcStop,
)
from axel.crypto.risk.venue_risk import (
    VenueRiskLimits,
    VenueRiskManager,
)
from axel.crypto.schemas import CryptoVenueHealth
from axel.crypto.types import VenueStatus
from axel.risk.engine import AccountState, SectionRiskAgent, SectionRiskState


def test_no_leverage_enforcer():
    # 1.0 or None passes (cash spot)
    NoLeverageEnforcer.validate_leverage(1)
    NoLeverageEnforcer.validate_leverage(Decimal("1.0"))
    NoLeverageEnforcer.validate_leverage(None)

    # Any leverage > 1.0 strictly raises NoLeverageViolationError
    with pytest.raises(NoLeverageViolationError, match="NO-LEVERAGE RULE VIOLATION"):
        NoLeverageEnforcer.validate_leverage(Decimal("1.01"))

    with pytest.raises(NoLeverageViolationError, match="NO-LEVERAGE RULE VIOLATION"):
        NoLeverageEnforcer.validate_leverage(2.0)

    # Margin parameter strictly rejected
    with pytest.raises(NoLeverageViolationError, match="Margin parameter is enabled"):
        proposal = Proposal(
            section=Section.CRYPTO,
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            entry=50000.0,
            stop=49000.0,
            target=52000.0,
            strategy_id="crypto:funding_arbitrage",
            panel_confidence=0.8,
        )
        NoLeverageEnforcer.validate_proposal(proposal, extra_fields={"margin": True})


def test_rolling_24h_utc_stop(tmp_path: Path):
    state_file = tmp_path / ".test_crypto_stop.json"
    stop_monitor = Rolling24HourUtcStop(stop_threshold_pct=Decimal("0.03"), state_file=state_file)
    now = datetime.now(UTC)

    # 1. Normal P&L: -1.0% loss -> ALLOW (not halted)
    events = [
        PnLEvent(timestamp=now - timedelta(hours=2), realized_pnl=Decimal("-1000.0"), fee_paid=Decimal("50.0")),
    ]
    state = stop_monitor.evaluate(
        reference_equity=Decimal("100000.0"),
        unrealized_pnl=Decimal("0.0"),
        pnl_events=events,
        now=now,
    )
    assert not state.is_halted

    # 2. Cumulative loss exceeds 3% ($3,500 on $100,000) -> TRIPS HALT
    severe_events = [
        PnLEvent(timestamp=now - timedelta(hours=10), realized_pnl=Decimal("-3000.0"), fee_paid=Decimal("200.0")),
        PnLEvent(timestamp=now - timedelta(hours=2), realized_pnl=Decimal("-500.0"), fee_paid=Decimal("50.0")),
    ]
    state2 = stop_monitor.evaluate(
        reference_equity=Decimal("100000.0"),
        unrealized_pnl=Decimal("0.0"),
        pnl_events=severe_events,
        now=now,
    )
    assert state2.is_halted
    assert "ROLLING_24H_UTC_STOP_BREACHED" in (state2.halt_reason or "")
    assert state_file.exists()

    # 3. Events outside the 24-hour window are ignored
    old_event = PnLEvent(
        timestamp=now - timedelta(hours=25),
        realized_pnl=Decimal("-10000.0"),
    )
    fresh_stop = Rolling24HourUtcStop(stop_threshold_pct=Decimal("0.03"), state_file=tmp_path / "fresh.json")
    state3 = fresh_stop.evaluate(
        reference_equity=Decimal("100000.0"),
        unrealized_pnl=Decimal("0.0"),
        pnl_events=[old_event],
        now=now,
    )
    assert not state3.is_halted

    # 4. Fail closed on missing data
    corrupted_stop = Rolling24HourUtcStop(stop_threshold_pct=Decimal("0.03"), state_file=tmp_path / "corrupt.json")
    state_missing = corrupted_stop.evaluate(
        reference_equity=Decimal("0.0"),
        unrealized_pnl=Decimal("0.0"),
        pnl_events=None,
        now=now,
    )
    assert state_missing.is_halted

    # 5. Re-arming requires operator token and rationale
    with pytest.raises(PermissionError, match="requires an operator token"):
        stop_monitor.re_arm("", "")

    stop_monitor.re_arm("token_123", "Risk committee reviewed and reset")
    assert not stop_monitor._halted


def test_independent_venue_risk_manager():
    mgr = VenueRiskManager(VenueRiskLimits(max_latency_ms=300.0, max_stale_seconds=10.0))
    now = datetime.now(UTC)

    # Venue A healthy
    health_a = CryptoVenueHealth(
        venue="binance",
        status=VenueStatus.ACTIVE,
        latency_ms=45.0,
        maintenance_mode=False,
        last_heartbeat=now,
        stale_data_seconds=1.0,
    )
    mgr.update_health(health_a)

    allowed_a, _ = mgr.is_venue_allowed("binance")
    assert allowed_a

    # Venue B enters maintenance mode -> auto-disabled
    health_b = CryptoVenueHealth(
        venue="bybit",
        status=VenueStatus.MAINTENANCE,
        latency_ms=50.0,
        maintenance_mode=True,
        last_heartbeat=now,
        stale_data_seconds=0.5,
    )
    mgr.update_health(health_b)

    allowed_b, reason_b = mgr.is_venue_allowed("bybit")
    assert not allowed_b
    assert "maintenance mode" in reason_b.lower()

    # Venue A remains healthy and operational (independent circuit breaker)
    allowed_a_again, _ = mgr.is_venue_allowed("binance")
    assert allowed_a_again


def test_section_risk_agent_crypto_integration(tmp_path: Path):
    stop_file = tmp_path / "crypto_stop_test.json"
    rolling_stop = Rolling24HourUtcStop(stop_threshold_pct=Decimal("0.03"), state_file=stop_file)
    venue_mgr = VenueRiskManager()

    risk_agent = SectionRiskAgent(
        venue_risk_manager=venue_mgr,
        rolling_utc_stop=rolling_stop,
    )

    account_state = AccountState(total_equity=100000.0, cash=50000.0, global_drawdown_pct=0.01)
    section_state = SectionRiskState(
        section=Section.CRYPTO,
        section_nav=50000.0,
        start_of_day_equity=50000.0,
        active_positions_count=1,
    )

    prop = Proposal(
        section=Section.CRYPTO,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        entry=50000.0,
        stop=49000.0,
        target=52000.0,
        strategy_id="crypto:funding_arbitrage",
        panel_confidence=0.85,
        rationale_ref="crypto_manager:binance:2026-10-20T12:00:00Z",
    )

    # 1. Healthy conditions -> APPROVED
    decision = risk_agent.evaluate(prop, account_state, section_state)
    assert decision.approved
    assert decision.verdict == RiskVerdict.APPROVED

    # 2. Trip rolling 24h stop -> HALTED
    rolling_stop.trip_halt("Simulated 24h breach")
    decision2 = risk_agent.evaluate(prop, account_state, section_state)
    assert not decision2.approved
    assert decision2.verdict == RiskVerdict.HALTED
    assert "ROLLING_24H_UTC_STOP" in decision2.binding_limit

    # 3. Re-arm stop, but disable target venue -> REJECTED
    rolling_stop.re_arm("tok", "cleared")
    venue_mgr.disable_venue("binance", "API latency spike")
    decision3 = risk_agent.evaluate(prop, account_state, section_state)
    assert not decision3.approved
    assert decision3.verdict == RiskVerdict.REJECTED
    assert "VENUE_RISK_DISABLED" in decision3.checks_failed
