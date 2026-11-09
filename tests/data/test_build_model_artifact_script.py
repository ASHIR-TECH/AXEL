"""Tests for the model artifact builder script."""

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


build_model_artifact = _load("build_model_artifact")


def test_metrics_filters_non_numeric(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            {
                "brier": 0.21,
                "trades": 140,
                "approved": True,
                "reasons": ["X"],
                "label": "ok",
            }
        )
    )
    assert build_model_artifact._metrics(path) == {"brier": 0.21, "trades": 140.0}


def test_metrics_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(SystemExit):
        build_model_artifact._metrics(path)
