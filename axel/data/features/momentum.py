"""Momentum deterministic features (single series)."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from axel.data.features.base import FeatureRecord, by_time, feature_for, max_available, param
from axel.data.schemas import BarRecord


def momentum(bars: Sequence[BarRecord], *, window: int = 20) -> list[FeatureRecord]:
    """Cumulative return over ``window`` bars."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        used = series[index - window : index + 1]
        previous = used[0].close
        if previous == 0:
            continue
        features.append(
            feature_for(
                f"momentum_{window}",
                series[index],
                series[index].close / previous - 1.0,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def average_true_momentum(
    bars: Sequence[BarRecord], *, window: int = 20
) -> list[FeatureRecord]:
    """Fraction of the last ``window`` bars that closed up."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        used = series[index - window : index + 1]
        ups = sum(1 for prev, cur in pairwise(used) if cur.close > prev.close)
        features.append(
            feature_for(
                f"up_fraction_{window}",
                series[index],
                ups / window,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def rsi(bars: Sequence[BarRecord], *, window: int = 14) -> list[FeatureRecord]:
    """Wilder-style RSI computed with a simple rolling mean of gains/losses."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        used = series[index - window : index + 1]
        gains = 0.0
        losses = 0.0
        for previous, current in pairwise(used):
            change = current.close - previous.close
            if change >= 0:
                gains += change
            else:
                losses -= change
        if losses == 0:
            value = 100.0
        else:
            rs = gains / losses
            value = 100.0 - (100.0 / (1.0 + rs))
        features.append(
            feature_for(
                f"rsi_{window}",
                series[index],
                value,
                params=params,
                available_at=max_available(used),
            )
        )
    return features
