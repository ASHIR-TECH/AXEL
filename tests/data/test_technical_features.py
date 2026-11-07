"""Point-in-time technical indicators added from the reference quant notebooks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from axel.data.features import (
    average_directional_index,
    bollinger_bandwidth,
    bollinger_percent_b,
    donchian_position,
    ema,
    macd,
    on_balance_volume,
    stochastic_oscillator,
)
from axel.data.schemas import BarRecord, Provenance

START = datetime(2026, 1, 1, tzinfo=UTC)


def _prov(seed: int, available_at: datetime | None = None) -> Provenance:
    return Provenance(
        source="test",
        source_id=str(seed),
        source_hash=f"hash-{seed}",
        ingestion_timestamp=available_at or START,
    )


def _bar(
    index: int,
    *,
    price: float,
    volume: float = 1_000.0,
    high: float | None = None,
    low: float | None = None,
    available_delay: timedelta | None = None,
) -> BarRecord:
    event = START + timedelta(days=index)
    available = event + (available_delay or timedelta(0))
    return BarRecord(
        symbol="AAPL",
        venue="alpaca-iex",
        event_time=event,
        available_at=available,
        open=price,
        high=price + 1.0 if high is None else high,
        low=price - 1.0 if low is None else low,
        close=price,
        volume=volume,
        adjustment_status="raw",
        provenance=_prov(index, available),
    )


def _trend(count: int = 80, *, rate: float = 0.01) -> list[BarRecord]:
    return [_bar(index, price=100.0 * (1.0 + rate) ** index) for index in range(count)]


def _flat(count: int = 80) -> list[BarRecord]:
    return [_bar(index, price=100.0, high=100.0, low=100.0) for index in range(count)]


def _by_name(features, name: str):
    return [feature for feature in features if feature.name.startswith(name)]


def test_all_indicators_are_deterministic_and_point_in_time() -> None:
    bars = _trend()
    produced = [
        *ema(bars, window=10),
        *macd(bars, fast=3, slow=6, signal=3),
        *bollinger_percent_b(bars, window=10),
        *bollinger_bandwidth(bars, window=10),
        *stochastic_oscillator(bars, window=10),
        *donchian_position(bars, window=10),
        *on_balance_volume(bars),
        *average_directional_index(bars, window=10),
    ]
    assert produced
    assert all(feature.available_at >= feature.event_time for feature in produced)
    assert all(len(feature.fingerprint()) == 64 for feature in produced)
    assert ema(bars, window=10) == ema(bars, window=10)


def test_feature_availability_uses_the_latest_input_bar() -> None:
    bars = _trend(30)
    delayed = bars[20]
    bars[20] = BarRecord(
        symbol=delayed.symbol,
        venue=delayed.venue,
        event_time=delayed.event_time,
        available_at=delayed.event_time + timedelta(hours=5),
        open=delayed.open,
        high=delayed.high,
        low=delayed.low,
        close=delayed.close,
        volume=delayed.volume,
        adjustment_status=delayed.adjustment_status,
        provenance=delayed.provenance,
    )
    features = bollinger_percent_b(bars, window=5)
    stamped = next(feature for feature in features if feature.event_time == delayed.event_time)
    assert stamped.available_at == bars[20].available_at


def test_ema_of_a_constant_series_is_constant() -> None:
    features = ema(_flat(40), window=10)
    assert features
    assert all(feature.value == pytest.approx(100.0) for feature in features)


def test_macd_of_a_constant_series_is_zero() -> None:
    features = macd(_flat(60), fast=5, slow=10, signal=4)
    assert _by_name(features, "macd_line")
    assert all(feature.value == pytest.approx(0.0) for feature in features)


def test_macd_requires_fast_shorter_than_slow() -> None:
    with pytest.raises(ValueError):
        macd(_trend(30), fast=10, slow=10)


def test_macd_histogram_equals_line_minus_signal() -> None:
    features = macd(_trend(80), fast=5, slow=10, signal=4)
    lines = {(feature.event_time): feature.value for feature in _by_name(features, "macd_line")}
    signals = {
        feature.event_time: feature.value for feature in _by_name(features, "macd_signal")
    }
    histograms = {
        feature.event_time: feature.value for feature in _by_name(features, "macd_histogram")
    }
    assert histograms
    for event_time, histogram in histograms.items():
        assert histogram == pytest.approx(lines[event_time] - signals[event_time])


def test_bollinger_position_and_bandwidth_on_a_flat_series() -> None:
    assert all(
        feature.value == pytest.approx(0.5) for feature in bollinger_percent_b(_flat(40), window=10)
    )
    assert all(
        feature.value == pytest.approx(0.0)
        for feature in bollinger_bandwidth(_flat(40), window=10)
    )


def test_bollinger_position_is_above_one_when_price_breaks_the_upper_band() -> None:
    bars = _flat(40)
    bars[-1] = _bar(39, price=200.0, high=201.0, low=199.0)
    features = bollinger_percent_b(bars, window=10)
    assert features[-1].value > 1.0


def test_stochastic_and_donchian_are_bounded() -> None:
    stochastic = stochastic_oscillator(_trend(60), window=10)
    donchian = donchian_position(_trend(60), window=10)
    assert all(0.0 <= feature.value <= 100.0 for feature in stochastic)
    assert all(0.0 <= feature.value <= 1.0 for feature in donchian)


def test_stochastic_and_donchian_midpoint_on_a_flat_series() -> None:
    assert all(
        feature.value == pytest.approx(50.0)
        for feature in stochastic_oscillator(_flat(40), window=10)
    )
    assert all(
        feature.value == pytest.approx(0.5)
        for feature in donchian_position(_flat(40), window=10)
    )


def test_on_balance_volume_adds_up_volume_and_subtracts_down_volume() -> None:
    bars = [
        _bar(0, price=100.0, volume=10.0),
        _bar(1, price=101.0, volume=20.0),
        _bar(2, price=99.0, volume=30.0),
        _bar(3, price=99.0, volume=40.0),
    ]
    values = [feature.value for feature in on_balance_volume(bars)]
    assert values == [0.0, 20.0, -10.0, -10.0]


def test_adx_is_high_in_a_trend_and_zero_in_a_flat_market() -> None:
    trending = average_directional_index(_trend(80), window=14)
    flat = average_directional_index(_flat(80), window=14)
    assert trending
    assert all(0.0 <= feature.value <= 100.0 for feature in trending)
    assert trending[-1].value > 50.0
    assert all(feature.value == pytest.approx(0.0) for feature in flat)


def test_adx_returns_empty_when_history_is_too_short() -> None:
    assert average_directional_index(_trend(20), window=14) == []
