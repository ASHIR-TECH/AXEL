from datetime import UTC, datetime

import httpx

from axel.data.providers import AlpacaMarketData, FredData, GdeltNews, SecEdgar


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_alpaca_fetch_bars_are_canonical_and_available_after_close() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/v2/stocks/bars" in str(request.url)
        return httpx.Response(
            200,
            json={
                "bars": {
                    "AAPL": [
                        {"t": "2026-01-02T00:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 4}
                    ]
                }
            },
        )

    provider = AlpacaMarketData("key", "secret", _client(handler))
    result = provider.fetch_bars(
        "AAPL", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)
    )
    record = result.records[0]
    assert record.symbol == "AAPL"
    assert record.venue == "alpaca-iex"
    assert record.available_at > record.event_time
    assert record.adjustment_status == "raw"
    assert result.raw.content_hash

    legacy = provider.bars(
        "AAPL", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)
    )
    assert legacy[0].close == 2


def test_fred_fetch_observations_carry_vintage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2025-12-01", "value": "3.9", "realtime_start": "2026-01-15", "realtime_end": "2026-01-15"},
                    {"date": "2025-12-02", "value": ".", "realtime_start": "2026-01-15", "realtime_end": "2026-01-15"},
                ]
            },
        )

    provider = FredData("key", _client(handler))
    result = provider.fetch_observations("DFF", realtime_as_of="2026-01-15")
    assert len(result.records) == 1
    record = result.records[0]
    assert record.series_id == "DFF"
    assert record.value == 3.9
    assert record.available_at > record.event_time
    assert record.vintage == "2026-01-15"


def test_sec_fetch_filings_use_public_filing_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "CIK0000320193" in str(request.url)
        return httpx.Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": ["10-Q"],
                        "filingDate": ["2026-02-01"],
                        "reportDate": ["2025-12-31"],
                        "accessionNumber": ["0000320193-26-000001"],
                        "primaryDocument": ["aapl-20251231.htm"],
                        "primaryDocDescription": ["10-Q"],
                    }
                }
            },
        )

    provider = SecEdgar("Analyst analyst@example.com", _client(handler))
    result = provider.fetch_filings("320193")
    record = result.records[0]
    assert record.form == "10-Q"
    assert record.issuer_id == "0000320193"
    assert record.available_at > record.event_time


def test_news_articles_are_data_with_entity_hints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "url": "https://example.com/a",
                        "title": "$AAPL beats estimates",
                        "seendate": "20260102T120000Z",
                        "domain": "example.com",
                    }
                ]
            },
        )

    provider = GdeltNews(_client(handler))
    result = provider.fetch_articles("AAPL", max_records=1)
    record = result.records[0]
    assert record.publisher == "example.com"
    assert "AAPL" in record.entities
    assert record.event_time == record.available_at
