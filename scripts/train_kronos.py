"""Plan (and optionally run) a Kronos fine-tune over AXEL's prepared folds.

This script deliberately does not import ``torch``. It validates the fold
manifest, generates the exact ``finetune/config.py`` that points Kronos at
AXEL's Qlib data, writes a reproducible command plan, and only touches the GPU
when ``--run`` is passed.

Important: Kronos does not read ``folds.json``. It reads whatever
``finetune/config.py`` points at. This script makes that pointer explicit and
refuses to run unless the generated config has been installed.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

VARIANTS = {
    "mini": {
        "parameters": "4.1M",
        "gpu_memory": "4-8 GB",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-2k",
        "predictor": "NeoQuasar/Kronos-mini",
        "max_context": 2048,
    },
    "small": {
        "parameters": "24.7M",
        "gpu_memory": "8-16 GB",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "predictor": "NeoQuasar/Kronos-small",
        "max_context": 512,
    },
    "base": {
        "parameters": "102.3M",
        "gpu_memory": "16-24 GB",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "predictor": "NeoQuasar/Kronos-base",
        "max_context": 512,
    },
}

# The features qlib_data_preprocess.py actually feeds the model.
KRONOS_FEATURES = ("open", "high", "low", "close", "vol", "amt")


@dataclass(frozen=True)
class FoldSummary:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_rows: int
    test_rows: int


def load_fold_manifest(
    path: Path,
) -> tuple[int, tuple[str, ...], list[FoldSummary], dict[str, object] | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "axel-folds-1":
        raise SystemExit(f"{path} is not an axel-folds-1 manifest")
    folds = [
        FoldSummary(
            index=int(item["index"]),
            train_start=item["train_start"],
            train_end=item["train_end"],
            test_start=item["test_start"],
            test_end=item["test_end"],
            train_rows=int(item["train_rows"]),
            test_rows=int(item["test_rows"]),
        )
        for item in payload["folds"]
    ]
    if not folds:
        raise SystemExit(f"{path} contains no folds")
    for fold in folds:
        if fold.train_end >= fold.test_start:
            raise SystemExit(f"fold {fold.index} leaks: train_end >= test_start")
        if fold.train_rows < 1 or fold.test_rows < 1:
            raise SystemExit(f"fold {fold.index} has an empty split")
    holdout = payload.get("holdout")
    if holdout is not None and not isinstance(holdout, dict):
        raise SystemExit(f"{path} has a malformed holdout block")
    return (
        int(payload["timestamp_count"]),
        tuple(payload["symbols"]),
        folds,
        holdout,
    )


def _date(value: str) -> str:
    return value[:10]


def compute_windows(
    folds: list[FoldSummary], holdout: dict[str, object] | None
) -> dict[str, str | list[str]]:
    """Derive Kronos train/validation/test windows from the fold manifest.

    The validation window starts before training ends so the first validation
    samples have the full 90-bar lookback. The test window is the untouched
    holdout and is disjoint from both.
    """
    train_start = _date(folds[0].train_start)
    test_start_iso = str(holdout["start"]) if holdout else folds[-1].test_start
    test_end_iso = str(holdout["end"]) if holdout else folds[-1].test_end
    test_start = datetime.fromisoformat(test_start_iso)
    test_end = datetime.fromisoformat(test_end_iso)
    val_end = (test_start - timedelta(days=1)).date().isoformat()
    val_start = (test_start - timedelta(days=301)).date().isoformat()
    train_end = (test_start - timedelta(days=181)).date().isoformat()
    return {
        "dataset_begin_time": train_start,
        "dataset_end_time": test_end.date().isoformat(),
        "train_time_range": [train_start, train_end],
        "val_time_range": [val_start, val_end],
        "test_time_range": [test_start.date().isoformat(), test_end.date().isoformat()],
        "backtest_time_range": [test_start.date().isoformat(), test_end.date().isoformat()],
    }


def _assign(source: str, key: str, value: object) -> tuple[str, bool]:
    pattern = re.compile(rf"^(\s*)self\.{re.escape(key)}\s*=.*$", re.MULTILINE)
    updated, count = pattern.subn(
        lambda match: f"{match.group(1)}self.{key} = {value!r}", source, count=1
    )
    return updated, count == 1


def render_kronos_config(
    source: str, values: dict[str, object], *, benchmark: str = "SPY"
) -> str:
    """Patch a Kronos ``finetune/config.py`` with AXEL's values.

    Patching the upstream file keeps this resilient to upstream changes; adding
    a new setting only requires adding it to ``values``.
    """
    missing: list[str] = []
    rendered = source
    for key, value in values.items():
        rendered, ok = _assign(rendered, key, value)
        if not ok:
            missing.append(key)

    benchmark_pattern = re.compile(
        r"self\.backtest_benchmark\s*=\s*self\._set_benchmark\(self\.instrument\)"
    )
    rendered, count = benchmark_pattern.subn(
        f"self.backtest_benchmark = {benchmark!r}", rendered
    )
    if count != 1:
        missing.append("backtest_benchmark")

    if missing:
        raise SystemExit(
            "could not set these settings in the Kronos config: " + ", ".join(sorted(missing))
        )
    return rendered


def config_values(
    *,
    windows: dict[str, str | list[str]],
    qlib_data_path: Path,
    dataset_path: Path,
    save_path: Path,
    variant: str,
    epochs: int,
    batch_size: int,
    pretrained_tokenizer: str | None,
    pretrained_predictor: str | None,
) -> dict[str, object]:
    spec = VARIANTS[variant]
    values: dict[str, object] = {
        "qlib_data_path": str(qlib_data_path),
        "instrument": "all",
        "dataset_begin_time": windows["dataset_begin_time"],
        "dataset_end_time": windows["dataset_end_time"],
        "dataset_path": str(dataset_path),
        "train_time_range": windows["train_time_range"],
        "val_time_range": windows["val_time_range"],
        "test_time_range": windows["test_time_range"],
        "backtest_time_range": windows["backtest_time_range"],
        "save_path": str(save_path),
        "pretrained_tokenizer_path": pretrained_tokenizer or spec["tokenizer"],
        "pretrained_predictor_path": pretrained_predictor or spec["predictor"],
        "backtest_n_symbol_hold": 5,
        "backtest_n_symbol_drop": 1,
        "use_comet": False,
        "epochs": epochs,
        "batch_size": batch_size,
        "max_context": spec["max_context"],
        "lookback_window": 90,
        "predict_window": 10,
    }
    return values


def install_config(rendered: str, repo: Path) -> Path:
    """Write the generated config into the Kronos checkout, keeping a backup."""
    target = repo / "finetune" / "config.py"
    if not target.parent.exists():
        raise SystemExit(f"{repo} does not look like a Kronos checkout")
    backup = target.with_suffix(".py.orig")
    if target.exists() and not backup.exists():
        shutil.copy2(target, backup)
    target.write_text(rendered, encoding="utf-8")
    return target


def build_commands(repo: Path, *, gpus: int, device: str) -> list[list[str]]:
    """The documented Kronos fine-tune sequence, in order."""
    nproc = str(gpus)
    return [
        ["python", "finetune/qlib_data_preprocess.py"],
        [
            "torchrun",
            "--standalone",
            f"--nproc_per_node={nproc}",
            "finetune/train_tokenizer.py",
        ],
        [
            "torchrun",
            "--standalone",
            f"--nproc_per_node={nproc}",
            "finetune/train_predictor.py",
        ],
        ["python", "finetune/qlib_test.py", "--device", device],
    ]


def render_script(commands: list[list[str]], repo: Path) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(repo))}",
        "",
    ]
    lines.extend(" ".join(shlex.quote(part) for part in command) for command in commands)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan a Kronos fine-tune")
    parser.add_argument(
        "--folds", required=True, type=Path, help="folds.json from prepare_training_folds.py"
    )
    parser.add_argument("--kronos-repo", required=True, type=Path, help="local Kronos checkout")
    parser.add_argument("--out", required=True, type=Path, help="directory for the plan")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="mini")
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/kronos"),
        help="root containing qlib/, bars.csv and processed/ (default: data/kronos)",
    )
    parser.add_argument("--qlib-data-path", type=Path, default=None)
    parser.add_argument("--dataset-path", type=Path, default=None)
    parser.add_argument("--save-path", type=Path, default=None)
    parser.add_argument("--pretrained-tokenizer", default=None)
    parser.add_argument("--pretrained-predictor", default=None)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument(
        "--install-config",
        action="store_true",
        help="write the generated config into <kronos-repo>/finetune/config.py",
    )
    parser.add_argument("--run", action="store_true", help="execute the commands (requires GPU)")
    args = parser.parse_args()

    if args.gpus < 1:
        raise SystemExit("--gpus must be at least 1")
    source_config = args.kronos_repo / "finetune" / "config.py"
    if not source_config.exists():
        raise SystemExit(
            f"{args.kronos_repo} does not look like a Kronos checkout "
            "(finetune/config.py missing)"
        )

    timestamp_count, symbols, folds, holdout = load_fold_manifest(args.folds)
    windows = compute_windows(folds, holdout)
    commands = build_commands(args.kronos_repo, gpus=args.gpus, device=args.device)

    qlib_data_path = (args.qlib_data_path or args.data_root / "qlib").resolve()
    dataset_path = (args.dataset_path or args.data_root / "processed").resolve()
    save_path = (args.save_path or args.out / "runs").resolve()
    values = config_values(
        windows=windows,
        qlib_data_path=qlib_data_path,
        dataset_path=dataset_path,
        save_path=save_path,
        variant=args.variant,
        epochs=args.epochs,
        batch_size=args.batch_size,
        pretrained_tokenizer=args.pretrained_tokenizer,
        pretrained_predictor=args.pretrained_predictor,
    )
    rendered = render_kronos_config(
        source_config.read_text(encoding="utf-8"), values, benchmark=args.benchmark
    )

    args.out.mkdir(parents=True, exist_ok=True)
    config_path = args.out / "kronos_config.py"
    config_path.write_text(rendered, encoding="utf-8")

    if args.install_config:
        installed = install_config(rendered, args.kronos_repo)
        print(f"installed {installed}")

    plan = {
        "schema_version": "axel-kronos-train-plan-2",
        "variant": args.variant,
        "variant_spec": VARIANTS[args.variant],
        "gpus": args.gpus,
        "device": args.device,
        "timestamp_count": timestamp_count,
        "symbols": list(symbols),
        "training_data": {
            "source_csv": str((args.data_root / "bars.csv").resolve()),
            "qlib_data_path": str(qlib_data_path),
            "dataset_path": str(dataset_path),
            "features": list(KRONOS_FEATURES),
            "windows": windows,
            "benchmark": args.benchmark,
            "note": (
                "Kronos reads Qlib binary data through finetune/config.py. "
                "folds.json is only validated here, not consumed by Kronos."
            ),
        },
        "config_values": values,
        "folds": [asdict(fold) for fold in folds],
        "holdout": holdout,
        "commands": [" ".join(command) for command in commands],
        "notes": [
            "Edit qlib_data_preprocess.py and qlib_test.py: REG_CN -> REG_US.",
            "Edit qlib_test.py: limit_threshold 0.095 -> None (US has no daily limit).",
            "The generated config sets backtest_benchmark to the benchmark symbol.",
            "The pretrained checkpoints are the starting point; do not train from scratch.",
            "Copy the best checkpoint into the artifact directory before building the manifest.",
        ],
    }
    (args.out / "train_plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    script_path = args.out / "train.sh"
    script_path.write_text(render_script(commands, args.kronos_repo), encoding="utf-8")
    script_path.chmod(0o755)

    print(f"variant   {args.variant} ({VARIANTS[args.variant]['parameters']})")
    print(f"symbols   {', '.join(symbols)}")
    print(f"timestamps {timestamp_count}")
    print(f"csv       {plan['training_data']['source_csv']}")
    print(f"qlib      {qlib_data_path}")
    print(
        "windows   "
        f"train {windows['train_time_range'][0]}..{windows['train_time_range'][1]} | "
        f"val {windows['val_time_range'][0]}..{windows['val_time_range'][1]} | "
        f"test {windows['test_time_range'][0]}..{windows['test_time_range'][1]}"
    )
    print(f"config    {config_path}")
    print(f"plan      {args.out / 'train_plan.json'}")
    print(f"script    {script_path}")

    if not args.run:
        if not args.install_config:
            print(
                "config is NOT installed; run with --install-config "
                "before training"
            )
        print("dry run: pass --run --install-config to execute on the GPU host")
        return 0

    if not args.install_config:
        raise SystemExit(
            "refusing to train with an unverified config; pass --install-config"
        )
    for command in commands:
        print(f"$ {' '.join(command)}", flush=True)
        subprocess.run(command, cwd=args.kronos_repo, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
