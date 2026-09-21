from axel.core.contracts import AllocationProposal
from axel.core.types import Section
from axel.risk.allocator import CapitalAllocator
from axel.risk.limits import LIMITS


def test_allocator_valid_proposal():
    alloc = CapitalAllocator(LIMITS)
    prop = AllocationProposal(
        from_section=Section.STOCKS,
        to_section=Section.CRYPTO,
        pct_of_total=0.03,  # 3% <= 5% cap
    )
    decision = alloc.evaluate_proposal(
        proposal=prop,
        total_portfolio_nav=100_000.0,
        from_section_capital=40_000.0,
    )
    assert decision.approved
    assert decision.approved_pct == 0.03
    assert decision.approved_amount_usd == 3_000.0


def test_allocator_exceeds_cycle_cap():
    alloc = CapitalAllocator(LIMITS)
    prop = AllocationProposal(
        from_section=Section.STOCKS,
        to_section=Section.CRYPTO,
        pct_of_total=0.08,  # 8% > 5% cap
    )
    decision = alloc.evaluate_proposal(
        proposal=prop,
        total_portfolio_nav=100_000.0,
        from_section_capital=40_000.0,
    )
    assert not decision.approved
    assert "EXCEEDS CYCLE CAP" in (decision.reason or "")


def test_allocator_violates_source_reserve():
    alloc = CapitalAllocator(LIMITS)
    prop = AllocationProposal(
        from_section=Section.STOCKS,
        to_section=Section.CRYPTO,
        pct_of_total=0.04,  # $4,000 shift
    )
    # If stocks only has $12,000, leaving it at $8,000 (< 10% = $10,000 reserve)
    decision = alloc.evaluate_proposal(
        proposal=prop,
        total_portfolio_nav=100_000.0,
        from_section_capital=12_000.0,
    )
    assert not decision.approved
    assert "SOURCE RESERVE BREACH" in (decision.reason or "")
