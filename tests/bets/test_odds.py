"""
Unit tests for Phase 9 Bets odds normalization and de-vig transforms.
"""

import pytest

from axel.bets.odds import (
    american_to_decimal,
    closing_line_value,
    closing_line_value_bps,
    decimal_to_american,
    decimal_to_fractional,
    fractional_to_decimal,
    implied_probability,
    implied_probability_from_odds,
    overround,
    proportional_normalization,
    shin_probabilities,
)


def test_implied_probability():
    assert implied_probability(2.0) == 0.5
    assert implied_probability(4.0) == 0.25
    assert implied_probability(1.5) == pytest.approx(1.0 / 1.5)


def test_implied_probability_rejects_nonpositive():
    with pytest.raises(ValueError):
        implied_probability(0.0)
    with pytest.raises(ValueError):
        implied_probability(-1.0)


def test_overround():
    # Fair book: probs sum to 1.0 -> overround 0
    assert overround([0.5, 0.5]) == 0.0
    # Overround: probs sum to 1.05 -> overround 0.05
    assert overround([0.55, 0.5]) == pytest.approx(0.05)


def test_proportional_normalization():
    # Odds 2.0 and 2.0 -> raw probs 0.5, 0.5 -> sum 1.0 -> normalized [0.5, 0.5]
    result = proportional_normalization([2.0, 2.0])
    assert result == pytest.approx([0.5, 0.5])

    # Odds 2.0 and 4.0 -> raw probs 0.5, 0.25 -> sum 0.75 -> [0.667, 0.333]
    result = proportional_normalization([2.0, 4.0])
    assert result == pytest.approx([0.5 / 0.75, 0.25 / 0.75])


def test_shin_probabilities_sum_to_one():
    result = shin_probabilities([2.0, 4.0])
    assert abs(sum(result) - 1.0) < 1e-9
    assert all(0.0 < p < 1.0 for p in result)


def test_shin_two_outcome_fair_book():
    # Fair book with no overround -> shin should be close to proportional
    result = shin_probabilities([2.0, 2.0])
    assert result == pytest.approx([0.5, 0.5], abs=1e-6)


def test_shin_single_outcome():
    result = shin_probabilities([2.0])
    assert result == pytest.approx([0.5])


def test_american_to_decimal():
    assert american_to_decimal(150.0) == 2.5  # +150 -> 2.50
    assert american_to_decimal(-200.0) == 1.5  # -200 -> 1.50


def test_fractional_to_decimal():
    assert fractional_to_decimal(3, 2) == 2.5  # 3/2 -> 2.50
    assert fractional_to_decimal(1, 1) == 2.0  # 1/1 -> 2.00


def test_decimal_to_american():
    assert decimal_to_american(2.5) == pytest.approx(150.0)
    assert decimal_to_american(1.5) == pytest.approx(-200.0)
    assert decimal_to_american(2.0) == pytest.approx(100.0)
    with pytest.raises(ValueError):
        decimal_to_american(1.0)


def test_american_decimal_roundtrip():
    for american in (-250.0, -110.0, 100.0, 145.0, 900.0):
        assert american_to_decimal(decimal_to_american(american_to_decimal(american))) == pytest.approx(
            american_to_decimal(american)
        )


def test_decimal_to_fractional():
    assert decimal_to_fractional(2.5) == (3, 2)
    assert decimal_to_fractional(1.5) == (1, 2)
    assert decimal_to_fractional(2.0) == (1, 1)
    assert decimal_to_fractional(2.75) == (7, 4)
    with pytest.raises(ValueError):
        decimal_to_fractional(1.0)


def test_fractional_decimal_roundtrip():
    for numerator, denominator in ((1, 2), (3, 2), (7, 4), (1, 1)):
        decimal = fractional_to_decimal(numerator, denominator)
        assert decimal_to_fractional(decimal) == (numerator, denominator)


def test_implied_probability_from_odds():
    assert implied_probability_from_odds({"decimal": 2.0}) == pytest.approx(0.5)
    assert implied_probability_from_odds({"us": -200.0}) == pytest.approx(1.0 / 1.5)
    assert implied_probability_from_odds({"frac": [3, 2]}) == pytest.approx(0.4)
    assert implied_probability_from_odds({}) is None
    assert implied_probability_from_odds({"decimal": 0}) is None


def test_closing_line_value():
    # Took 2.20, closed 2.00 -> beat the close by 10%.
    assert closing_line_value(2.20, 2.00) == pytest.approx(0.10)
    assert closing_line_value_bps(2.20, 2.00) == pytest.approx(1000.0)
    # Took the worse price -> negative CLV.
    assert closing_line_value(1.80, 2.00) == pytest.approx(-0.10)
    assert closing_line_value(2.00, 2.00) == 0.0
    with pytest.raises(ValueError):
        closing_line_value(1.0, 2.0)
    with pytest.raises(ValueError):
        closing_line_value(2.0, 1.0)
