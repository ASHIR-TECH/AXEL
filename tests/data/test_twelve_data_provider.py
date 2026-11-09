"""Tests for the Twelve Data OHLCV provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from axel.data.providers.base import ProviderDataError
from axel.data.providers.twelve_data import TwelveDataMarketData, parse_time_series

START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 10, tzinfo=UTC)

TIME_SERIES = {
    "meta": {
        "symbol": "AAPL",
        "interval": "1day",
        "exchange_timezone": "America/New_York",
        "currency": "USD",
        "type": "Common Stock",
    },
    "values": [
        {"datetime": "2026-10-02", "open": "333.69", "high": "335.0", "low": "332.0", "close": "333.69", "volume": "33278600"},
        {"datetime": "2026-10-01", "open": "330.32", "high": "331.0", "low": "329.0", "close": "330.32", "volume": "36306300"},
    ],
    "status": "ok",
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_time_series_sorts_and_filters_range() -> None:
    records = parse_time_series(json.dumps(TIME_SERIES), symbol="aapl", timeframe="1Day")

    assert [record.symbol for record in records] == ["AAPL", "AAPL"]
    assert [record.close for record in records] == [330.32, 333.69]
    assert records[0].event_time == datetime(2026, 10, 1, tzinfo=UTC)
    assert records[0].adjustment_status == "raw"
    assert records[0].provenance.source == "twelve-data"
    assert records[0].available_at > records[0].event_time


def test_parse_time_series_localizes_intraday_to_utc() -> None:
    body = json.dumps(
        {
            "meta": {"exchange_timezone": "America/New_York"},
            "values": [
                {"datetime": "2026-10-01 09:30:00", "open": "1", "high": "2", "low": "1", "close": "2", "volume": "10"}
            ],
            "status": "ok",
        }
    )
    records = parse_time_series(body, symbol="AAPL", timeframe="1Hour")
    # 09:30 New York (EDT, UTC-4) == 13:30 UTC.
    assert records[0].event_time == datetime(2026, 10, 1, 13, 30, tzinfo=UTC)


def test_parse_time_series_respects_adjustment_status() -> None:
    records = parse_time_series(
        json.dumps(TIME_SERIES),
        symbol="AAPL",
        adjustment_status="split_and_dividend_adjusted",
    )
    assert records[0].adjustment_status == "split_and_dividend_adjusted"


def test_parse_time_series_raises_on_error_status() -> None:
    body = json.dumps({"code": 404, "message": "invalid symbol", "status": "error"})
    with pytest.raises(ProviderDataError, match="Twelve Data returned status"):
        parse_time_series(body, symbol="NOPE")


def test_fetch_bars_sends_key_interval_and_adjustment() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["apikey"] == "td-test"
        assert request.url.params["symbol"] == "AAPL"
        assert request.url.params["interval"] == "1day"
        assert request.url.params["adjust"] == "splits"
        assert request.url.params["order"] == "ASC"
        return httpx.Response(200, json=TIME_SERIES)

    provider = TwelveDataMarketData("td-test", _client(handler), adjustment="splits")
    result = provider.fetch_bars("AAPL", START, END)

    assert len(calls) == 1
    assert [record.close for record in result.records] == [330.32, 333.69]
    assert result.records[0].adjustment_status == "split_adjusted"
    assert dict(result.raw.context)["adjustment"] == "splits"
    assert result.raw.content_hash


def test_fetch_bars_maps_to_legacy_bar_model() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TIME_SERIES)

    provider = TwelveDataMarketData("td-test", _client(handler))
    bars = provider.bars("AAPL", START, END)
    assert bars[0].symbol == "AAPL"
    assert bars[-1].close == 333.69


def test_fetch_bars_rejects_unknown_timeframe() -> None:
    provider = TwelveDataMarketData("td-test", _client(lambda _r: httpx.Response(500)))
    with pytest.raises(ValueError):
        provider.fetch_bars("AAPL", START, END, timeframe="2Hour")


def test_requires_api_key_and_valid_adjustment() -> None:
    with pytest.raises(ValueError):
        TwelveDataMarketData("")
    with pytest.raises(ValueError):
        TwelveDataMarketData("td-test", adjustment="bogus")
