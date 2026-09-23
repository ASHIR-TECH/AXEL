from datetime import UTC, datetime

import httpx
import pytest

from axel.data.providers import AlpacaMarketData, FredData, SecEdgar


def test_alpaca_bars_use_read_only_market_data_endpoint() -> None:
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"bars": {"AAPL": [{"t": "2026-01-02T00:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 4}]}})
    client = AlpacaMarketData("key", "secret", httpx.Client(transport=httpx.MockTransport(handler)))
    bars = client.bars("AAPL", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC))
    assert bars[0].close == 2
    assert "/v2/stocks/bars" in seen["url"]


def test_provider_credentials_and_sec_contact_are_required() -> None:
    with pytest.raises(ValueError):
        FredData("")
    with pytest.raises(ValueError):
        SecEdgar("not-a-contact")
