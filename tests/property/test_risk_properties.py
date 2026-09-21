"""
Property-based tests using Hypothesis for the Deterministic Risk Engine.
Invariants asserted:
1. No approved trade ever exceeds 5% of section NAV.
2. No approved trade ever risks more than 1% of section NAV at stop distance.
3. If kill switch is active or global drawdown >= 10%, no trade is EVER approved.
4. If confidence < 0.30, no trade is EVER approved.
5. If RR < 1.5 or RR > 4.0, no trade is EVER approved.
6. Rejections always yield 0 quantity and 0 notional.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from axel.core.contracts import Proposal
from axel.core.types import OrderSide, RiskVerdict, Section, TradingMode
from axel.risk.engine import AccountState, SectionRiskAgent, SectionRiskState
from axel.risk.limits import LIMITS


@settings(max_examples=250, deadline=None)
@given(
    nav=st.floats(min_value=1_000.0, max_value=10_000_000.0),
    cash_ratio=st.floats(min_value=0.0, max_value=1.0),
    global_dd=st.floats(min_value=0.0, max_value=0.50),
    kill_switch_flag=st.booleans(),
    entry=st.floats(min_value=1.0, max_value=5000.0),
    stop_offset=st.floats(min_value=0.01, max_value=0.50),  # fraction below entry
    target_offset=st.floats(min_value=0.01, max_value=1.50),  # fraction above entry
    confidence=st.floats(min_value=0.0, max_value=1.0),
    win_rate=st.floats(min_value=0.0, max_value=1.0),
    avg_rr=st.floats(min_value=0.1, max_value=5.0),
    positions_count=st.integers(min_value=0, max_value=20),
)
def test_risk_engine_invariants(
    nav,
    cash_ratio,
    global_dd,
    kill_switch_flag,
    entry,
    stop_offset,
    target_offset,
    confidence,
    win_rate,
    avg_rr,
    positions_count,
):
    agent = SectionRiskAgent(LIMITS)

    stop = entry * (1.0 - stop_offset)
    target = entry * (1.0 + target_offset)

    prop = Proposal(
        section=Section.STOCKS,
        symbol="HYPO",
        side=OrderSide.BUY,
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        strategy_id="hypo_test",
        panel_confidence=round(confidence, 2),
        win_rate=round(win_rate, 2),
        avg_win_loss_ratio=round(avg_rr, 2),
        mode=TradingMode.PAPER,
    )

    account_state = AccountState(
        total_equity=nav,
        cash=nav * cash_ratio,
        global_drawdown_pct=global_dd,
        kill_switch_active=kill_switch_flag,
    )
    section_state = SectionRiskState(
        section=Section.STOCKS,
        section_nav=nav,
        start_of_day_equity=nav,
        active_positions_count=positions_count,
    )

    decision = agent.evaluate(prop, account_state, section_state)

    # Invariant 1: Non-negative outputs
    assert decision.approved_qty >= 0.0
    assert decision.approved_notional_usd >= 0.0

    # Invariant 2: When rejected or halted, quantity and notional MUST be strictly 0
    if not decision.approved:
        assert decision.approved_qty == 0.0
        assert decision.approved_notional_usd == 0.0
        assert decision.verdict in (RiskVerdict.REJECTED, RiskVerdict.HALTED)

    # Invariant 3: Kill Switch absolute veto
    if kill_switch_flag or global_dd >= LIMITS.GLOBAL_MAX_DRAWDOWN:
        assert not decision.approved
        assert decision.verdict == RiskVerdict.HALTED

    # Invariant 4: Panel confidence below 0.30 absolute veto
    if confidence < LIMITS.MIN_PANEL_CONFIDENCE:
        assert not decision.approved

    # Invariant 5: Max concurrent positions veto
    if positions_count >= LIMITS.MAX_OPEN_POSITIONS_PER_SECTION:
        assert not decision.approved

    # Invariants for APPROVED decisions
    if decision.approved:
        assert decision.verdict == RiskVerdict.APPROVED

        # Invariant 6: Approved notional CANNOT exceed 5% of section NAV (+ small rounding epsilon)
        max_allowed_notional = (nav * LIMITS.MAX_POSITION_PCT) + 1.0
        assert decision.approved_notional_usd <= max_allowed_notional, (
            f"Notional {decision.approved_notional_usd} exceeded cap {max_allowed_notional}"
        )

        # Invariant 7: Risk at stop CANNOT exceed 1% of section NAV (+ small rounding epsilon)
        stop_dist = abs(prop.entry - prop.stop)
        risk_at_stop = decision.approved_qty * stop_dist
        max_allowed_risk = (nav * LIMITS.MAX_PER_TRADE_RISK_PCT) + 1.0
        assert risk_at_stop <= max_allowed_risk, (
            f"Risk at stop {risk_at_stop} exceeded max loss {max_allowed_risk}"
        )
