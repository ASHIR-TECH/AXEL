"""Tests for the Massive (formerly Polygon.io) aggregate-bars provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from axel.data.providers.massive import MassiveMarketData, parse_aggregates

START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 10, tzinfo=UTC)

DAY_ONE_MS = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)
DAY_TWO_MS = int(datetime(2026, 10, 2, tzinfo=UTC).timestamp() * 1000)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _row(ts_ms: int, close: float) -> dict[str, float]:
    return {
        "t": ts_ms,
        "o": close - 1,
        "h": close + 1,
        "l": close - 2,
        "c": close,
        "v": 1000.0,
        "vw": close,
    }


def test_parse_aggregates_sorts_and_marks_split_adjusted() -> None:
    body = json.dumps(
        {"status": "OK", "results": [_row(DAY_TWO_MS, 101.0), _row(DAY_ONE_MS, 100.0)]}
    )
    records = parse_aggregates(body, symbol="aapl", timeframe="1Day")

    assert [record.symbol for record in records] == ["AAPL", "AAPL"]
    assert [record.close for record in records] == [100.0, 101.0]
    assert records[0].event_time == datetime(2026, 10, 1, tzinfo=UTC)
    assert records[0].adjustment_status == "split_adjusted"
    assert records[0].provenance.source == "massive"
    assert records[0].available_at > records[0].event_time


def test_fetch_bars_uses_api_key_query_and_daily_range() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["apiKey"] == "massive-test"
        assert request.url.params["adjusted"] == "true"
        assert request.url.params["sort"] == "asc"
        assert "/v2/aggs/ticker/AAPL/range/1/day/2026-10-01/2026-10-10" in str(request.url)
        return httpx.Response(
            200,
            json={"status": "OK", "results": [_row(DAY_ONE_MS, 100.0)]},
        )

    provider = MassiveMarketData("massive-test", _client(handler))
    result = provider.fetch_bars("AAPL", START, END)

    assert len(calls) == 1
    assert [record.close for record in result.records] == [100.0]
    assert dict(result.raw.context)["pages"] == "1"
    assert result.raw.content_hash


def test_fetch_bars_follows_next_url_and_keeps_the_key() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.params["apiKey"] == "massive-test"
        if "cursor=" not in str(request.url):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "results": [_row(DAY_ONE_MS, 100.0)],
                    "next_url": "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/2026-10-01/2026-10-10?cursor=abc",
                },
            )
        return httpx.Response(
            200,
            json={"status": "OK", "results": [_row(DAY_TWO_MS, 101.0)]},
        )

    provider = MassiveMarketData("massive-test", _client(handler))
    result = provider.fetch_bars("AAPL", START, END)

    assert len(calls) == 2
    assert [record.close for record in result.records] == [100.0, 101.0]
    assert dict(result.raw.context)["pages"] == "2"


def test_fetch_bars_raises_on_error_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "ERROR", "error": "invalid ticker", "results": []},
        )

    provider = MassiveMarketData("massive-test", _client(handler))
    with pytest.raises(RuntimeError, match="Massive returned status"):
        provider.fetch_bars("NOPE", START, END)


def test_fetch_bars_rejects_unknown_timeframe() -> None:
    provider = MassiveMarketData("massive-test", _client(lambda _r: httpx.Response(500)))
    with pytest.raises(ValueError):
        provider.fetch_bars("AAPL", START, END, timeframe="2Hour")


def test_requires_api_key() -> None:
    with pytest.raises(ValueError):
        MassiveMarketData("")


def test_unadjusted_marks_records_raw() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "OK", "results": [_row(DAY_ONE_MS, 100.0)]})

    provider = MassiveMarketData("massive-test", _client(handler), adjusted=False)
    result = provider.fetch_bars("AAPL", START, END)
    assert result.records[0].adjustment_status == "raw"
