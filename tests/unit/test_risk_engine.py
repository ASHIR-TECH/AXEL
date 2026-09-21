from axel.core.contracts import Proposal
from axel.core.types import OrderSide, RiskVerdict, Section
from axel.risk.engine import (
    AccountState,
    OpenPositionInfo,
    SectionRiskAgent,
    SectionRiskState,
)
from axel.risk.limits import LIMITS


def test_risk_engine_approved_flow():
    agent = SectionRiskAgent(LIMITS)
    prop = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.BUY,
        entry=150.0,
        stop=145.0,
        target=162.5,  # 2.5:1 RR
        strategy_id="strat_v1",
        panel_confidence=0.85,
        win_rate=0.55,
        avg_win_loss_ratio=1.8,
    )
    acct = AccountState(100_000.0, 50_000.0, 0.02, False)
    sec = SectionRiskState(Section.STOCKS, 100_000.0, 100_000.0, 100.0, 0.0, 1)

    dec = agent.evaluate(prop, acct, sec)
    assert dec.approved
    assert dec.verdict == RiskVerdict.APPROVED
    assert dec.approved_qty > 0


def test_risk_engine_rr_too_low():
    agent = SectionRiskAgent(LIMITS)
    prop = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.BUY,
        entry=100.0,
        stop=90.0,   # Risk 10
        target=110.0,  # Reward 10 -> 1.0 RR < 1.5 min
        strategy_id="strat_v1",
        panel_confidence=0.85,
    )
    acct = AccountState(100_000.0, 50_000.0, 0.02, False)
    sec = SectionRiskState(Section.STOCKS, 100_000.0, 100_000.0)

    dec = agent.evaluate(prop, acct, sec)
    assert not dec.approved
    assert "RR_RATIO_TOO_LOW" in dec.checks_failed


def test_risk_engine_overfit_rejection():
    agent = SectionRiskAgent(LIMITS)
    prop = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.BUY,
        entry=100.0,
        stop=98.0,   # Risk 2
        target=120.0,  # Reward 20 -> 10.0 RR > 4.0 max
        strategy_id="strat_v1",
        panel_confidence=0.85,
    )
    acct = AccountState(100_000.0, 50_000.0, 0.02, False)
    sec = SectionRiskState(Section.STOCKS, 100_000.0, 100_000.0)

    dec = agent.evaluate(prop, acct, sec)
    assert not dec.approved
    assert "R_TARGET_OVERFITTING_FLAG" in dec.checks_failed


def test_risk_engine_correlation_block():
    agent = SectionRiskAgent(LIMITS)
    prop = Proposal(
        section=Section.STOCKS,
        symbol="MSFT",
        side=OrderSide.BUY,
        entry=400.0,
        stop=390.0,
        target=425.0,
        strategy_id="strat_v1",
        panel_confidence=0.85,
    )
    acct = AccountState(100_000.0, 50_000.0, 0.02, False)
    sec = SectionRiskState(Section.STOCKS, 100_000.0, 100_000.0)
    open_pos = [
        OpenPositionInfo(
            symbol="AAPL",
            section=Section.STOCKS,
            side=OrderSide.BUY,
            notional_usd=5_000.0,
            correlation_with_proposal=0.92,  # > 0.85 threshold!
        )
    ]

    dec = agent.evaluate(prop, acct, sec, open_positions=open_pos)
    assert not dec.approved
    assert "CORRELATION_LIMIT_BREACH" in dec.checks_failed
