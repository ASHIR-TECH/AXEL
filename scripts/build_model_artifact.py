"""Build the ``manifest.json`` for a trained model artifact.

The training person copies their checkpoint into an artifact directory, runs
this script, and hands the directory back. AXEL never needs to know how the
checkpoint was produced beyond what the manifest records.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from axel.training.artifact import ArtifactError, build_artifact


def _git_commit(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _metrics(path: Path | None) -> dict[str, float]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("--metrics must point to a JSON object of name -> number")
    metrics: dict[str, float] = {}
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        metrics[str(key)] = float(value)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a model artifact manifest")
    parser.add_argument("--dir", required=True, type=Path, help="artifact directory")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--model-file", default="model.pt", help="checkpoint filename")
    parser.add_argument("--section", default="STOCKS")
    parser.add_argument("--timeframe", required=True, help="e.g. 1Hour")
    parser.add_argument("--horizon-bars", required=True, type=int)
    parser.add_argument("--universe", required=True, help="comma-separated symbols")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--train-start", required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--metrics", type=Path, default=None, help="JSON metrics file")
    parser.add_argument("--code-commit", default=None, help="override git HEAD")
    args = parser.parse_args()

    universe = tuple(item.strip().upper() for item in args.universe.split(",") if item.strip())
    if not universe:
        raise SystemExit("--universe must contain at least one symbol")

    try:
        artifact = build_artifact(
            args.dir,
            model_id=args.model_id,
            version=args.version,
            section=args.section,
            timeframe=args.timeframe,
            horizon_bars=args.horizon_bars,
            universe=universe,
            seed=args.seed,
            code_commit=args.code_commit or _git_commit(Path.cwd()),
            train_window=(args.train_start, args.train_end),
            validation_window=(args.validation_start, args.validation_end),
            artifact_file=args.model_file,
            metrics=_metrics(args.metrics),
        )
    except ArtifactError as error:
        raise SystemExit(str(error)) from error

    print(f"manifest  {args.dir / 'manifest.json'}")
    print(f"model     {args.model_file}")
    print(f"sha256    {artifact.content_hash}")
    print(f"commit    {artifact.code_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
