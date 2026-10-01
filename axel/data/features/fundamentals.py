"""Fundamental deterministic features, usable only from public filing time."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from axel.data.features.base import FeatureRecord, param
from axel.data.schemas import FilingRecord


def filing_value(filings: Sequence[FilingRecord], *, field: str) -> list[FeatureRecord]:
    """Extract a numeric field from filings, available at their public filing time."""
    if not field:
        raise ValueError("field is required")
    ordered = sorted(filings, key=lambda filing: filing.available_at)
    params = param(field=field)
    features: list[FeatureRecord] = []
    for filing in ordered:
        raw = dict(filing.fields).get(field)
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        features.append(
            FeatureRecord(
                name=f"filing_{field}",
                entity=filing.issuer_id,
                event_time=filing.event_time,
                available_at=filing.available_at,
                value=value,
                inputs=(filing.provenance.source_hash,),
                params=params,
            )
        )
    return features


def filing_change(filings: Sequence[FilingRecord], *, field: str) -> list[FeatureRecord]:
    """Period-over-period change of a filing field (knowable only at the later filing)."""
    values = filing_value(filings, field=field)
    params = param(field=field)
    features: list[FeatureRecord] = []
    for previous, current in pairwise(values):
        features.append(
            FeatureRecord(
                name=f"filing_{field}_change",
                entity=current.entity,
                event_time=current.event_time,
                available_at=max(previous.available_at, current.available_at),
                value=current.value - previous.value,
                inputs=previous.inputs + current.inputs,
                params=params,
            )
        )
    return features
