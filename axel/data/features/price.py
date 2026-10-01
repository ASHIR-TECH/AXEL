"""Price-derived deterministic features (single series)."""

from __future__ import annotations

from collections.abc import Sequence
from math import log

from axel.data.features.base import FeatureRecord, by_time, feature_for, max_available, param
from axel.data.schemas import BarRecord


def close_price(bars: Sequence[BarRecord]) -> list[FeatureRecord]:
    return [feature_for("close", bar, bar.close, params=param()) for bar in by_time(bars)]


def simple_return(
    bars: Sequence[BarRecord], *, window: int = 1
) -> list[FeatureRecord]:
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
                f"simple_return_{window}",
                series[index],
                series[index].close / previous - 1.0,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def log_return(bars: Sequence[BarRecord], *, window: int = 1) -> list[FeatureRecord]:
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        used = series[index - window : index + 1]
        previous = used[0].close
        if previous <= 0 or series[index].close <= 0:
            continue
        features.append(
            feature_for(
                f"log_return_{window}",
                series[index],
                log(series[index].close / previous),
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def overnight_gap(bars: Sequence[BarRecord]) -> list[FeatureRecord]:
    series = by_time(bars)
    params = param()
    features: list[FeatureRecord] = []
    for index in range(1, len(series)):
        used = series[index - 1 : index + 1]
        previous_close = used[0].close
        if previous_close == 0:
            continue
        features.append(
            feature_for(
                "overnight_gap",
                series[index],
                series[index].open / previous_close - 1.0,
                params=params,
                available_at=max_available(used),
            )
        )
    return features
