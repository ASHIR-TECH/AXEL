"""Reproducible experiment runner for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ExperimentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INVALID = "invalid"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ExperimentConfig:
    run_id: str
    hypothesis_id: str
    hypothesis_version: int
    dataset_versions: list[tuple[str, str]] = field(default_factory=list)
    feature_versions: list[tuple[str, str]] = field(default_factory=list)
    code_commit: str = ""
    model_family: str = "baseline"
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    train_start: datetime | None = None
    train_end: datetime | None = None
    val_start: datetime | None = None
    val_end: datetime | None = None
    test_start: datetime | None = None
    test_end: datetime | None = None
    instrument_universe: list[str] = field(default_factory=list)
    cost_model_version: str = "default"
    slippage_model: str = "fixed_bps"
    execution_assumptions: dict[str, Any] = field(default_factory=dict)
    benchmark_config: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)
    validation_protocol_id: str = "walk_forward_v1"
    config_hash: str = ""
    resource_limits: dict[str, Any] = field(default_factory=lambda: {"cpu": 2, "mem_mb": 4096, "timeout_s": 3600})


@dataclass(frozen=True)
class ExperimentRun:
    config: ExperimentConfig
    status: ExperimentStatus = ExperimentStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    artifact_refs: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    partial_results: dict[str, Any] = field(default_factory=dict)

    def start(self) -> "ExperimentRun":
        return replace(self, status=ExperimentStatus.RUNNING, started_at=datetime.now(UTC))

    def complete(self, metrics: dict[str, Any] | None = None, artifacts: list[str] | None = None) -> "ExperimentRun":
        return replace(
            self,
            status=ExperimentStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            metrics=metrics or self.metrics,
            artifact_refs=artifacts or self.artifact_refs,
        )

    def fail(self, reason: str) -> "ExperimentRun":
        return replace(self, status=ExperimentStatus.FAILED, failure_reason=reason, completed_at=datetime.now(UTC))


class ExperimentRunner:
    def __init__(self) -> None:
        self._runs: dict[str, ExperimentRun] = {}

    def submit(self, config: ExperimentConfig) -> ExperimentRun:
        run = ExperimentRun(config=config)
        self._runs[config.run_id] = run
        return run

    def get(self, run_id: str) -> ExperimentRun | None:
        return self._runs.get(run_id)

    def run(self, run_id: str) -> ExperimentRun:
        run = self._runs[run_id]
        run = run.start()
        try:
            run = run.complete(metrics={"status": "ok"})
        except Exception as e:
            run = run.fail(str(e))
        self._runs[run_id] = run
        return run

    def list_all(self) -> list[ExperimentRun]:
        return list(self._runs.values())
