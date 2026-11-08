"""Tests for the Kronos training-plan script."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


train_kronos = _load("train_kronos")


def _fold(index: int = 0, *, train_end: str = "2024-01-10", test_start: str = "2024-01-11"):
    return {
        "index": index,
        "train_start": "2024-01-01",
        "train_end": train_end,
        "test_start": test_start,
        "test_end": "2024-01-20",
        "train_rows": 100,
        "test_rows": 50,
    }


def _manifest(tmp_path: Path, folds: list[dict]) -> Path:
    path = tmp_path / "folds.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "axel-folds-1",
                "timestamp_count": 100,
                "symbols": ["AAPL", "MSFT"],
                "folds": folds,
            }
        )
    )
    return path


def test_load_fold_manifest_reads_folds(tmp_path: Path) -> None:
    count, symbols, folds = train_kronos.load_fold_manifest(_manifest(tmp_path, [_fold()]))
    assert count == 100
    assert symbols == ("AAPL", "MSFT")
    assert folds[0].index == 0
    assert folds[0].train_rows == 100


def test_load_fold_manifest_rejects_leakage(tmp_path: Path) -> None:
    path = _manifest(tmp_path, [_fold(train_end="2024-01-15", test_start="2024-01-15")])
    with pytest.raises(SystemExit):
        train_kronos.load_fold_manifest(path)


def test_load_fold_manifest_rejects_wrong_schema(tmp_path: Path) -> None:
    path = tmp_path / "folds.json"
    path.write_text(json.dumps({"schema_version": "other", "folds": []}))
    with pytest.raises(SystemExit):
        train_kronos.load_fold_manifest(path)


def test_build_commands_uses_requested_gpu_count() -> None:
    commands = train_kronos.build_commands(Path("/repo"), gpus=2, device="cuda:1")
    assert commands[0] == ["python", "finetune/qlib_data_preprocess.py"]
    assert commands[1][2] == "--nproc_per_node=2"
    assert commands[2][2] == "--nproc_per_node=2"
    assert commands[3][-1] == "cuda:1"


def test_render_script_is_runnable_shell() -> None:
    script = train_kronos.render_script(
        train_kronos.build_commands(Path("/tmp/kronos"), gpus=1, device="cuda:0"),
        Path("/tmp/kronos"),
    )
    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "cd /tmp/kronos" in script
    assert "finetune/train_predictor.py" in script
