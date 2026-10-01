"""
Deterministic feature contract.

A feature is a pure function of already-normalized records: same inputs + same
params + same version always yield the same value. Every feature carries its
decision time (``event_time``), the time it first became knowable
(``available_at``), the params/fingerprint and the provenance of what it used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from math import isfinite

from axel.data.schemas import BarRecord, check_available
from axel.util.stats import mean, population_std, sample_std, simple_returns

FEATURE_VERSION = "feat-1"


@dataclass(frozen=True)
class FeatureRecord:
    """A single named feature value for one entity at one decision time."""

    name: str
    entity: str
    event_time: datetime
    available_at: datetime
    value: float
    inputs: tuple[str, ...] = ()
    params: tuple[tuple[str, str], ...] = ()
    version: str = FEATURE_VERSION

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.name or not self.entity:
            raise ValueError("feature requires a name and an entity")
        if not isfinite(self.value):
            raise ValueError("feature value must be finite")

    def key(self) -> str:
        return f"feature:{self.name}:{self.entity}:{self.event_time.isoformat()}"

    def fingerprint(self) -> str:
        payload = f"{self.name}|{self.version}|{sorted(self.params)}"
        return sha256(payload.encode("utf-8")).hexdigest()


def feature_params(**params: object) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in params.items()))


def max_available(bars: Sequence[BarRecord]) -> datetime:
    return max(bar.available_at for bar in bars)


def feature_for(
    name: str,
    bar: BarRecord,
    value: float,
    *,
    params: tuple[tuple[str, str], ...] = (),
    inputs: tuple[str, ...] = (),
    available_at: datetime | None = None,
) -> FeatureRecord:
    """Build a feature from a bar, defaulting availability to the bar's own."""
    return FeatureRecord(
        name=name,
        entity=bar.symbol,
        event_time=bar.event_time,
        available_at=available_at or bar.available_at,
        value=value,
        inputs=inputs,
        params=params,
    )


def make_feature(
    name: str,
    *,
    entity: str,
    event_time: datetime,
    available_at: datetime,
    value: float,
    params: tuple[tuple[str, str], ...] = (),
    inputs: tuple[str, ...] = (),
) -> FeatureRecord:
    return FeatureRecord(
        name=name,
        entity=entity,
        event_time=event_time,
        available_at=available_at,
        value=value,
        inputs=inputs,
        params=params,
    )


def by_time(bars: Sequence[BarRecord]) -> list[BarRecord]:
    """Return bars sorted by event time, rejecting mixed symbols."""
    symbols = {bar.symbol for bar in bars}
    if len(symbols) > 1:
        raise ValueError("single-series features require one symbol at a time")
    return sorted(bars, key=lambda bar: bar.event_time)


def returns(closes: Sequence[float]) -> list[float]:
    """Simple period-over-period returns (length = closes - 1)."""
    return simple_returns(closes)


__all__ = [
    "FEATURE_VERSION",
    "FeatureRecord",
    "by_time",
    "feature_for",
    "feature_params",
    "make_feature",
    "max_available",
    "mean",
    "param",
    "population_std",
    "returns",
    "sample_std",
]


def param(
    params: Mapping[str, object] | None = None, **extra: object
) -> tuple[tuple[str, str], ...]:
    merged: dict[str, object] = dict(params or {})
    merged.update(extra)
    return feature_params(**merged)
