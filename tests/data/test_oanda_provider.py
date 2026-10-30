import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from axel.data.providers import Capability, CapabilityError, OandaMarketData

NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def _provider(handler, **kwargs) -> OandaMarketData:
    return OandaMarketData(
        "token",
        account_id="acct",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_backoff_seconds=0,
        **kwargs,
    )


def test_oanda_instruments_map_pair_conventions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/accounts/acct/instruments"
        return httpx.Response(
            200,
            json={
                "instruments": [
                    {
                        "name": "EUR_USD",
                        "type": "CURRENCY",
                        "displayName": "EUR/USD",
                        "pipLocation": -4,
                        "tradeUnitsPrecision": 0,
                        "minimumTradeSize": "1",
                        "marginRate": "0.02",
                    },
                    {"name": "XAU_USD", "type": "METAL"},
                ]
            },
        )

    async def run():
        provider = _provider(handler)
        instruments = await provider.get_instruments()
        await provider.close()
        return instruments

    instruments = asyncio.run(run())
    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.instrument_id == "forex:oanda:EUR_USD"
    assert instrument.base_currency == "EUR"
    assert instrument.quote_currency == "USD"
    assert instrument.tick_size == pytest.approx(0.00001)
    assert instrument.trading_calendar == "FX_24_5"
    assert instrument.extra_dict["margin_rate"] == "0.02"


def test_oanda_candles_are_mid_priced_and_available_after_close() -> None:
    candle_time = NOW - timedelta(hours=1)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/instruments/EUR_USD/candles"
        assert request.url.params["count"] == "5000"
        return httpx.Response(
            200,
            json={
                "candles": [
                    {
                        "complete": True,
                        "time": "2026-01-05T14:00:00.000000000Z",
                        "volume": 100,
                        "mid": {"o": "1.10000", "h": "1.10100", "l": "1.09900", "c": "1.10050"},
                    },
                    {
                        "complete": False,
                        "time": "2026-01-05T15:00:00.000000000Z",
                        "volume": 10,
                        "mid": {"o": "1.10050", "h": "1.10060", "l": "1.10040", "c": "1.10055"},
                    },
                ]
            },
        )

    async def run():
        provider = _provider(handler)
        bars = await provider.get_historical_bars(
            "forex:oanda:EUR_USD", candle_time, NOW, interval="1h"
        )
        await provider.close()
        return bars

    bars = asyncio.run(run())
    assert len(bars) == 1
    assert bars[0].event_time == candle_time
    assert bars[0].available_at == candle_time + timedelta(hours=1)
    assert bars[0].close == 1.1005
    assert "mid_price" in bars[0].provenance.quality_flags


def test_oanda_quote_has_bid_ask_and_liquidity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/accounts/acct/pricing"
        return httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "instrument": "EUR_USD",
                        "time": "2026-01-05T15:00:00.000000000Z",
                        "bids": [{"price": "1.10000", "liquidity": 1000000}],
                        "asks": [{"price": "1.10010", "liquidity": 900000}],
                    }
                ]
            },
        )

    async def run():
        provider = _provider(handler)
        quote = await provider.get_quote("forex:oanda:EUR_USD")
        await provider.close()
        return quote

    quote = asyncio.run(run())
    assert quote.bid == 1.1
    assert quote.ask == 1.1001
    assert quote.bid_size == 1_000_000
    assert quote.spread_bps > 0


def test_oanda_health_and_unsupported_capabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candles": []})

    async def run():
        provider = _provider(handler)
        health = await provider.health_check()
        await provider.close()
        return provider, health

    provider, health = asyncio.run(run())
    assert health.healthy is True
    assert Capability.QUOTES in provider.capabilities
    with pytest.raises(CapabilityError, match="order_book"):
        asyncio.run(provider.get_order_book("forex:oanda:EUR_USD"))


def test_oanda_requires_token_and_account_for_account_scoped_calls() -> None:
    with pytest.raises(ValueError, match="token"):
        OandaMarketData("")
    provider = OandaMarketData("token")
    with pytest.raises(ValueError, match="account_id"):
        asyncio.run(provider.get_instruments())


def test_oanda_rejects_monthly_candles_instead_of_guessing_availability() -> None:
    provider = OandaMarketData("token")

    async def run():
        with pytest.raises(ValueError, match="unsupported OANDA interval"):
            await provider.get_historical_bars(
                "forex:oanda:EUR_USD", NOW - timedelta(days=90), NOW, interval="1M"
            )
        await provider.close()

    asyncio.run(run())
