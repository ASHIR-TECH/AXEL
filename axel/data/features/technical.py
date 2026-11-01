"""
Deterministic technical indicators over canonical OHLCV bars.

These are the indicator families the reference repositories use most often
(Sunday Quant Scientist QS004/QS009/QS010/QS013, TradingAgents' technical
toolkit): moving-average convergence, Bollinger position, stochastic
oscillation, directional movement, on-balance volume and Donchian position.

Every function is pure and point-in-time. A value stamped on bar *i* only
depends on bars at or before *i*, and its ``available_at`` is the latest
availability among the bars it used -- never the decision wall clock. That is
what lets the same code run in a backtest and in paper trading without a
look-ahead gap.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from axel.data.features.base import (
    FeatureRecord,
    by_time,
    feature_for,
    max_available,
    mean,
    param,
    population_std,
)
from axel.data.schemas import BarRecord


def _require_window(window: int, *, minimum: int = 1) -> None:
    if window < minimum:
        raise ValueError(f"window must be >= {minimum}")


def _running_available(series: Sequence[BarRecord]) -> list[datetime]:
    """Latest availability at or before each index (for whole-history features)."""
    running: list[datetime] = []
    latest: datetime | None = None
    for bar in series:
        latest = bar.available_at if latest is None else max(latest, bar.available_at)
        running.append(latest)
    return running


def _ema_values(values: Sequence[float], window: int) -> list[float | None]:
    """EMA seeded with the first ``window``-value SMA; ``None`` before that."""
    _require_window(window)
    if len(values) < window:
        return [None] * len(values)
    alpha = 2.0 / (window + 1.0)
    seed = mean(values[:window])
    result: list[float | None] = [None] * (window - 1) + [seed]
    previous = seed
    for value in values[window:]:
        previous = alpha * value + (1.0 - alpha) * previous
        result.append(previous)
    return result


def ema(bars: Sequence[BarRecord], *, window: int = 20) -> list[FeatureRecord]:
    """Exponential moving average of close, seeded by a simple average."""
    series = by_time(bars)
    values = _ema_values([bar.close for bar in series], window)
    params = param(window=window)
    available = _running_available(series)
    return [
        feature_for(
            f"ema_{window}",
            series[index],
            value,
            params=params,
            available_at=available[index],
        )
        for index, value in enumerate(values)
        if value is not None
    ]


def macd(
    bars: Sequence[BarRecord],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[FeatureRecord]:
    """MACD line, signal line and histogram as three aligned feature families.

    The MACD line is ``EMA(fast) - EMA(slow)``. The signal line is an EMA of the
    MACD line, so the histogram (MACD - signal) is only emitted once the signal
    window is also available. A shorter history returns an empty list rather
    than a zero that could be mistaken for a neutral reading.
    """
    if fast >= slow:
        raise ValueError("fast must be shorter than slow")
    _require_window(fast)
    _require_window(slow)
    _require_window(signal)
    series = by_time(bars)
    closes = [bar.close for bar in series]
    fast_values = _ema_values(closes, fast)
    slow_values = _ema_values(closes, slow)
    if len(closes) < slow:
        return []

    macd_line: list[float | None] = [None] * len(closes)
    for index in range(slow - 1, len(closes)):
        fast_value = fast_values[index]
        slow_value = slow_values[index]
        if fast_value is not None and slow_value is not None:
            macd_line[index] = fast_value - slow_value

    first_macd = slow - 1
    defined = [value for value in macd_line[first_macd:] if value is not None]
    signal_values = _ema_values(defined, signal)
    available = _running_available(series)

    features: list[FeatureRecord] = []
    for offset, value in enumerate(macd_line[first_macd:], start=first_macd):
        if value is None:
            continue
        features.append(
            feature_for(
                f"macd_line_{fast}_{slow}",
                series[offset],
                value,
                params=param(fast=fast, slow=slow),
                available_at=available[offset],
            )
        )
    for offset, value in enumerate(signal_values):
        if value is None:
            continue
        index = first_macd + offset
        line = macd_line[index]
        if line is None:
            continue
        features.append(
            feature_for(
                f"macd_signal_{fast}_{slow}_{signal}",
                series[index],
                value,
                params=param(fast=fast, slow=slow, signal=signal),
                available_at=available[index],
            )
        )
        features.append(
            feature_for(
                f"macd_histogram_{fast}_{slow}_{signal}",
                series[index],
                line - value,
                params=param(fast=fast, slow=slow, signal=signal),
                available_at=available[index],
            )
        )
    return features


def bollinger_percent_b(
    bars: Sequence[BarRecord], *, window: int = 20, num_std: float = 2.0
) -> list[FeatureRecord]:
    """Position of close inside its Bollinger band: 0 at the lower, 1 at the upper.

    A flat band (zero width) reports 0.5 instead of dividing by zero. Values
    outside [0, 1] are valid: they mean price closed outside the band.
    """
    _require_window(window, minimum=2)
    if num_std < 0:
        raise ValueError("num_std cannot be negative")
    series = by_time(bars)
    params = param(window=window, num_std=num_std)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        closes = [bar.close for bar in used]
        middle = mean(closes)
        width = num_std * population_std(closes)
        lower, upper = middle - width, middle + width
        value = 0.5 if upper == lower else (used[-1].close - lower) / (upper - lower)
        features.append(
            feature_for(
                f"bollinger_percent_b_{window}",
                used[-1],
                value,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def bollinger_bandwidth(
    bars: Sequence[BarRecord], *, window: int = 20, num_std: float = 2.0
) -> list[FeatureRecord]:
    """Band width relative to its moving average (a volatility-expansion gauge)."""
    _require_window(window, minimum=2)
    if num_std < 0:
        raise ValueError("num_std cannot be negative")
    series = by_time(bars)
    params = param(window=window, num_std=num_std)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        closes = [bar.close for bar in used]
        middle = mean(closes)
        width = 2.0 * num_std * population_std(closes)
        value = 0.0 if middle == 0 else width / middle
        features.append(
            feature_for(
                f"bollinger_bandwidth_{window}",
                used[-1],
                value,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def stochastic_oscillator(bars: Sequence[BarRecord], *, window: int = 14) -> list[FeatureRecord]:
    """Fast %K: close's position in the recent high-low range, scaled to 0-100."""
    _require_window(window)
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        highest = max(bar.high for bar in used)
        lowest = min(bar.low for bar in used)
        span = highest - lowest
        value = 50.0 if span == 0 else (used[-1].close - lowest) / span * 100.0
        features.append(
            feature_for(
                f"stochastic_{window}",
                used[-1],
                value,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def donchian_position(bars: Sequence[BarRecord], *, window: int = 20) -> list[FeatureRecord]:
    """Close's position in the Donchian channel, from 0 (low) to 1 (high)."""
    _require_window(window)
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        highest = max(bar.high for bar in used)
        lowest = min(bar.low for bar in used)
        span = highest - lowest
        value = 0.5 if span == 0 else (used[-1].close - lowest) / span
        features.append(
            feature_for(
                f"donchian_position_{window}",
                used[-1],
                value,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def on_balance_volume(bars: Sequence[BarRecord]) -> list[FeatureRecord]:
    """Cumulative signed volume (OBV), starting at zero on the first bar."""
    series = by_time(bars)
    if not series:
        return []
    available = _running_available(series)
    params = param()
    value = 0.0
    features = [feature_for("obv", series[0], value, params=params, available_at=available[0])]
    for index in range(1, len(series)):
        previous, current = series[index - 1], series[index]
        if current.close > previous.close:
            value += current.volume
        elif current.close < previous.close:
            value -= current.volume
        features.append(
            feature_for("obv", current, value, params=params, available_at=available[index])
        )
    return features


def _directional_components(
    series: Sequence[BarRecord],
) -> tuple[list[float], list[float], list[float]]:
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(series)):
        current, previous = series[index], series[index - 1]
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    return true_ranges, plus_dm, minus_dm


def _dx(true_range: float, plus: float, minus: float) -> float:
    if true_range <= 0:
        return 0.0
    plus_di = 100.0 * plus / true_range
    minus_di = 100.0 * minus / true_range
    total = plus_di + minus_di
    if total <= 0:
        return 0.0
    return 100.0 * abs(plus_di - minus_di) / total


def average_directional_index(bars: Sequence[BarRecord], *, window: int = 14) -> list[FeatureRecord]:
    """Wilder's Average Directional Index (ADX), a trend-strength measure.

    Unlike a raw directional index, ADX rises for a strong trend in *either*
    direction and stays low in a range, which is why it is useful as a filter
    rather than a signal. Wilder smoothing is used so the value matches the
    conventional indicator.
    """
    _require_window(window)
    series = by_time(bars)
    if len(series) < 2 * window:
        return []
    true_ranges, plus_dm, minus_dm = _directional_components(series)
    smoothed_tr = mean(true_ranges[:window])
    smoothed_plus = mean(plus_dm[:window])
    smoothed_minus = mean(minus_dm[:window])
    dx_values = [_dx(smoothed_tr, smoothed_plus, smoothed_minus)]
    for index in range(window, len(true_ranges)):
        smoothed_tr = smoothed_tr - smoothed_tr / window + true_ranges[index]
        smoothed_plus = smoothed_plus - smoothed_plus / window + plus_dm[index]
        smoothed_minus = smoothed_minus - smoothed_minus / window + minus_dm[index]
        dx_values.append(_dx(smoothed_tr, smoothed_plus, smoothed_minus))

    available = _running_available(series)
    params = param(window=window)
    adx_value = mean(dx_values[:window])
    features = [
        feature_for(
            f"adx_{window}",
            series[2 * window - 1],
            adx_value,
            params=params,
            available_at=available[2 * window - 1],
        )
    ]
    for index in range(window, len(dx_values)):
        adx_value = (adx_value * (window - 1) + dx_values[index]) / window
        bar_index = window + index
        features.append(
            feature_for(
                f"adx_{window}",
                series[bar_index],
                adx_value,
                params=params,
                available_at=available[bar_index],
            )
        )
    return features


__all__ = [
    "average_directional_index",
    "bollinger_bandwidth",
    "bollinger_percent_b",
    "donchian_position",
    "ema",
    "macd",
    "on_balance_volume",
    "stochastic_oscillator",
]
