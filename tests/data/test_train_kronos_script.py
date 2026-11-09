"""Tests for the Kronos training-plan and config-generation script."""

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

CONFIG_SOURCE = '''class Config:
    def __init__(self):
        self.qlib_data_path = "~/.qlib/qlib_data/cn_data"
        self.instrument = 'csi300'
        self.dataset_begin_time = "2011-01-01"
        self.dataset_end_time = '2025-06-05'
        self.dataset_path = "./data/processed_datasets"
        self.train_time_range = ["2011-01-01", "2022-12-31"]
        self.val_time_range = ["2022-09-01", "2024-06-30"]
        self.test_time_range = ["2024-04-01", "2025-06-05"]
        self.backtest_time_range = ["2024-07-01", "2025-06-05"]
        self.save_path = "./outputs/models"
        self.pretrained_tokenizer_path = "path/to/tokenizer"
        self.pretrained_predictor_path = "path/to/predictor"
        self.backtest_n_symbol_hold = 50
        self.backtest_n_symbol_drop = 5
        self.use_comet = True
        self.epochs = 30
        self.batch_size = 50
        self.max_context = 512
        self.lookback_window = 90
        self.predict_window = 10
        self.backtest_benchmark = self._set_benchmark(self.instrument)
'''


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


def _manifest(tmp_path: Path, folds: list[dict], holdout: dict | None = None) -> Path:
    payload = {
        "schema_version": "axel-folds-1",
        "timestamp_count": 100,
        "symbols": ["AAPL", "MSFT"],
        "folds": folds,
    }
    if holdout is not None:
        payload["holdout"] = holdout
    path = tmp_path / "folds.json"
    path.write_text(json.dumps(payload))
    return path


def test_load_fold_manifest_reads_folds_and_holdout(tmp_path: Path) -> None:
    holdout = {"start": "2024-06-01T00:00:00+00:00", "end": "2024-12-01T00:00:00+00:00", "rows": 50}
    count, symbols, folds, loaded_holdout = train_kronos.load_fold_manifest(
        _manifest(tmp_path, [_fold()], holdout)
    )
    assert count == 100
    assert symbols == ("AAPL", "MSFT")
    assert folds[0].index == 0
    assert loaded_holdout == holdout


def test_load_fold_manifest_rejects_leakage(tmp_path: Path) -> None:
    path = _manifest(tmp_path, [_fold(train_end="2024-01-15", test_start="2024-01-15")])
    with pytest.raises(SystemExit):
        train_kronos.load_fold_manifest(path)


def test_load_fold_manifest_rejects_wrong_schema(tmp_path: Path) -> None:
    path = tmp_path / "folds.json"
    path.write_text(json.dumps({"schema_version": "other", "folds": []}))
    with pytest.raises(SystemExit):
        train_kronos.load_fold_manifest(path)


def test_compute_windows_is_disjoint_and_uses_holdout() -> None:
    folds = [
        train_kronos.FoldSummary(
            index=0,
            train_start="2020-07-27T04:00:00+00:00",
            train_end="2023-07-19T04:00:00+00:00",
            test_start="2023-07-20T04:00:00+00:00",
            test_end="2024-02-22T05:00:00+00:00",
            train_rows=1,
            test_rows=1,
        )
    ]
    holdout = {"start": "2025-08-07T04:00:00+00:00", "end": "2026-11-06T04:00:00+00:00"}
    windows = train_kronos.compute_windows(folds, holdout)

    assert windows["dataset_begin_time"] == "2020-07-27"
    assert windows["dataset_end_time"] == "2026-11-06"
    assert windows["train_time_range"][1] < windows["val_time_range"][1]
    assert windows["val_time_range"][1] < windows["test_time_range"][0]
    assert windows["test_time_range"] == ["2025-08-07", "2026-11-06"]


def test_render_kronos_config_sets_every_value() -> None:
    values = {
        "qlib_data_path": "/data/kronos/qlib",
        "instrument": "all",
        "dataset_begin_time": "2020-07-27",
        "dataset_end_time": "2026-11-06",
        "dataset_path": "/data/kronos/processed",
        "train_time_range": ["2020-07-27", "2025-02-07"],
        "val_time_range": ["2024-10-10", "2025-08-06"],
        "test_time_range": ["2025-08-07", "2026-11-06"],
        "backtest_time_range": ["2025-08-07", "2026-11-06"],
        "save_path": "/artifacts/kronos-mini/runs",
        "pretrained_tokenizer_path": "NeoQuasar/Kronos-Tokenizer-2k",
        "pretrained_predictor_path": "NeoQuasar/Kronos-mini",
        "backtest_n_symbol_hold": 5,
        "backtest_n_symbol_drop": 1,
        "use_comet": False,
        "epochs": 10,
        "batch_size": 32,
        "max_context": 2048,
        "lookback_window": 90,
        "predict_window": 10,
    }
    rendered = train_kronos.render_kronos_config(CONFIG_SOURCE, values, benchmark="SPY")

    assert "self.qlib_data_path = '/data/kronos/qlib'" in rendered
    assert "self.instrument = 'all'" in rendered
    assert "self.backtest_benchmark = 'SPY'" in rendered
    assert "_set_benchmark" not in rendered
    assert "self.use_comet = False" in rendered
    assert "self.max_context = 2048" in rendered


def test_render_kronos_config_rejects_missing_key() -> None:
    with pytest.raises(SystemExit):
        train_kronos.render_kronos_config(CONFIG_SOURCE, {"not_a_real_setting": 1})


def test_install_config_keeps_original(tmp_path: Path) -> None:
    repo = tmp_path / "kronos"
    (repo / "finetune").mkdir(parents=True)
    target = repo / "finetune" / "config.py"
    target.write_text("original")

    installed = train_kronos.install_config("generated", repo)

    assert installed == target
    assert target.read_text() == "generated"
    assert target.with_suffix(".py.orig").read_text() == "original"

    train_kronos.install_config("generated-again", repo)
    assert target.with_suffix(".py.orig").read_text() == "original"


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


def test_main_writes_plan_and_installs_config(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "kronos"
    (repo / "finetune").mkdir(parents=True)
    (repo / "finetune" / "config.py").write_text(CONFIG_SOURCE)
    folds = _manifest(
        tmp_path,
        [_fold()],
        {"start": "2024-06-01T00:00:00+00:00", "end": "2024-12-01T00:00:00+00:00", "rows": 50},
    )
    out = tmp_path / "out"
    data_root = tmp_path / "data"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_kronos.py",
            "--folds",
            str(folds),
            "--kronos-repo",
            str(repo),
            "--out",
            str(out),
            "--data-root",
            str(data_root),
            "--install-config",
        ],
    )

    assert train_kronos.main() == 0

    plan = json.loads((out / "train_plan.json").read_text())
    assert plan["training_data"]["qlib_data_path"] == str((data_root / "qlib").resolve())
    assert plan["symbols"] == ["AAPL", "MSFT"]
    assert plan["holdout"]["rows"] == 50
    assert (out / "kronos_config.py").exists()
    assert (out / "train.sh").exists()
    assert (repo / "finetune" / "config.py").read_text() != CONFIG_SOURCE
