"""Tests for the reproducible bars CSV export."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axel.data.export import export_dataset, write_bars_csv
from axel.data.models import Bar
from axel.db.models import OhlcvBar

START = datetime(2024, 1, 1, tzinfo=UTC)


def _bars(symbol: str = "AAPL", *, count: int = 4, timeframe: str = "1Hour") -> list[Bar]:
    bars = []
    for index in range(count):
        price = 100.0 + index
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=START + timedelta(hours=index),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1_000.0 + index,
                timeframe=timeframe,
            )
        )
    return bars


def _insert_bars(session, bars: list[Bar]) -> None:
    for index, bar in enumerate(bars):
        session.add(
            OhlcvBar(
                id=f"bar_{bar.symbol}_{index}",
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                timeframe=bar.timeframe,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
        )
    session.commit()


def test_write_bars_csv_is_deterministic_and_hash_stable(tmp_path: Path) -> None:
    bars = _bars(count=5)
    first = write_bars_csv(bars, tmp_path / "a.csv")
    second = write_bars_csv(list(reversed(bars)), tmp_path / "b.csv")
    assert first == second
    assert (tmp_path / "a.csv").read_text() == (tmp_path / "b.csv").read_text()


def test_export_dataset_writes_manifest(in_memory_db, tmp_path: Path) -> None:
    _insert_bars(in_memory_db, _bars("AAPL") + _bars("MSFT"))
    manifest = export_dataset(in_memory_db, tmp_path)

    assert manifest.row_count == 8
    assert manifest.symbols == ("AAPL", "MSFT")
    assert manifest.timeframes == ("1Hour",)
    assert manifest.content_sha256
    payload = json.loads((tmp_path / "manifest.json").read_text())
    assert payload["schema_version"] == "ohlcv-csv-1"
    assert payload["row_count"] == 8
    assert (tmp_path / "bars.csv").exists()


def test_export_dataset_filters_by_symbol_and_window(in_memory_db, tmp_path: Path) -> None:
    _insert_bars(in_memory_db, _bars("AAPL", count=10) + _bars("MSFT", count=10))
    manifest = export_dataset(
        in_memory_db,
        tmp_path,
        symbols=["AAPL"],
        start=START + timedelta(hours=2),
        end=START + timedelta(hours=4),
    )
    assert manifest.symbols == ("AAPL",)
    assert manifest.row_count == 3
