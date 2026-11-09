"""Tests for the Equibles daily-prices provider."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from axel.data.providers.equibles import EquiblesMarketData, parse_prices
from axel.data.providers.keypool import Credential, CredentialPool

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


def test_rotates_to_the_next_key_after_rate_limit() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["Authorization"]
        seen.append(auth)
        if auth == "Bearer eq_first":
            return httpx.Response(
                429,
                headers={"Retry-After": "30"},
                json={"error": "rate limit"},
            )
        return httpx.Response(200, json={"data": PAGE_ONE["data"], "meta": {"hasMore": False}})

    pool = CredentialPool(
        [Credential("eq_first", "eq_first"), Credential("eq_second", "eq_second")],
        cooldown_s=60,
    )
    provider = EquiblesMarketData(pool=pool, client=_client(handler))
    result = provider.fetch_bars("AAPL", START, END)

    assert seen == ["Bearer eq_first", "Bearer eq_second"]
    assert [record.close for record in result.records] == [316.22, 315.32]
    assert dict(result.raw.context)["credentials"] == "2"
    assert pool.available_count() == 1


def test_restarts_pagination_on_the_next_key_without_duplicates() -> None:
    pages = {"eq_first": 0, "eq_second": 0}
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        pages[token] += 1
        offset = request.url.params["offset"]
        seen.append((token, offset))
        if token == "eq_first" and pages[token] == 2:
            return httpx.Response(429, json={"error": "rate limit"})
        if offset == "0":
            return httpx.Response(200, json=PAGE_ONE)
        return httpx.Response(200, json=PAGE_TWO)

    pool = CredentialPool(
        [Credential("eq_first", "eq_first"), Credential("eq_second", "eq_second")]
    )
    provider = EquiblesMarketData(pool=pool, client=_client(handler), page_limit=2)
    result = provider.fetch_bars("AAPL", START, END)

    assert [record.close for record in result.records] == [316.22, 315.32]
    # The second key restarts from offset 0, then fetches the final page once.
    assert seen == [
        ("eq_first", "0"),
        ("eq_first", "2"),
        ("eq_second", "0"),
        ("eq_second", "2"),
    ]


def test_all_keys_exhausted_surfaces_the_429() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    pool = CredentialPool([Credential("eq_only", "eq_only")], cooldown_s=60)
    provider = EquiblesMarketData(pool=pool, client=_client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        provider.fetch_bars("AAPL", START, END)

