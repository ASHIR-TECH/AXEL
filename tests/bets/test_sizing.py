"""
Unit tests for Phase 9 Bets sizing — fractional Kelly, flat stake, bankroll limits.
"""

import pytest

from axel.bets.sizing import (
    check_bankroll_limits,
    flat_stake,
    fractional_kelly,
)


def test_fractional_kelly_positive_edge():
    # Odds 2.0 (b=1), prob 0.6 -> f* = (1*0.6 - 0.4) / 1 = 0.2
    # Fractional Kelly at 25% -> f = 0.05
    result = fractional_kelly(2.0, 0.6, kelly_fraction=0.25, bankroll=1000.0)
    assert result["is_no_bet"] is False
    assert result["full_kelly_fraction"] == pytest.approx(0.2)
    assert result["kelly_fraction_applied"] == pytest.approx(0.05)
    assert result["stake"] == pytest.approx(50.0)


def test_fractional_kelly_no_edge():
    # prob 0.5 at odds 2.0 -> f* = (1*0.5 - 0.5) / 1 = 0
    result = fractional_kelly(2.0, 0.5, kelly_fraction=0.25, bankroll=1000.0)
    assert result["is_no_bet"] is True
    assert result["stake"] == 0.0
    assert result["reason"] == "non_positive_edge"


def test_fractional_kelly_negative_edge():
    result = fractional_kelly(2.0, 0.4, kelly_fraction=0.25, bankroll=1000.0)
    assert result["is_no_bet"] is True
    assert result["stake"] == 0.0


def test_fractional_kelly_caps_at_max_stake_pct():
    # High edge would suggest large Kelly, but capped at 5% of bankroll
    result = fractional_kelly(2.0, 0.9, kelly_fraction=0.25, bankroll=1000.0, max_stake_pct=0.05)
    assert result["stake"] == pytest.approx(50.0)  # 5% of 1000


def test_fractional_kelly_never_full_kelly():
    # Even with edge 1.0, full Kelly would be 1.0 (bet everything)
    # But kelly_fraction=0.25 caps it at 25% of full Kelly
    result = fractional_kelly(2.0, 1.0, kelly_fraction=0.25, bankroll=1000.0)
    assert result["kelly_fraction_applied"] < 1.0
    assert result["stake"] < 1000.0


def test_flat_stake():
    result = flat_stake(1000.0, stake_pct=0.01)
    assert result["stake"] == 10.0


def test_check_bankroll_limits_allows_small_stake():
    result = check_bankroll_limits(
        proposed_stake=10.0,
        bankroll=1000.0,
        open_exposure_pct=0.0,
        daily_stake_used_pct=0.0,
    )
    assert result["allowed"] is True


def test_check_bankroll_limits_rejects_large_stake():
    result = check_bankroll_limits(
        proposed_stake=100.0,
        bankroll=1000.0,
        max_stake_per_bet_pct=0.05,  # max 50
        open_exposure_pct=0.0,
        daily_stake_used_pct=0.0,
    )
    assert result["allowed"] is False
    assert result["reason"] == "stake_exceeds_limit"


def test_check_bankroll_limits_rejects_high_exposure():
    result = check_bankroll_limits(
        proposed_stake=10.0,
        bankroll=1000.0,
        open_exposure_pct=0.25,  # above 20% correlated exposure cap
        daily_stake_used_pct=0.0,
        max_correlated_exposure_pct=0.20,
    )
    assert result["allowed"] is False
    assert result["reason"] == "correlated_exposure_limit"
