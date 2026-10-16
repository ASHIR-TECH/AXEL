"""
Unit tests for Phase 9 Bets value engine — EV and conservative EV.
"""

import pytest

from axel.bets.value import break_even_probability, conservative_ev, expected_value


def test_expected_value_positive_edge():
    # Odds 2.0, prob 0.6 -> EV = 0.6 * 1.0 - 0.4 = 0.2
    ev = expected_value(2.0, 0.6)
    assert ev == pytest.approx(0.2)


def test_expected_value_negative_edge():
    # Odds 2.0, prob 0.4 -> EV = 0.4 * 1.0 - 0.6 = -0.2
    ev = expected_value(2.0, 0.4)
    assert ev == pytest.approx(-0.2)


def test_expected_value_zero_edge():
    # Odds 2.0, prob 0.5 -> EV = 0.5 * 1.0 - 0.5 = 0
    ev = expected_value(2.0, 0.5)
    assert ev == pytest.approx(0.0)


def test_expected_value_with_commission():
    # Odds 2.0, prob 0.6, commission 5% on winnings
    # Gross win = 0.6 * 1.0 = 0.6, commission = 0.05 * 0.6 = 0.03
    # EV = 0.6 - 0.4 - 0.03 = 0.17
    ev = expected_value(2.0, 0.6, commission_rate=0.05)
    assert ev == pytest.approx(0.17)


def test_expected_value_with_execution_cost():
    # Odds 2.0, prob 0.6, execution cost 10 bps = 0.001
    ev = expected_value(2.0, 0.6, execution_cost_bps=10)
    assert ev == pytest.approx(0.2 - 0.001)


def test_conservative_ev_zero_haircut_equals_ev():
    ev = expected_value(2.0, 0.6)
    cons = conservative_ev(2.0, 0.6, 0.5, uncertainty_haircut=0.0)
    assert cons == pytest.approx(ev)


def test_conservative_ev_full_haircut_toward_market():
    # Full haircut: estimated prob becomes market-implied (0.5 at odds 2.0)
    # EV at p=0.5, odds 2.0 = 0.0 (minus commission/costs)
    cons = conservative_ev(
        2.0, 0.6, 0.5, uncertainty_haircut=1.0
    )
    assert cons == pytest.approx(0.0, abs=1e-6)


def test_break_even_probability():
    # Odds 2.0, no commission -> break-even p = 0.5
    p = break_even_probability(2.0)
    assert p == pytest.approx(0.5)

    # Odds 4.0 -> break-even p = 0.25
    p = break_even_probability(4.0)
    assert p == pytest.approx(0.25)

    # With commission, break-even probability is higher
    p_no_comm = break_even_probability(2.0)
    p_with_comm = break_even_probability(2.0, commission_rate=0.05)
    assert p_with_comm > p_no_comm


def test_value_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        expected_value(1.0, 0.5)  # odds must be > 1
    with pytest.raises(ValueError):
        expected_value(2.0, 1.5)  # prob must be in [0,1]
    with pytest.raises(ValueError):
        conservative_ev(2.0, 0.5, 0.5, uncertainty_haircut=1.5)  # haircut > 1
