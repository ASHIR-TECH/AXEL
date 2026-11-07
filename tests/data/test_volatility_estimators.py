"""Tests for range-based and exponentially weighted volatility estimators."""

from datetime import UTC, datetime, timedelta
from math import log, sqrt

import pytest

from axel.data.features import (
    ewma_volatility,
    garman_klass_volatility,
    parkinson_volatility,
    realized_volatility,
    rogers_satchell_volatility,
    yang_zhang_volatility,
)
from axel.data.schemas import BarRecord, Provenance

_START = datetime(2026, 1, 1, tzinfo=UTC)
_TRADING_DAYS = 252


def _bar(index: int, *, open_: float, high: float, low: float, close: float) -> BarRecord:
    event = _START + timedelta(days=index)
    return BarRecord(
        symbol="AAPL",
        venue="alpaca-iex",
        event_time=event,
        available_at=event,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        adjustment_status="raw",
        provenance=Provenance(
            source="test",
            source_id=str(index),
            source_hash=f"hash-{index}",
            ingestion_timestamp=_START,
        ),
    )


def _constant_bars(count: int) -> list[BarRecord]:
    return [_bar(index, open_=100.0, high=110.0, low=90.0, close=105.0) for index in range(count)]


def _trending_bars(count: int) -> list[BarRecord]:
    bars = []
    for index in range(count):
        close = 100.0 + index
        bars.append(
            _bar(
                index,
                open_=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
            )
        )
    return bars


def _gk_variance() -> float:
    return 0.5 * log(110.0 / 90.0) ** 2 - (2.0 * log(2.0) - 1.0) * log(105.0 / 100.0) ** 2


def _rs_variance() -> float:
    return log(110.0 / 105.0) * log(110.0 / 100.0) + log(90.0 / 105.0) * log(
        90.0 / 100.0
    )


def test_garman_klass_constant_bars() -> None:
    features = garman_klass_volatility(_constant_bars(10), window=5, annualize=False)

    assert len(features) == 6
    assert features[0].name == "garman_klass_volatility_5"
    assert features[0].value == pytest.approx(sqrt(_gk_variance()))


def test_rogers_satchell_constant_bars() -> None:
    features = rogers_satchell_volatility(_constant_bars(10), window=5, annualize=False)

    assert len(features) == 6
    assert features[0].name == "rogers_satchell_volatility_5"
    assert features[0].value == pytest.approx(sqrt(_rs_variance()))


def test_range_estimators_annualize() -> None:
    raw = garman_klass_volatility(_trending_bars(20), window=5, annualize=False)
    annualized = garman_klass_volatility(_trending_bars(20), window=5, annualize=True)

    assert annualized[0].value == pytest.approx(raw[0].value * sqrt(_TRADING_DAYS))
    assert raw[0].event_time == annualized[0].event_time


def test_yang_zhang_constant_bars_reduces_to_range_term() -> None:
    features = yang_zhang_volatility(_constant_bars(10), window=5, annualize=False)
    weight = 0.34 / (1.34 + (5 + 1) / (5 - 1))

    assert len(features) == 5
    assert features[0].name == "yang_zhang_volatility_5"
    assert features[0].value == pytest.approx(sqrt((1.0 - weight) * _rs_variance()))


def test_yang_zhang_uses_overnight_gaps() -> None:
    bars = _trending_bars(10)
    bars[7] = _bar(7, open_=107.5, high=108.0, low=106.0, close=107.0)

    features = yang_zhang_volatility(bars, window=5, annualize=False)

    assert features[-1].value > 0.0


def test_ewma_starts_at_realized_volatility_and_updates() -> None:
    bars = _trending_bars(30)
    ewma = ewma_volatility(bars, window=10, decay=0.5, annualize=False)
    realized = realized_volatility(bars, window=10, annualize=False)

    assert len(ewma) == len(realized)
    assert ewma[0].value == pytest.approx(realized[0].value)

    closes = [bar.close for bar in bars]
    seed_returns = [
        closes[index] / closes[index - 1] - 1.0 for index in range(1, 11)
    ]
    seed_mean = sum(seed_returns) / len(seed_returns)
    seed_variance = sum((value - seed_mean) ** 2 for value in seed_returns) / (
        len(seed_returns) - 1
    )
    next_return = closes[11] / closes[10] - 1.0
    expected = sqrt(0.5 * seed_variance + 0.5 * next_return**2)

    assert ewma[1].value == pytest.approx(expected)


def test_estimators_preserve_point_in_time_availability() -> None:
    bars = _trending_bars(20)
    for feature in garman_klass_volatility(bars, window=5):
        assert feature.available_at >= feature.event_time


def test_estimator_validation() -> None:
    bars = _constant_bars(10)

    with pytest.raises(ValueError):
        garman_klass_volatility(bars, window=0)
    with pytest.raises(ValueError):
        rogers_satchell_volatility(bars, window=0)
    with pytest.raises(ValueError):
        yang_zhang_volatility(bars, window=1)
    with pytest.raises(ValueError):
        ewma_volatility(bars, window=1)
    with pytest.raises(ValueError):
        ewma_volatility(bars, decay=1.0)
    with pytest.raises(ValueError):
        ewma_volatility(bars, decay=0.0)


def test_parkinson_remains_available_alongside_new_estimators() -> None:
    features = parkinson_volatility(_constant_bars(10), window=5)

    assert len(features) == 6
    assert features[0].value > 0.0
