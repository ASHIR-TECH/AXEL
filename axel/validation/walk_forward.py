"""Walk-forward / holdout splitting over an ordered timeline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Fold:
    """Half-open index ranges ``[start, end)`` for training and testing."""

    train: tuple[int, int]
    test: tuple[int, int]

    @property
    def train_slice(self) -> slice:
        return slice(*self.train)

    @property
    def test_slice(self) -> slice:
        return slice(*self.test)


def holdout_fold(n_periods: int, *, test_fraction: float = 0.3) -> Fold:
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be in (0, 1)")
    if n_periods < 2:
        raise ValueError("need at least two periods")
    cut = int(n_periods * (1.0 - test_fraction))
    cut = max(1, min(n_periods - 1, cut))
    return Fold(train=(0, cut), test=(cut, n_periods))


def walk_forward_folds(
    n_periods: int,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
) -> list[Fold]:
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size and test_size must be positive")
    stride = step or test_size
    if stride < 1:
        raise ValueError("step must be positive")
    folds: list[Fold] = []
    start = 0
    while start + train_size + test_size <= n_periods:
        folds.append(
            Fold(
                train=(start, start + train_size),
                test=(start + train_size, start + train_size + test_size),
            )
        )
        start += stride
    return folds


def fold_datetimes(
    folds: Sequence[Fold], timestamps: Sequence[datetime]
) -> list[tuple[datetime, datetime, datetime, datetime]]:
    """Map index folds to (train_start, train_end, test_start, test_end) datetimes."""
    windows = []
    for fold in folds:
        windows.append(
            (
                timestamps[fold.train[0]],
                timestamps[fold.train[1] - 1],
                timestamps[fold.test[0]],
                timestamps[fold.test[1] - 1],
            )
        )
    return windows
