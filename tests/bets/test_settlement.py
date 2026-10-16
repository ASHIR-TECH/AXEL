"""
Unit tests for Phase 9 Bets settlement — payout computation.
"""

import pytest

from axel.bets.settlement import compute_payout, settle_multiple


def test_win_payout():
    result = compute_payout(stake=100.0, decimal_odds=2.0, outcome="win")
    assert result.outcome == "win"
    assert result.profit == pytest.approx(100.0)  # 100 * (2-1) = 100 profit
    assert result.payout == pytest.approx(200.0)  # stake + profit
    assert result.commission == 0.0


def test_win_payout_with_commission():
    result = compute_payout(
        stake=100.0, decimal_odds=2.0, outcome="win", commission_rate=0.05
    )
    # Gross profit = 100, commission = 5% of 100 = 5, net = 95
    assert result.profit == pytest.approx(95.0)
    assert result.payout == pytest.approx(195.0)
    assert result.commission == pytest.approx(5.0)


def test_loss_payout():
    result = compute_payout(stake=100.0, decimal_odds=2.0, outcome="loss")
    assert result.outcome == "loss"
    assert result.profit == pytest.approx(-100.0)
    assert result.payout == pytest.approx(0.0)


def test_push_payout():
    result = compute_payout(stake=100.0, decimal_odds=2.0, outcome="push")
    assert result.outcome == "push"
    assert result.profit == pytest.approx(0.0)
    assert result.payout == pytest.approx(100.0)  # stake returned


def test_void_payout():
    result = compute_payout(stake=100.0, decimal_odds=2.0, outcome="void")
    assert result.outcome == "void"
    assert result.profit == pytest.approx(0.0)
    assert result.payout == pytest.approx(100.0)


def test_dead_heat_payout():
    # 2-way dead heat: odds 2.0 -> effective odds 1 + (2-1)/2 = 1.5
    # profit = 100 * 0.5 = 50, payout = 150
    result = compute_payout(stake=100.0, decimal_odds=2.0, outcome="dead_heat", dead_heat_divisor=2)
    assert result.outcome == "dead_heat"
    assert result.profit == pytest.approx(50.0)
    assert result.payout == pytest.approx(150.0)


def test_settle_multiple():
    bets = [
        (100.0, 2.0, "win"),
        (100.0, 2.0, "loss"),
        (100.0, 2.0, "push"),
    ]
    results = settle_multiple(bets)
    assert len(results) == 3
    assert results[0].profit == pytest.approx(100.0)
    assert results[1].profit == pytest.approx(-100.0)
    assert results[2].profit == pytest.approx(0.0)


def test_settlement_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        compute_payout(stake=0, decimal_odds=2.0, outcome="win")
    with pytest.raises(ValueError):
        compute_payout(stake=100, decimal_odds=1.0, outcome="win")
    with pytest.raises(ValueError):
        compute_payout(stake=100, decimal_odds=2.0, outcome="invalid")
