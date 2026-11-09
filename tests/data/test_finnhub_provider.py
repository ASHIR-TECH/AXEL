"""Tests for the Finnhub news, quote and candle provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from axel.data.providers.base import ProviderDataError
from axel.data.providers.finnhub import (
    FinnhubMarketData,
    parse_candles,
    parse_company_news,
)

START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 10, tzinfo=UTC)

NEWS = [
    {
        "category": "company",
        "datetime": 1791547200,
        "headline": "Apple ships new iPhone as $AAPL momentum builds",
        "id": 142795813,
        "related": "AAPL",
        "source": "Yahoo",
        "summary": "Summary text.",
        "url": "https://example.com/aapl-news",
    },
    {
        "category": "company",
        "datetime": 1791028800,
        "headline": "Older item",
        "id": 1,
        "related": "",
        "source": "Wire",
        "summary": "",
        "url": "https://example.com/older",
    },
]

CANDLES = {
    "c": [333.69, 330.32],
    "h": [335.0, 331.0],
    "l": [332.0, 329.0],
    "o": [333.69, 330.32],
    "s": "ok",
    "t": [1790913600, 1790827200],
    "v": [33278600, 36306300],
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_company_news_maps_fields_entities_and_range() -> None:
    records = parse_company_news(
        json.dumps(NEWS), symbol="AAPL", start=START, end=END
    )

    assert len(records) == 2
    newest = records[-1]
    assert newest.publisher == "Yahoo"
    assert newest.title.startswith("Apple ships")
    assert newest.body_ref == "https://example.com/aapl-news"
    assert "AAPL" in newest.entities
    assert newest.provenance.source == "finnhub"
    assert [record.event_time for record in records] == sorted(
        record.event_time for record in records
    )


def test_fetch_news_uses_token_header_and_date_params() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["X-Finnhub-Token"] == "fh-test"
        assert request.url.params["symbol"] == "AAPL"
        assert request.url.params["from"] == "2026-10-01"
        assert request.url.params["to"] == "2026-10-10"
        return httpx.Response(200, json=NEWS)

    provider = FinnhubMarketData("fh-test", _client(handler))
    result = provider.fetch_news("AAPL", START, END)

    assert len(calls) == 1
    assert len(result.records) == 2
    assert result.raw.source == "finnhub"
    assert "fh-test" not in result.raw.request


def test_quote_parses_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Finnhub-Token"] == "fh-test"
        return httpx.Response(
            200,
            json={
                "c": 342.37,
                "d": -1.8,
                "dp": -0.523,
                "h": 345.79,
                "l": 338.3,
                "o": 338.73,
                "pc": 344.17,
                "t": 1793998800,
            },
        )

    quote = FinnhubMarketData("fh-test", _client(handler)).quote("AAPL")
    assert quote.symbol == "AAPL"
    assert quote.price == pytest.approx(342.37)
    assert quote.percent_change == pytest.approx(-0.523)
    assert quote.as_of.tzinfo is not None


def test_parse_candles_sorts_and_marks_raw() -> None:
    records = parse_candles(json.dumps(CANDLES), symbol="aapl", timeframe="1Day")
    assert [record.close for record in records] == [330.32, 333.69]
    assert all(record.adjustment_status == "raw" for record in records)
    assert all(record.provenance.source == "finnhub" for record in records)


def test_parse_candles_handles_no_data() -> None:
    assert parse_candles(json.dumps({"s": "no_data"}), symbol="AAPL") == []


def test_parse_candles_raises_on_mismatched_arrays() -> None:
    body = json.dumps({"s": "ok", "t": [1], "o": [1], "h": [2], "l": [1], "c": [2], "v": []})
    with pytest.raises(ProviderDataError, match="mismatched lengths"):
        parse_candles(body, symbol="AAPL")


def test_fetch_bars_premium_gate_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "You don't have access to this resource."})

    provider = FinnhubMarketData("fh-test", _client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        provider.fetch_bars("AAPL", START, END)


def test_fetch_bars_rejects_unknown_timeframe() -> None:
    provider = FinnhubMarketData("fh-test", _client(lambda _r: httpx.Response(500)))
    with pytest.raises(ValueError):
        provider.fetch_bars("AAPL", START, END, timeframe="2Hour")


def test_requires_api_key() -> None:
    with pytest.raises(ValueError):
        FinnhubMarketData("")
