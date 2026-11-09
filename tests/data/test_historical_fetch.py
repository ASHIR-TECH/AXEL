"""Tests for the provider-agnostic historical fetch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axel.data.historical import fetch_historical_bars, fetch_to_csv
from axel.data.models import Bar
from axel.training.artifact import sha256_file

START = datetime(2024, 1, 1, tzinfo=UTC)


def _bars(symbol: str = "AAPL", *, count: int = 3) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timestamp=START + timedelta(hours=index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=1_000.0,
            timeframe="1Hour",
        )
        for index in range(count)
    ]


class StubProvider:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.calls: list[tuple[str, str]] = []

    def bars(self, symbol, start, end, timeframe="1Hour"):
        self.calls.append((symbol, timeframe))
        return [bar for bar in self._bars if bar.symbol == symbol]


def test_fetch_historical_bars_is_sorted_and_checks_window() -> None:
    provider = StubProvider(_bars("AAPL") + _bars("MSFT"))
    bars = fetch_historical_bars(provider, ["MSFT", "AAPL"], START, START + timedelta(days=1))
    assert [(bar.symbol, bar.timestamp) for bar in bars] == sorted(
        (bar.symbol, bar.timestamp) for bar in bars
    )
    assert provider.calls == [("MSFT", "1Hour"), ("AAPL", "1Hour")]

    with pytest.raises(ValueError):
        fetch_historical_bars(provider, ["AAPL"], START, START)


def test_fetch_to_csv_returns_row_count_and_hash(tmp_path: Path) -> None:
    provider = StubProvider(_bars("AAPL"))
    rows, content_hash = fetch_to_csv(
        provider, ["AAPL"], START, START + timedelta(days=1), tmp_path / "bars.csv"
    )
    assert rows == 3
    assert content_hash == sha256_file(tmp_path / "bars.csv")
