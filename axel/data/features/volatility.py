"""Volatility deterministic features (single series)."""

from __future__ import annotations

from collections.abc import Sequence
from math import log, sqrt

from axel.data.features.base import (
    FeatureRecord,
    by_time,
    feature_for,
    max_available,
    mean,
    param,
    returns,
    sample_std,
)
from axel.data.schemas import BarRecord

_TRADING_DAYS = 252


def realized_volatility(
    bars: Sequence[BarRecord], *, window: int = 20, annualize: bool = True
) -> list[FeatureRecord]:
    """Sample standard deviation of simple returns over ``window`` bars."""
    if window < 2:
        raise ValueError("window must be >= 2")
    series = by_time(bars)
    series_returns = returns([bar.close for bar in series])
    params = param(window=window, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        window_returns = series_returns[index - window : index]
        features.append(
            feature_for(
                f"realized_volatility_{window}",
                series[index],
                sample_std(window_returns) * scale,
                params=params,
                available_at=max_available(series[index - window : index + 1]),
            )
        )
    return features


def average_true_range(bars: Sequence[BarRecord], *, window: int = 14) -> list[FeatureRecord]:
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    true_ranges = _true_ranges(series)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        addr = max_available(series[index - window : index + 1])
        features.append(
            feature_for(
                f"atr_{window}",
                series[index],
                mean(true_ranges[index - window : index]),
                params=params,
                available_at=addr,
            )
        )
    return features


def parkinson_volatility(
    bars: Sequence[BarRecord], *, window: int = 20, annualize: bool = True
) -> list[FeatureRecord]:
    """Range-based volatility estimator using only high/low."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    denominator = 4.0 * log(2.0)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        squared = [(log(bar.high / bar.low)) ** 2 for bar in used]
        features.append(
            feature_for(
                f"parkinson_volatility_{window}",
                series[index],
                sqrt(sum(squared) / (denominator * len(used))) * scale,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def _true_ranges(series: Sequence[BarRecord]) -> list[float]:
    ranges: list[float] = []
    for index, bar in enumerate(series):
        if index == 0:
            ranges.append(bar.high - bar.low)
            continue
        previous_close = series[index - 1].close
        ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return ranges
