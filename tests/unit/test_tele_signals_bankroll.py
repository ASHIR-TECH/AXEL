"""Tests for the daily-allocation bankroll (adapted from SportyClaw semantics)."""

from axel.signals.bankroll import AllocationBankroll, compute_daily_allocation


class TestAllocationMath:
    def test_zero_balance_yields_zero_allocation(self):
        result = compute_daily_allocation(0)
        assert result.allocation == 0.0
        assert result.reserve == 0.0

    def test_capped_at_balance(self):
        result = compute_daily_allocation(100_000)
        assert 0 < result.allocation <= 100_000
        assert result.reserve >= 0

    def test_monotonic_with_balance(self):
        small = compute_daily_allocation(50_000).allocation
        large = compute_daily_allocation(500_000).allocation
        assert large > small


class TestBankroll:
    def test_initialize_sets_allocation_and_bets(self):
        bankroll = AllocationBankroll()
        bankroll.initialize(100_000, max_bets_per_day=10)
        assert bankroll.allocation_total > 0
        assert bankroll.bets_remaining == 10
        assert bankroll.has_available_allocation()

    def test_reserve_stake_depletes_gradually(self):
        bankroll = AllocationBankroll()
        bankroll.initialize(50_000, max_bets_per_day=4)
        stakes = [bankroll.reserve_stake() for _ in range(4)]
        assert all(stake is not None for stake in stakes)
        assert not bankroll.has_available_allocation()
        assert bankroll.reserve_stake() is None

    def test_release_stake_returns_to_pool(self):
        bankroll = AllocationBankroll()
        bankroll.initialize(50_000, max_bets_per_day=2)
        stake = bankroll.reserve_stake()
        assert stake is not None and stake > 0
        before = bankroll.remaining()
        bankroll.release_stake(stake)
        assert bankroll.remaining() == before + stake

    def test_remaining_tracks_usage(self):
        bankroll = AllocationBankroll()
        bankroll.initialize(50_000, max_bets_per_day=5)
        total = bankroll.allocation_total
        bankroll.reserve_stake()
        assert bankroll.remaining() < total