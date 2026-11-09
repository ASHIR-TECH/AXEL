"""Deterministic fold preparation for model training.

Splits are made on the union of timestamps, never per symbol and never
randomly. A bar whose timestamp is in the test window is excluded from the
training window even if it belongs to another symbol, so cross-sectional
leakage cannot occur.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from axel.data.export import write_bars_csv
from axel.data.ingest.ohlcv import parse_ohlcv_csv
from axel.data.models import Bar
from axel.validation.walk_forward import Fold, walk_forward_folds


@dataclass(frozen=True)
class FoldPlan:
    """A single train/test split, described by wall-clock windows and row counts."""

    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_rows: int
    test_rows: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_bars_csv(path: Path) -> list[Bar]:
    return parse_ohlcv_csv(path.read_text(encoding="utf-8"), source=path.name)


def _timestamps(bars: list[Bar]) -> list[datetime]:
    return sorted({bar.timestamp for bar in bars})


def prepare_folds(
    bars: list[Bar],
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
) -> tuple[list[FoldPlan], list[Fold]]:
    """Build walk-forward folds over the shared timestamp axis."""
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size and test_size must be positive")
    stamps = _timestamps(bars)
    folds = walk_forward_folds(len(stamps), train_size=train_size, test_size=test_size, step=step)
    if not folds:
        raise ValueError(
            f"not enough timestamps ({len(stamps)}) for train_size={train_size}, "
            f"test_size={test_size}"
        )
    plans: list[FoldPlan] = []
    for index, fold in enumerate(folds):
        train_stamps = stamps[fold.train[0]:fold.train[1]]
        test_stamps = stamps[fold.test[0]:fold.test[1]]
        train_set, test_set = set(train_stamps), set(test_stamps)
        plans.append(
            FoldPlan(
                index=index,
                train_start=train_stamps[0].isoformat(),
                train_end=train_stamps[-1].isoformat(),
                test_start=test_stamps[0].isoformat(),
                test_end=test_stamps[-1].isoformat(),
                train_rows=sum(1 for bar in bars if bar.timestamp in train_set),
                test_rows=sum(1 for bar in bars if bar.timestamp in test_set),
            )
        )
    return plans, folds


def write_folds(
    bars: list[Bar],
    plans: list[FoldPlan],
    folds: list[Fold],
    out_dir: Path,
    *,
    holdout: dict[str, object] | None = None,
) -> Path:
    """Write ``fold_NN/train.csv``, ``fold_NN/test.csv`` and ``folds.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamps = _timestamps(bars)
    for plan, fold in zip(plans, folds, strict=True):
        train_set = set(stamps[fold.train[0]:fold.train[1]])
        test_set = set(stamps[fold.test[0]:fold.test[1]])
        fold_dir = out_dir / f"fold_{plan.index:02d}"
        write_bars_csv([bar for bar in bars if bar.timestamp in train_set], fold_dir / "train.csv")
        write_bars_csv([bar for bar in bars if bar.timestamp in test_set], fold_dir / "test.csv")
    manifest: dict[str, object] = {
        "schema_version": "axel-folds-1",
        "timestamp_count": len(stamps),
        "symbols": sorted({bar.symbol for bar in bars}),
        "folds": [plan.to_dict() for plan in plans],
    }
    if holdout is not None:
        manifest["holdout"] = holdout
    path = out_dir / "folds.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_holdout(
    bars: list[Bar],
    out_dir: Path,
    *,
    test_fraction: float = 0.2,
) -> tuple[Path, Path]:
    """Write a final holdout split that must never be used for tuning."""
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be in (0, 1)")
    stamps = _timestamps(bars)
    if len(stamps) < 2:
        raise ValueError("need at least two distinct timestamps for a holdout split")
    cut = max(1, min(len(stamps) - 1, int(len(stamps) * (1.0 - test_fraction))))
    train_set, test_set = set(stamps[:cut]), set(stamps[cut:])
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "holdout_train.csv"
    test_path = out_dir / "holdout_test.csv"
    write_bars_csv([bar for bar in bars if bar.timestamp in train_set], train_path)
    write_bars_csv([bar for bar in bars if bar.timestamp in test_set], test_path)
    return train_path, test_path


__all__ = [
    "FoldPlan",
    "load_bars_csv",
    "prepare_folds",
    "write_bars_csv",
    "write_folds",
    "write_holdout",
]
