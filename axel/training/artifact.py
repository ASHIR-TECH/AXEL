"""Model artifact contract shared by the training harness and the runtime.

The artifact directory is the only interface between the GPU work and AXEL:

    artifacts/<model_id>/<version>/
        model.pt | model.safetensors
        manifest.json

The manifest records exactly which data, code and settings produced the model,
so a signal can always be traced back to a reproducible artifact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA_VERSION = "axel-model-artifact-1"
SUPPORTED_TASKS = ("forecast",)


class ArtifactError(ValueError):
    """Raised when an artifact manifest is missing or inconsistent."""


@dataclass(frozen=True)
class ModelArtifact:
    model_id: str
    version: str
    task: str
    section: str
    timeframe: str
    horizon_bars: int
    universe: tuple[str, ...]
    seed: int
    content_hash: str
    code_commit: str
    train_window: tuple[str, str]
    validation_window: tuple[str, str]
    artifact_file: str
    metrics: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: str = ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.task not in SUPPORTED_TASKS:
            raise ArtifactError(f"unsupported task {self.task!r}; expected one of {SUPPORTED_TASKS}")
        if self.horizon_bars < 1:
            raise ArtifactError("horizon_bars must be positive")
        if not self.model_id or not self.version:
            raise ArtifactError("model_id and version are required")
        if len(self.train_window) != 2 or len(self.validation_window) != 2:
            raise ArtifactError("train_window and validation_window must be (start, end) pairs")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["universe"] = list(self.universe)
        payload["train_window"] = list(self.train_window)
        payload["validation_window"] = list(self.validation_window)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelArtifact:
        try:
            return cls(
                model_id=payload["model_id"],
                version=payload["version"],
                task=payload["task"],
                section=payload.get("section", "STOCKS"),
                timeframe=payload["timeframe"],
                horizon_bars=int(payload["horizon_bars"]),
                universe=tuple(payload["universe"]),
                seed=int(payload["seed"]),
                content_hash=payload["content_hash"],
                code_commit=payload["code_commit"],
                train_window=tuple(payload["train_window"]),
                validation_window=tuple(payload["validation_window"]),
                artifact_file=payload["artifact_file"],
                metrics={key: float(value) for key, value in payload.get("metrics", {}).items()},
                created_at=payload.get("created_at", datetime.now(UTC).isoformat()),
                schema_version=payload.get("schema_version", ARTIFACT_SCHEMA_VERSION),
            )
        except KeyError as error:
            raise ArtifactError(f"manifest is missing required field {error.args[0]!r}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifact(directory: Path, artifact: ModelArtifact) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(json.dumps(artifact.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_artifact(directory: Path) -> ModelArtifact:
    path = directory / "manifest.json"
    if not path.exists():
        raise ArtifactError(f"no manifest.json in {directory}")
    return ModelArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))


def build_artifact(
    directory: Path,
    *,
    model_id: str,
    version: str,
    section: str,
    timeframe: str,
    horizon_bars: int,
    universe: tuple[str, ...],
    seed: int,
    code_commit: str,
    train_window: tuple[str, str],
    validation_window: tuple[str, str],
    artifact_file: str,
    metrics: dict[str, float] | None = None,
) -> ModelArtifact:
    """Hash the model file and write the manifest next to it."""
    model_path = directory / artifact_file
    if not model_path.exists():
        raise ArtifactError(f"artifact file not found: {model_path}")
    artifact = ModelArtifact(
        model_id=model_id,
        version=version,
        task="forecast",
        section=section,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        universe=universe,
        seed=seed,
        content_hash=sha256_file(model_path),
        code_commit=code_commit,
        train_window=train_window,
        validation_window=validation_window,
        artifact_file=artifact_file,
        metrics=metrics or {},
    )
    write_artifact(directory, artifact)
    return artifact


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "SUPPORTED_TASKS",
    "ArtifactError",
    "ModelArtifact",
    "build_artifact",
    "load_artifact",
    "sha256_file",
    "write_artifact",
]
