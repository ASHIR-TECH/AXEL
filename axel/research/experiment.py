"""Reproducible experiment runner for Phase 10."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from axel.data.backtester import summarize_returns, trend_returns
from axel.data.models import Bar
from axel.research.dataset import PointInTimeChecker
from axel.research.hypothesis import HypothesisRegistry, HypothesisStatus


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
    fee_bps: float = 1.0
    slippage_bps: float = 1.0
    slippage_model: str = "fixed_bps"
    execution_assumptions: dict[str, Any] = field(default_factory=dict)
    benchmark_config: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)
    validation_protocol_id: str = "walk_forward_v1"
    config_hash: str = ""
    resource_limits: dict[str, Any] = field(default_factory=lambda: {"cpu": 2, "mem_mb": 4096, "timeout_s": 3600})

    def __post_init__(self) -> None:
        if not self.config_hash:
            object.__setattr__(self, "config_hash", self.compute_hash())

    def compute_hash(self) -> str:
        data = {
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_version": self.hypothesis_version,
            "dataset_versions": self.dataset_versions,
            "feature_versions": self.feature_versions,
            "model_family": self.model_family,
            "hyperparameters": self.hyperparameters,
            "seed": self.seed,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "validation_protocol_id": self.validation_protocol_id,
        }
        canonical = json.dumps(data, sort_keys=True)
        return sha256(canonical.encode("utf-8")).hexdigest()


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

    def fail(self, reason: str, status: ExperimentStatus = ExperimentStatus.FAILED) -> "ExperimentRun":
        return replace(self, status=status, failure_reason=reason, completed_at=datetime.now(UTC))

    def cancel(self, reason: str = "User cancelled run") -> "ExperimentRun":
        return replace(self, status=ExperimentStatus.CANCELLED, failure_reason=reason, completed_at=datetime.now(UTC))


class ExperimentRunner:
    def __init__(
        self,
        hypothesis_registry: HypothesisRegistry | None = None,
        pit_checker: PointInTimeChecker | None = None,
    ) -> None:
        self.hypothesis_registry = hypothesis_registry
        self.pit_checker = pit_checker
        self._runs: dict[str, ExperimentRun] = {}

    def submit(self, config: ExperimentConfig) -> ExperimentRun:
        c = config
        if not c.config_hash:
            c = replace(c, config_hash=c.compute_hash())
        run = ExperimentRun(config=c)
        self._runs[c.run_id] = run
        return run

    def get(self, run_id: str) -> ExperimentRun | None:
        return self._runs.get(run_id)

    def cancel(self, run_id: str, reason: str = "User cancelled") -> ExperimentRun:
        run = self._runs[run_id]
        updated = run.cancel(reason)
        self._runs[run_id] = updated
        return updated

    def run(
        self,
        run_id: str,
        bars: list[Bar] | None = None,
        returns: list[float] | None = None,
    ) -> ExperimentRun:
        run = self._runs[run_id]
        cfg = run.config

        # 1. Prerequisite check: Registered hypothesis
        if self.hypothesis_registry:
            hyp = self.hypothesis_registry.get(cfg.hypothesis_id)
            if not hyp:
                failed = run.fail(f"Hypothesis '{cfg.hypothesis_id}' not found.", status=ExperimentStatus.INVALID)
                self._runs[run_id] = failed
                return failed
            if hyp.status == HypothesisStatus.DRAFT:
                failed = run.fail(
                    f"Hypothesis '{cfg.hypothesis_id}' is still in DRAFT status; must be REGISTERED before running experiments.",
                    status=ExperimentStatus.INVALID,
                )
                self._runs[run_id] = failed
                return failed

        # 2. Point-in-time check if configured
        if self.pit_checker and cfg.dataset_versions:
            check_ts = cfg.train_start or datetime.now(UTC)
            valid, pit_errors = self.pit_checker.validate(cfg.dataset_versions, check_ts)
            if not valid:
                failed = run.fail(f"Point-in-time validation failed: {pit_errors}", status=ExperimentStatus.INVALID)
                self._runs[run_id] = failed
                return failed

        run = run.start()

        # 3. Deterministic backtest execution
        try:
            metrics: dict[str, Any] = {}
            if bars and len(bars) > 3:
                lookback = int(cfg.hyperparameters.get("lookback", 3))
                bt_res = trend_returns(
                    bars,
                    lookback=lookback,
                    fee_bps=cfg.fee_bps,
                    slippage_bps=cfg.slippage_bps,
                )
                metrics = {
                    "trades": bt_res.trades,
                    "expectancy_r": bt_res.expectancy_r,
                    "sharpe": bt_res.sharpe,
                    "max_drawdown": bt_res.max_drawdown,
                    "fee_bps": cfg.fee_bps,
                    "slippage_bps": cfg.slippage_bps,
                }
            elif returns is not None:
                # Apply simulated cost
                cost_per_trade = (cfg.fee_bps + cfg.slippage_bps) / 10_000.0
                adjusted_returns = [r - cost_per_trade for r in returns]
                bt_res = summarize_returns(adjusted_returns)
                metrics = {
                    "trades": bt_res.trades,
                    "expectancy_r": bt_res.expectancy_r,
                    "sharpe": bt_res.sharpe,
                    "max_drawdown": bt_res.max_drawdown,
                    "fee_bps": cfg.fee_bps,
                    "slippage_bps": cfg.slippage_bps,
                }
            else:
                # Fallback baseline metrics recorded
                metrics = {
                    "trades": 0,
                    "expectancy_r": 0.0,
                    "sharpe": 0.0,
                    "max_drawdown": 0.0,
                    "status": "completed_no_data",
                }

            artifacts = [f"artifacts/{run_id}/results.json"]
            run = run.complete(metrics=metrics, artifacts=artifacts)
        except Exception as e:
            run = run.fail(str(e))

        self._runs[run_id] = run
        return run

    def list_all(self) -> list[ExperimentRun]:
        return list(self._runs.values())
