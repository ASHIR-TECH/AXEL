"""Tests for the Qlib export used by the Kronos fine-tune."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axel.data.models import Bar
from axel.training.qlib import (
    QLIB_COLUMNS,
    dump_bin_command,
    dump_qlib_bin,
    typical_price,
    write_qlib_csvs,
)

START = datetime(2024, 1, 2, tzinfo=UTC)


def _bars(symbol: str = "AAPL", *, count: int = 3) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timestamp=START + timedelta(days=index),
            open=100.0 + index,
            high=102.0 + index,
            low=99.0 + index,
            close=101.0 + index,
            volume=1_000.0 + index,
            timeframe="1Day",
        )
        for index in range(count)
    ]


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_write_qlib_csvs_uses_qlib_schema(tmp_path: Path) -> None:
    counts = write_qlib_csvs(_bars("aapl") + _bars("MSFT", count=2), tmp_path)

    assert counts == {"AAPL": 3, "MSFT": 2}
    rows = _rows(tmp_path / "AAPL.csv")
    assert tuple(rows[0]) == QLIB_COLUMNS
    assert rows[0]["date"] == "2024-01-02"
    assert rows[0]["factor"] == "1.0"
    assert float(rows[0]["vwap"]) == pytest.approx(100.5)


def test_intraday_freq_keeps_time(tmp_path: Path) -> None:
    write_qlib_csvs(_bars(count=1), tmp_path, freq="1h")
    assert _rows(tmp_path / "AAPL.csv")[0]["date"] == "2024-01-02 00:00:00"


def test_typical_price_is_ohlc_mean() -> None:
    bar = _bars(count=1)[0]
    assert typical_price(bar) == pytest.approx((100.0 + 102.0 + 99.0 + 101.0) / 4)


def test_dump_bin_command_is_explicit(tmp_path: Path) -> None:
    command = dump_bin_command(tmp_path / "csv", tmp_path / "qlib", tmp_path / "dump_bin.py")
    assert "dump_all" in command
    assert command[command.index("--freq") + 1] == "day"
    assert command[command.index("--date_field_name") + 1] == "date"


def test_dump_qlib_bin_requires_script(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        dump_qlib_bin(tmp_path, tmp_path / "qlib", tmp_path / "missing.py")
