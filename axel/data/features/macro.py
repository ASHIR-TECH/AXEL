"""Macro deterministic features from vintage-aware observations."""

from __future__ import annotations

from collections.abc import Sequence

from axel.data.features.base import FeatureRecord, param
from axel.data.schemas import MacroObservation


def macro_value(observations: Sequence[MacroObservation]) -> list[FeatureRecord]:
    ordered = sorted(observations, key=lambda observation: observation.available_at)
    params = param()
    return [
        FeatureRecord(
            name="macro_value",
            entity=observation.series_id,
            event_time=observation.event_time,
            available_at=observation.available_at,
            value=observation.value,
            inputs=(observation.provenance.source_hash,),
            params=params,
        )
        for observation in ordered
    ]


def macro_change(
    observations: Sequence[MacroObservation], *, periods: int = 1
) -> list[FeatureRecord]:
    """Change versus ``periods`` observations earlier (knowable at the later vintage)."""
    if periods < 1:
        raise ValueError("periods must be >= 1")
    ordered = sorted(observations, key=lambda observation: observation.event_time)
    params = param(periods=periods)
    features: list[FeatureRecord] = []
    for index in range(periods, len(ordered)):
        current = ordered[index]
        previous = ordered[index - periods]
        features.append(
            FeatureRecord(
                name=f"macro_change_{periods}",
                entity=current.series_id,
                event_time=current.event_time,
                available_at=max(previous.available_at, current.available_at),
                value=current.value - previous.value,
                inputs=(previous.provenance.source_hash, current.provenance.source_hash),
                params=params,
            )
        )
    return features
