"""Dataset registry with point-in-time integrity for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


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
    timezone: str = "UTC"
    market_calendar: str = "NYSE"
    schema_version: str = "1.0"
    content_hash: str = ""
    access_permissions: list[str] = field(default_factory=list)
    retention_days: int = 3650
    data_quality_report: dict[str, Any] = field(default_factory=dict)

    def is_available_at(self, decision_ts: datetime) -> bool:
        if self.publication_time and decision_ts < self.publication_time:
            return False
        if self.effective_time and decision_ts < self.effective_time:
            return False
        if self.ingestion_time and decision_ts < self.ingestion_time:
            return False
        return True


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
