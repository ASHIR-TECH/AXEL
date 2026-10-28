"""Time-series validation protocols for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ValidationProtocol:
    protocol_id: str
    method: str
    train_ratio: float = 0.6
    val_ratio: float = 0.2
    test_ratio: float = 0.2
    purge_days: int = 0
    embargo_days: int = 0
    min_train_samples: int = 252
    min_test_samples: int = 60


def time_split(dates: list[datetime], train_ratio: float = 0.6, val_ratio: float = 0.2):
    n = len(dates)
    if n == 0:
        return [], [], []
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))
    return dates[:train_end], dates[train_end:val_end], dates[val_end:]


def purged_embargo_split(dates: list[datetime], train_ratio: float = 0.6, val_ratio: float = 0.2, purge_days: int = 5, embargo_days: int = 2):
    train, val, test = time_split(dates, train_ratio, val_ratio)
    return train, val, test


def holdout_split(dates: list[datetime], holdout_ratio: float = 0.2):
    n = len(dates)
    if n == 0:
        return [], []
    split = int(n * (1 - holdout_ratio))
    return dates[:split], dates[split:]


def walk_forward_evaluate(dates: list[datetime], window_size: int = 252, step_size: int = 63):
    folds = []
    for i in range(0, len(dates) - window_size, step_size):
        train = dates[i : i + window_size]
        test_start = i + window_size
        test_end = min(test_start + step_size, len(dates))
        test = dates[test_start:test_end]
        if len(test) == 0:
            break
        folds.append({"fold": len(folds), "train": train, "test": test, "train_size": len(train), "test_size": len(test)})
    return folds
