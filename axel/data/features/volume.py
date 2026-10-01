"""Volume-derived deterministic features (single series)."""

from __future__ import annotations

from collections.abc import Sequence

from axel.data.features.base import (
    FeatureRecord,
    by_time,
    feature_for,
    max_available,
    mean,
    param,
    sample_std,
)
from axel.data.schemas import BarRecord


def volume(bars: Sequence[BarRecord]) -> list[FeatureRecord]:
    return [feature_for("volume", bar, bar.volume, params=param()) for bar in by_time(bars)]


def dollar_volume(bars: Sequence[BarRecord]) -> list[FeatureRecord]:
    return [
        feature_for("dollar_volume", bar, bar.close * bar.volume, params=param())
        for bar in by_time(bars)
    ]


def volume_ratio(bars: Sequence[BarRecord], *, window: int = 20) -> list[FeatureRecord]:
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        used = series[index - window : index + 1]
        baseline = mean([bar.volume for bar in used[:-1]])
        if baseline == 0:
            continue
        features.append(
            feature_for(
                f"volume_ratio_{window}",
                series[index],
                series[index].volume / baseline,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def volume_zscore(bars: Sequence[BarRecord], *, window: int = 20) -> list[FeatureRecord]:
    if window < 2:
        raise ValueError("window must be >= 2")
    series = by_time(bars)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        values = [bar.volume for bar in used]
        deviation = sample_std(values)
        if deviation == 0:
            continue
        features.append(
            feature_for(
                f"volume_zscore_{window}",
                series[index],
                (values[-1] - mean(values)) / deviation,
                params=params,
                available_at=max_available(used),
            )
        )
    return features
