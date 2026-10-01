"""Cross-sectional transforms over many entities sharing a decision time."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from axel.data.features.base import FeatureRecord, mean, population_std


def _group_by_time(features: Sequence[FeatureRecord]) -> dict[datetime, list[FeatureRecord]]:
    groups: dict[datetime, list[FeatureRecord]] = {}
    for feature in features:
        groups.setdefault(feature.event_time, []).append(feature)
    return groups


def _entire_group_available(group: Sequence[FeatureRecord]) -> datetime:
    return max(feature.available_at for feature in group)


def zscore_by_time(features: Sequence[FeatureRecord]) -> list[FeatureRecord]:
    """Standardize a feature against its peers at the same decision time."""
    transformed: list[FeatureRecord] = []
    for event_time, group in _group_by_time(features).items():
        values = [feature.value for feature in group]
        average = mean(values)
        deviation = population_std(values)
        available = _entire_group_available(group)
        for feature in group:
            value = 0.0 if deviation == 0 else (feature.value - average) / deviation
            transformed.append(
                FeatureRecord(
                    name=f"{feature.name}_zscore",
                    entity=feature.entity,
                    event_time=event_time,
                    available_at=available,
                    value=value,
                    inputs=feature.inputs,
                    params=feature.params,
                )
            )
    return sorted(transformed, key=lambda feature: (feature.event_time, feature.entity))


def demean_by_time(features: Sequence[FeatureRecord]) -> list[FeatureRecord]:
    """Subtract the cross-sectional mean at each decision time."""
    transformed: list[FeatureRecord] = []
    for event_time, group in _group_by_time(features).items():
        average = mean([feature.value for feature in group])
        available = _entire_group_available(group)
        for feature in group:
            transformed.append(
                FeatureRecord(
                    name=f"{feature.name}_demeaned",
                    entity=feature.entity,
                    event_time=event_time,
                    available_at=available,
                    value=feature.value - average,
                    inputs=feature.inputs,
                    params=feature.params,
                )
            )
    return sorted(transformed, key=lambda feature: (feature.event_time, feature.entity))


def rank_by_time(features: Sequence[FeatureRecord]) -> list[FeatureRecord]:
    """Percentile rank (0 = lowest) across peers at each decision time."""
    transformed: list[FeatureRecord] = []
    for event_time, group in _group_by_time(features).items():
        ordered = sorted(group, key=lambda feature: feature.value)
        count = len(ordered)
        available = _entire_group_available(group)
        for position, feature in enumerate(ordered):
            value = position / (count - 1) if count > 1 else 0.0
            transformed.append(
                FeatureRecord(
                    name=f"{feature.name}_rank",
                    entity=feature.entity,
                    event_time=event_time,
                    available_at=available,
                    value=value,
                    inputs=feature.inputs,
                    params=feature.params,
                )
            )
    return sorted(transformed, key=lambda feature: (feature.event_time, feature.entity))
