"""Tests for the proposal-only Kronos adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from axel.agents.kronos import (
    Forecast,
    KronosForecastAnalyst,
    StubPredictor,
    load_analyst,
)
from axel.training.artifact import ModelArtifact, build_artifact

START = datetime(2024, 1, 1, tzinfo=UTC)


def _artifact(tmp_path: Path, *, horizon_bars: int = 1) -> ModelArtifact:
    (tmp_path / "model.pt").write_bytes(b"weights")
    return build_artifact(
        tmp_path,
        model_id="kronos-mini",
        version="v1",
        section="STOCKS",
        timeframe="1Hour",
        horizon_bars=horizon_bars,
        universe=("AAPL",),
        seed=1,
        code_commit="c",
        train_window=("a", "b"),
        validation_window=("c", "d"),
        artifact_file="model.pt",
    )


def test_kronos_adapter_maps_forecast_to_signal(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    analyst = KronosForecastAnalyst(artifact=artifact, predictor=StubPredictor(0.02, 0.8, 1))
    forecast = analyst.forecast("AAPL", [100.0, 101.0], START)
    signal = analyst.to_signal(forecast)

    assert signal.symbol == "AAPL"
    assert signal.analyst == "KRONOS"
    assert signal.direction == "LONG"
    assert signal.strength == pytest.approx(0.6)
    assert signal.horizon == "1b"
    assert signal.section == "STOCKS"
    assert len(signal.features_ref or "") <= 128
    assert json.loads(signal.features_ref or "{}")["m"] == "kronos-mini"


def test_kronos_adapter_rejects_horizon_mismatch(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, horizon_bars=4)
    analyst = KronosForecastAnalyst(artifact=artifact, predictor=StubPredictor(0.0, 0.5, 1))
    with pytest.raises(ValueError):
        analyst.forecast("AAPL", [100.0], START)


def test_kronos_adapter_rejects_naive_timestamp(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    analyst = KronosForecastAnalyst(artifact=artifact, predictor=StubPredictor())
    with pytest.raises(ValueError):
        analyst.forecast("AAPL", [100.0], datetime(2024, 1, 1))


def test_kronos_emit_persists_signal(in_memory_db, tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    analyst = KronosForecastAnalyst(artifact=artifact, predictor=StubPredictor(0.01, 0.7, 1))
    forecast = analyst.forecast("AAPL", [100.0], START)
    row = analyst.emit(in_memory_db, forecast)
    in_memory_db.commit()

    assert in_memory_db.get(type(row), row.id) is not None


def test_load_analyst_reads_manifest(tmp_path: Path) -> None:
    _artifact(tmp_path)
    analyst = load_analyst(tmp_path, StubPredictor())
    assert analyst.artifact.model_id == "kronos-mini"


def test_forecast_requires_aware_timestamp() -> None:
    with pytest.raises(ValueError):
        Forecast(
            symbol="AAPL",
            as_of=datetime(2024, 1, 1),
            expected_return=0.0,
            probability_up=0.5,
            horizon_bars=1,
        )
