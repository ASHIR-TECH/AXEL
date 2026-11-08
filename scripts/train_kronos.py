"""Plan (and optionally run) a Kronos fine-tune over AXEL's prepared folds.

This script deliberately does not import ``torch``. It validates the fold
manifest, writes a reproducible command plan, and only touches the GPU when
``--run`` is passed. The actual training loop lives in the Kronos repository,
which is the supported fine-tuning path.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

VARIANTS = {
    "mini": {"parameters": "4.1M", "gpu_memory": "4-8 GB"},
    "small": {"parameters": "24.7M", "gpu_memory": "8-16 GB"},
    "base": {"parameters": "102.3M", "gpu_memory": "16-24 GB"},
}


@dataclass(frozen=True)
class FoldSummary:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_rows: int
    test_rows: int


def load_fold_manifest(path: Path) -> tuple[int, tuple[str, ...], list[FoldSummary]]:
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
    return int(payload["timestamp_count"]), tuple(payload["symbols"]), folds


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
    parser.add_argument("--folds", required=True, type=Path, help="folds.json from prepare_training_folds.py")
    parser.add_argument("--kronos-repo", required=True, type=Path, help="local Kronos checkout")
    parser.add_argument("--out", required=True, type=Path, help="directory for the plan")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="mini")
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--run", action="store_true", help="execute the commands (requires GPU)")
    args = parser.parse_args()

    if args.gpus < 1:
        raise SystemExit("--gpus must be at least 1")
    if not (args.kronos_repo / "finetune" / "config.py").exists():
        raise SystemExit(
            f"{args.kronos_repo} does not look like a Kronos checkout "
            "(finetune/config.py missing)"
        )

    timestamp_count, symbols, folds = load_fold_manifest(args.folds)
    commands = build_commands(args.kronos_repo, gpus=args.gpus, device=args.device)

    args.out.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": "axel-kronos-train-plan-1",
        "variant": args.variant,
        "variant_spec": VARIANTS[args.variant],
        "gpus": args.gpus,
        "device": args.device,
        "timestamp_count": timestamp_count,
        "symbols": list(symbols),
        "folds": [asdict(fold) for fold in folds],
        "commands": [" ".join(command) for command in commands],
        "notes": [
            "Set qlib_data_path, dataset_path and save_path in finetune/config.py first.",
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
    print(f"folds     {len(folds)}")
    print(f"plan      {args.out / 'train_plan.json'}")
    print(f"script    {script_path}")
    for command in commands:
        print("  " + " ".join(command))

    if not args.run:
        print("dry run: pass --run to execute on the GPU host")
        return 0

    for command in commands:
        print(f"$ {' '.join(command)}", flush=True)
        subprocess.run(command, cwd=args.kronos_repo, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
