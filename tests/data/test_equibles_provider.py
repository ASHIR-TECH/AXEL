"""Tests for the Equibles daily-prices provider."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from axel.data.providers.equibles import EquiblesMarketData, parse_prices

START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 7, 31, tzinfo=UTC)

PAGE_ONE = {
    "data": [
        {"date": "2026-07-10", "open": 314.72, "high": 316.91, "low": 312.17, "close": 315.32, "volume": 34109200},
        {"date": "2026-07-09", "open": 310.51, "high": 316.53, "low": 308.16, "close": 316.22, "volume": 48084300},
    ],
    "meta": {"limit": 2, "offset": 0, "count": 2, "hasMore": True},
}
PAGE_TWO = {
    "data": [
        {"date": "2026-06-30", "open": 300.0, "high": 301.0, "low": 299.0, "close": 300.5, "volume": 1000},
    ],
    "meta": {"limit": 2, "offset": 2, "count": 1, "hasMore": False},
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_prices_converts_daily_rows_and_filters_range() -> None:
    import json

    records = parse_prices(
        json.dumps(PAGE_ONE),
        symbol="aapl",
        start=START,
        end=END,
    )

    assert [record.symbol for record in records] == ["AAPL", "AAPL"]
    assert [record.close for record in records] == [316.22, 315.32]  # ascending
    assert records[0].event_time == datetime(2026, 7, 9, tzinfo=UTC)
    assert records[0].available_at > records[0].event_time
    assert records[0].provenance.source == "equibles"


def test_fetch_bars_paginates_and_uses_bearer_auth() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer eq_test"
        assert "/v1/stocks/AAPL/prices" in str(request.url)
        assert request.url.params["startDate"] == "2026-07-01"
        assert request.url.params["endDate"] == "2026-07-31"
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json=PAGE_ONE)
        return httpx.Response(200, json=PAGE_TWO)

    provider = EquiblesMarketData("eq_test", _client(handler), page_limit=2)
    result = provider.fetch_bars("AAPL", START, END)

    assert len(calls) == 2
    assert [record.close for record in result.records] == [316.22, 315.32]
    assert dict(result.raw.context)["pages"] == "2"
    assert result.raw.content_hash


def test_fetch_bars_maps_to_legacy_bar_model() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": PAGE_ONE["data"], "meta": {"hasMore": False}})

    provider = EquiblesMarketData("eq_test", _client(handler))
    bars = provider.bars("AAPL", START, END)
    assert bars[0].symbol == "AAPL"
    assert bars[-1].close == 315.32


def test_fetch_bars_rejects_intraday() -> None:
    provider = EquiblesMarketData("eq_test", _client(lambda _r: httpx.Response(500)))
    with pytest.raises(ValueError):
        provider.fetch_bars("AAPL", START, END, timeframe="1Hour")


def test_requires_api_key() -> None:
    with pytest.raises(ValueError):
        EquiblesMarketData("")
