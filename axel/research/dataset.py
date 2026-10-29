"""Dataset registry with point-in-time integrity for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class DatasetVersion:
    dataset_id: str
    version: str
    provider: str = "internal"
    universe_spec_id: str = "default"
    event_time: datetime | None = None
    publication_time: datetime | None = None
    ingestion_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    effective_time: datetime | None = None
    adjustment_policy: str = "split_dividend_adjusted"
    missing_data_policy: str = "explicit_na"
    currency: str = "USD"
    unit_metadata: str = "shares_and_usd"
    timezone: str = "UTC"
    market_calendar: str = "NYSE"
    schema_version: str = "1.0"
    content_hash: str = ""
    access_permissions: list[str] = field(default_factory=list)
    retention_days: int = 3650
    data_quality_report: dict[str, Any] = field(default_factory=dict)

    def is_available_at(self, decision_ts: datetime) -> bool:
        ts = ensure_utc(decision_ts)
        assert ts is not None

        # 1. Event cannot be observed before it happens
        if self.event_time:
            ev = ensure_utc(self.event_time)
            if ev and ts < ev:
                return False

        # 2. Publication delay
        if self.publication_time:
            pub = ensure_utc(self.publication_time)
            if pub and ts < pub:
                return False

        # 3. Effective delay (e.g. corporate action or filing effective date)
        if self.effective_time:
            eff = ensure_utc(self.effective_time)
            if eff and ts < eff:
                return False

        # 4. Ingestion delay (when AXEL system actually ingested the data)
        if self.ingestion_time:
            ing = ensure_utc(self.ingestion_time)
            if ing and ts < ing:
                return False

        return True

    def verify_content_hash(self, content_bytes: bytes) -> bool:
        if not self.content_hash:
            return True
        computed = sha256(content_bytes).hexdigest()
        return computed.lower() == self.content_hash.lower()


@dataclass(frozen=True)
class DatasetLineage:
    dataset_id: str
    version: str
    parent_dataset_id: str | None = None
    parent_version: str | None = None
    transformation: str = ""
    code_commit: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provenance_hash: str = ""


class DatasetRegistry:
    def __init__(self) -> None:
        self._datasets: dict[tuple[str, str], DatasetVersion] = {}
        self._lineage: dict[tuple[str, str], DatasetLineage] = {}

    def register(self, dataset: DatasetVersion, lineage: DatasetLineage | None = None) -> None:
        key = (dataset.dataset_id, dataset.version)
        self._datasets[key] = dataset
        if lineage:
            self._lineage[key] = lineage

    def get(self, dataset_id: str, version: str) -> DatasetVersion | None:
        return self._datasets.get((dataset_id, version))

    def get_lineage(self, dataset_id: str, version: str) -> DatasetLineage | None:
        return self._lineage.get((dataset_id, version))

    def list_all(self) -> list[DatasetVersion]:
        return list(self._datasets.values())


class PointInTimeChecker:
    def __init__(self, registry: DatasetRegistry) -> None:
        self.registry = registry

    def validate(self, dataset_refs: list[tuple[str, str]], decision_ts: datetime) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for dataset_id, version in dataset_refs:
            ds = self.registry.get(dataset_id, version)
            if not ds:
                errors.append(f"missing dataset {dataset_id}@{version}")
                continue
            if not ds.is_available_at(decision_ts):
                errors.append(f"point-in-time violation for {dataset_id}@{version} at {decision_ts}")
        return (len(errors) == 0, errors)

    def validate_observations(
        self,
        observations: list[Any],
        decision_ts: datetime,
        timestamp_key: str = "timestamp",
    ) -> tuple[bool, list[str]]:
        """Verify no observation in historical sequence has a timestamp after decision_ts."""
        errors: list[str] = []
        target_ts = ensure_utc(decision_ts)
        assert target_ts is not None

        for idx, obs in enumerate(observations):
            obs_dt: datetime | None = None
            if isinstance(obs, dict):
                obs_val = obs.get(timestamp_key)
                if isinstance(obs_val, datetime):
                    obs_dt = obs_val
            elif hasattr(obs, timestamp_key):
                obs_val = getattr(obs, timestamp_key)
                if isinstance(obs_val, datetime):
                    obs_dt = obs_val

            if obs_dt is not None:
                obs_utc = ensure_utc(obs_dt)
                if obs_utc and obs_utc > target_ts:
                    errors.append(
                        f"look-ahead leakage at index {idx}: observation timestamp {obs_utc} is after decision timestamp {target_ts}"
                    )

        return (len(errors) == 0, errors)
