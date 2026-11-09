"""Tests for the model artifact manifest contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from axel.training.artifact import (
    ArtifactError,
    ModelArtifact,
    build_artifact,
    load_artifact,
    sha256_file,
)


def test_artifact_round_trip_hashes_model_file(tmp_path: Path) -> None:
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    artifact = build_artifact(
        tmp_path,
        model_id="kronos-mini",
        version="2024-01-01",
        section="STOCKS",
        timeframe="1Hour",
        horizon_bars=4,
        universe=("AAPL", "MSFT"),
        seed=7,
        code_commit="abc123",
        train_window=("2023-01-01", "2023-12-31"),
        validation_window=("2024-01-01", "2024-02-01"),
        artifact_file="model.pt",
        metrics={"brier": 0.21},
    )
    assert artifact.content_hash == sha256_file(model)

    loaded = load_artifact(tmp_path)
    assert loaded == artifact
    assert loaded.universe == ("AAPL", "MSFT")
    assert loaded.metrics == {"brier": 0.21}


def test_artifact_rejects_missing_model_file(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError):
        build_artifact(
            tmp_path,
            model_id="m",
            version="v",
            section="STOCKS",
            timeframe="1Hour",
            horizon_bars=1,
            universe=("AAPL",),
            seed=1,
            code_commit="c",
            train_window=("a", "b"),
            validation_window=("c", "d"),
            artifact_file="missing.pt",
        )


def test_artifact_rejects_unknown_task() -> None:
    with pytest.raises(ArtifactError):
        ModelArtifact(
            model_id="m",
            version="v",
            task="execution",
            section="STOCKS",
            timeframe="1Hour",
            horizon_bars=1,
            universe=("AAPL",),
            seed=1,
            content_hash="h",
            code_commit="c",
            train_window=("a", "b"),
            validation_window=("c", "d"),
            artifact_file="model.pt",
        )
