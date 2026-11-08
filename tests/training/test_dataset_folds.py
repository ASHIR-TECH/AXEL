"""Tests for timestamp-axis walk-forward folds and the holdout split."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axel.data.models import Bar
from axel.training.dataset import (
    load_bars_csv,
    prepare_folds,
    write_folds,
    write_holdout,
)

START = datetime(2024, 1, 1, tzinfo=UTC)


def _bars(symbol: str = "AAPL", *, count: int = 30) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timestamp=START + timedelta(hours=index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=1_000.0 + index,
            timeframe="1Hour",
        )
        for index in range(count)
    ]


def test_prepare_folds_never_overlap_train_and_test() -> None:
    bars = _bars("AAPL", count=40) + _bars("MSFT", count=40)
    plans, folds = prepare_folds(bars, train_size=10, test_size=5)

    assert plans
    for plan in plans:
        assert plan.train_end < plan.test_start
        assert plan.train_rows > 0
        assert plan.test_rows > 0

    for fold in folds:
        assert fold.train[1] == fold.test[0]
        assert set(range(*fold.train)).isdisjoint(range(*fold.test))


def test_prepare_folds_rejects_short_history() -> None:
    with pytest.raises(ValueError):
        prepare_folds(_bars(count=3), train_size=10, test_size=5)


def test_write_folds_and_holdout_round_trip(tmp_path: Path) -> None:
    bars = _bars("AAPL") + _bars("MSFT")
    plans, folds = prepare_folds(bars, train_size=10, test_size=5)
    manifest_path = write_folds(bars, plans, folds, tmp_path)

    payload = json.loads(manifest_path.read_text())
    assert payload["schema_version"] == "axel-folds-1"
    assert len(payload["folds"]) == len(plans)
    first = tmp_path / "fold_00"
    assert load_bars_csv(first / "train.csv")
    assert load_bars_csv(first / "test.csv")

    train_path, test_path = write_holdout(bars, tmp_path / "holdout", test_fraction=0.2)
    train_bars = load_bars_csv(train_path)
    test_bars = load_bars_csv(test_path)
    assert {bar.timestamp for bar in train_bars}.isdisjoint({bar.timestamp for bar in test_bars})
    assert {bar.symbol for bar in train_bars} == {"AAPL", "MSFT"}


def test_write_holdout_requires_two_timestamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_holdout(_bars(count=1), tmp_path)
