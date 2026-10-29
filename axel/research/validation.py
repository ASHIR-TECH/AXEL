"""Time-series validation protocols for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


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


def purged_embargo_split(
    dates: list[datetime],
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    purge_days: int = 5,
    embargo_days: int = 2,
) -> tuple[list[datetime], list[datetime], list[datetime]]:
    """Purged and embargoed chronological split to prevent label leakage across folds.
    
    Purge: drops the last N days of the training set immediately preceding validation/test to
    prevent overlapping labels or holding periods from leaking forward.
    Embargo: drops the first M days of the validation/test set following a prior period to
    prevent lingering auto-regressive state leakage.
    """
    n = len(dates)
    if n == 0:
        return [], [], []

    sorted_dates = sorted(dates)
    raw_train, raw_val, raw_test = time_split(sorted_dates, train_ratio, val_ratio)

    if not raw_train or not raw_val:
        return raw_train, raw_val, raw_test

    # 1. Purge training end relative to validation start
    val_start_dt = raw_val[0]
    purge_delta = timedelta(days=purge_days)
    purged_train = [d for d in raw_train if (val_start_dt - d) > purge_delta]

    # 2. Embargo validation start relative to training end
    train_end_dt = raw_train[-1]
    embargo_delta = timedelta(days=embargo_days)
    embargoed_val = [d for d in raw_val if (d - train_end_dt) > embargo_delta]

    # 3. Purge/embargo between validation and test if test exists
    if raw_test and embargoed_val:
        test_start_dt = raw_test[0]
        purged_val = [d for d in embargoed_val if (test_start_dt - d) > purge_delta]
        val_end_dt = raw_val[-1]
        embargoed_test = [d for d in raw_test if (d - val_end_dt) > embargo_delta]
    else:
        purged_val = embargoed_val
        embargoed_test = raw_test

    return purged_train, purged_val, embargoed_test


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
        folds.append({
            "fold": len(folds),
            "train": train,
            "test": test,
            "train_size": len(train),
            "test_size": len(test),
        })
    return folds


def perturb_parameters(
    base_params: dict[str, Any],
    perturbation_ratio: float = 0.2,
) -> list[dict[str, Any]]:
    """Generate parameter variants perturbing numeric hyperparameters by +/- ratio (PRD §6.6)."""
    variants: list[dict[str, Any]] = [dict(base_params)]

    for param_name, param_val in base_params.items():
        if isinstance(param_val, (int, float)) and not isinstance(param_val, bool):
            for multiplier in (1.0 + perturbation_ratio, 1.0 - perturbation_ratio):
                new_dict = dict(base_params)
                if isinstance(param_val, int):
                    new_val = max(1, round(param_val * multiplier))
                else:
                    new_val = round(param_val * multiplier, 6)
                new_dict[param_name] = new_val
                if new_dict not in variants:
                    variants.append(new_dict)

    return variants
