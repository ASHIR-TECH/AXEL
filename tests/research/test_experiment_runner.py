"""Unit and integration tests for reproducible experiment execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from axel.data.models import Bar
from axel.research.dataset import DatasetRegistry, DatasetVersion, PointInTimeChecker
from axel.research.experiment import (
    ExperimentConfig,
    ExperimentRunner,
    ExperimentStatus,
)
from axel.research.hypothesis import HypothesisRegistry


def _sample_bars(n: int = 50) -> list[Bar]:
    base_ts = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    bars = []
    price = 100.0
    for i in range(n):
        # alternating price to generate signals
        price = price + (1.5 if i % 2 == 0 else -0.8)
        bars.append(
            Bar(
                symbol="TEST",
                timestamp=base_ts + timedelta(days=i),
                open=price - 0.2,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=10_000.0,
            )
        )
    return bars


def test_experiment_config_hashing():
    cfg1 = ExperimentConfig(
        run_id="run_1",
        hypothesis_id="hyp_1",
        hypothesis_version=1,
        fee_bps=1.5,
        hyperparameters={"lookback": 5},
    )
    cfg2 = ExperimentConfig(
        run_id="run_2",
        hypothesis_id="hyp_1",
        hypothesis_version=1,
        fee_bps=1.5,
        hyperparameters={"lookback": 5},
    )
    assert cfg1.compute_hash() == cfg2.compute_hash()

    cfg3 = ExperimentConfig(
        run_id="run_3",
        hypothesis_id="hyp_1",
        hypothesis_version=1,
        fee_bps=2.0,  # different fee
        hyperparameters={"lookback": 5},
    )
    assert cfg1.compute_hash() != cfg3.compute_hash()


def test_runner_requires_registered_hypothesis():
    hyp_reg = HypothesisRegistry()
    h = hyp_reg.create(title="Unregistered Draft", question="?")
    runner = ExperimentRunner(hypothesis_registry=hyp_reg)

    cfg = ExperimentConfig(
        run_id="run_test",
        hypothesis_id=h.hypothesis_id,
        hypothesis_version=1,
    )
    runner.submit(cfg)
    run = runner.run(cfg.run_id)

    assert run.status == ExperimentStatus.INVALID
    assert "still in DRAFT status" in (run.failure_reason or "")


def test_runner_fails_on_point_in_time_violation():
    hyp_reg = HypothesisRegistry()
    h = hyp_reg.create(title="Test PIT", question="?")
    hyp_reg.register(h.hypothesis_id)

    ds_reg = DatasetRegistry()
    # Available only on March 1
    ds_reg.register(DatasetVersion(dataset_id="ds1", version="v1", ingestion_time=datetime(2026, 3, 1, tzinfo=UTC)))

    pit_checker = PointInTimeChecker(ds_reg)
    runner = ExperimentRunner(hypothesis_registry=hyp_reg, pit_checker=pit_checker)

    # Experiment simulated at Feb 1 (before ingestion)
    cfg = ExperimentConfig(
        run_id="run_pit_fail",
        hypothesis_id=h.hypothesis_id,
        hypothesis_version=1,
        dataset_versions=[("ds1", "v1")],
        train_start=datetime(2026, 2, 1, tzinfo=UTC),
    )
    runner.submit(cfg)
    run = runner.run(cfg.run_id)

    assert run.status == ExperimentStatus.INVALID
    assert "Point-in-time validation failed" in (run.failure_reason or "")


def test_runner_executes_backtest_with_costs():
    hyp_reg = HypothesisRegistry()
    h = hyp_reg.create(title="Backtest Run", question="?")
    hyp_reg.register(h.hypothesis_id)

    runner = ExperimentRunner(hypothesis_registry=hyp_reg)
    cfg = ExperimentConfig(
        run_id="run_bt",
        hypothesis_id=h.hypothesis_id,
        hypothesis_version=1,
        fee_bps=2.0,
        slippage_bps=1.0,
        hyperparameters={"lookback": 3},
    )
    runner.submit(cfg)
    bars = _sample_bars(60)
    run = runner.run(cfg.run_id, bars=bars)

    assert run.status == ExperimentStatus.COMPLETED
    assert run.metrics["trades"] > 0
    assert "sharpe" in run.metrics
    assert "max_drawdown" in run.metrics
    assert run.metrics["fee_bps"] == 2.0
