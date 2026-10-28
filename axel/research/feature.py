"""Feature registry for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class FeatureVersion:
    feature_id: str
    version: str
    description: str = ""
    economic_rationale: str = ""
    input_datasets: list[tuple[str, str]] = field(default_factory=list)
    required_lookback: int = 0
    availability_lag: int = 0
    calculation_method: str = ""
    missing_data_behavior: str = "drop_or_impute"
    normalization_method: str = "none"
    fit_on_training_only: bool = True
    warmup_bars: int = 0
    applicable_sections: list[str] = field(default_factory=list)
    cross_sectional: bool = False
    dependencies: list[tuple[str, str]] = field(default_factory=list)
    leakage_risks: list[str] = field(default_factory=list)
    immutable: bool = False
    validated: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    current_version: str
    versions: dict[str, FeatureVersion] = field(default_factory=dict)

    def get(self, version: str | None = None) -> FeatureVersion | None:
        v = version or self.current_version
        return self.versions.get(v)

    def add_version(self, fv: FeatureVersion, make_current: bool = False) -> "FeatureDefinition":
        versions = dict(self.versions)
        versions[fv.version] = fv
        current = fv.version if make_current or not self.versions else self.current_version
        return FeatureDefinition(feature_id=self.feature_id, current_version=current, versions=versions)


class FeatureRegistry:
    def __init__(self) -> None:
        self._features: dict[str, FeatureDefinition] = {}

    def register(self, feature_def: FeatureDefinition) -> None:
        self._features[feature_def.feature_id] = feature_def

    def get(self, feature_id: str) -> FeatureDefinition | None:
        return self._features.get(feature_id)

    def get_version(self, feature_id: str, version: str | None = None) -> FeatureVersion | None:
        fd = self._features.get(feature_id)
        if not fd:
            return None
        return fd.get(version)

    def list_all(self) -> list[FeatureDefinition]:
        return list(self._features.values())
