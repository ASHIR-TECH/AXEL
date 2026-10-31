import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from axel.data.instruments import AssetClass
from axel.data.providers import BinancePublicMarketData, Capability, CapabilityError
from axel.data.providers.base import CapabilityAwareProvider, ProviderCapabilities

NOW = datetime.now(UTC).replace(microsecond=0)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(handler, **kwargs) -> BinancePublicMarketData:
    return BinancePublicMarketData(
        client=_client(handler),
        retry_backoff_seconds=0,
        **kwargs,
    )


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def test_instruments_are_canonical_and_capability_aware() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/exchangeInfo"
        return httpx.Response(
            200,
            json={
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "0.0001",
                                "minQty": "0.0001",
                            },
                            {"filterType": "NOTIONAL", "minNotional": "5"},
                        ],
                    },
                    {"symbol": "DEADUSDT", "status": "BREAK", "baseAsset": "DEAD"},
                ]
            },
        )

    async def run():
        provider = _provider(handler)
        instruments = await provider.get_instruments()
        await provider.close()
        return provider, instruments

    provider, instruments = asyncio.run(run())
    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.instrument_id == "crypto:binance:BTCUSDT"
    assert instrument.asset_class is AssetClass.CRYPTO
    assert instrument.base_currency == "BTC"
    assert instrument.quote_currency == "USDT"
    assert instrument.tick_size == 0.01
    assert instrument.lot_size == 0.0001
    assert instrument.extra_dict["notional_min"] == "5"
    assert Capability.FUNDING_RATES in provider.capabilities


def test_historical_bars_use_close_time_and_drop_forming_candle() -> None:
    closed_open = NOW - timedelta(hours=2)
    closed_close = NOW - timedelta(hours=1)
    forming_open = NOW - timedelta(minutes=5)
    forming_close = NOW + timedelta(minutes=55)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/klines"
        return httpx.Response(
            200,
            json=[
                [
                    _ms(closed_open),
                    "100.0",
                    "102.0",
                    "99.0",
                    "101.0",
                    "10.0",
                    _ms(closed_close),
                    "1000.0",
                    5,
                    "6.0",
                    "600.0",
                    "0",
                ],
                [
                    _ms(forming_open),
                    "101.0",
                    "103.0",
                    "100.0",
                    "102.0",
                    "5.0",
                    _ms(forming_close),
                    "500.0",
                    2,
                    "3.0",
                    "300.0",
                    "0",
                ],
            ],
        )

    async def run():
        provider = _provider(handler)
        bars = await provider.get_historical_bars(
            "crypto:binance:BTCUSDT", NOW - timedelta(days=1), NOW, interval="1h"
        )
        await provider.close()
        return bars

    bars = asyncio.run(run())
    assert len(bars) == 1
    bar = bars[0]
    assert bar.event_time == closed_open
    assert bar.available_at == closed_close + timedelta(milliseconds=1)
    assert bar.instrument_id == "crypto:binance:BTCUSDT"
    assert bar.close == 101.0


def test_trades_preserve_aggressor_side_and_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/aggTrades"
        return httpx.Response(
            200,
            json=[
                {"a": 10, "p": "50000.0", "q": "0.1", "T": _ms(NOW - timedelta(seconds=5)), "m": True},
                {"a": 11, "p": "50001.0", "q": "0.2", "T": _ms(NOW - timedelta(seconds=4)), "m": False},
            ],
        )

    async def run():
        provider = _provider(handler)
        trades = await provider.get_trades(
            "crypto:binance:BTCUSDT", NOW - timedelta(minutes=1), NOW
        )
        await provider.close()
        return trades

    trades = asyncio.run(run())
    assert [trade.trade_id for trade in trades] == ["10", "11"]
    assert trades[0].side.value == "sell"
    assert trades[1].side.value == "buy"
    assert trades[0].price == 50_000.0


def test_order_book_sequence_and_depth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/depth"
        return httpx.Response(
            200,
            json={
                "lastUpdateId": 123,
                "bids": [["100.0", "2.0"], ["99.0", "3.0"]],
                "asks": [["101.0", "1.0"], ["102.0", "4.0"]],
            },
        )

    async def run():
        provider = _provider(handler)
        book = await provider.get_order_book("crypto:binance:BTCUSDT", depth=2)
        await provider.close()
        return book

    book = asyncio.run(run())
    assert book.sequence == 123
    assert book.best_bid == 100.0
    assert book.best_ask == 101.0
    assert book.imbalance(1) == pytest.approx((200 - 101) / (200 + 101))


def test_funding_and_open_interest_are_point_in_time() -> None:
    funding_time = NOW - timedelta(hours=8)
    oi_time = NOW - timedelta(minutes=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/fundingRate":
            assert request.url.params["symbol"] == "BTCUSDT"
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "fundingTime": _ms(funding_time),
                        "fundingRate": "0.0001",
                        "markPrice": "50000.0",
                    }
                ],
            )
        if request.url.path == "/futures/data/openInterestHist":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterest": "1234.5",
                        "sumOpenInterestValue": "61725000.0",
                        "timestamp": _ms(oi_time),
                    }
                ],
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    async def run():
        provider = _provider(handler)
        funding = await provider.get_funding_rates(
            "crypto:binance:BTCUSDT:perpetual", NOW - timedelta(days=1), NOW
        )
        interest = await provider.get_open_interest(
            "crypto:binance:BTCUSDT", NOW - timedelta(hours=1), NOW
        )
        await provider.close()
        return funding, interest

    funding, interest = asyncio.run(run())
    assert len(funding) == 1
    assert funding[0].event_time == funding_time
    assert funding[0].available_at == funding_time
    assert funding[0].instrument_id.endswith(":perpetual")
    assert funding[0].funding_payment(1.0, 50_000.0) == pytest.approx(5.0)
    assert len(interest) == 1
    assert interest[0].contracts == 1234.5
    assert interest[0].notional == 61_725_000.0


def test_health_check_reports_failure_without_fabricating_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="maintenance")

    async def run():
        provider = _provider(handler, max_retries=0)
        health = await provider.health_check()
        await provider.close()
        return health

    health = asyncio.run(run())
    assert health.healthy is False
    assert "maintenance" in health.message


def test_rate_limit_is_retried_once() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, json={"serverTime": _ms(NOW)})

    async def run():
        provider = _provider(handler, max_retries=1)
        server_time = await provider.get_server_time()
        await provider.close()
        return server_time

    assert asyncio.run(run()) == NOW
    assert calls["count"] == 2


def test_unsupported_operation_raises_capability_error() -> None:
    class SpotOnly(CapabilityAwareProvider):
        name = "spot-only"
        capabilities = ProviderCapabilities(frozenset({Capability.INSTRUMENTS}))

    provider = SpotOnly()
    with pytest.raises(CapabilityError, match="trades"):
        asyncio.run(
            provider.get_trades("x", NOW - timedelta(minutes=1), NOW)
        )
