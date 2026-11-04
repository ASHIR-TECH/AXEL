import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from axel.data.providers import CapabilityError, KalshiPredictionMarketData
from axel.data.providers.base import ProviderDataError, ProviderRequestError

NOW = datetime.now(UTC).replace(microsecond=0)
TICKER = "PRES-2028-DEM"
INSTRUMENT = "prediction:kalshi:PRES-2028-DEM:binary"
MARKET = {
    "ticker": TICKER,
    "event_ticker": "PRES-2028",
    "title": "Will the Democratic candidate win the 2028 presidential election?",
    "market_type": "binary",
    "status": "active",
    "open_time": "2026-01-01T00:00:00Z",
    "close_time": "2028-11-07T00:00:00Z",
    "expiration_time": "2028-11-07T00:00:00Z",
    "updated_time": NOW.isoformat(),
    "yes_bid": 42,
    "yes_ask": 44,
    "no_bid": 56,
    "no_ask": 58,
    "last_price": 43,
    "volume": 1000,
    "open_interest": 500,
    "liquidity": 250000,
    "result": "",
    "rules_primary": "Resolves YES if the Democratic candidate wins.",
    "rules_secondary": "Settlement follows the certified result.",
    "can_close_early": False,
    "fractional_trading_enabled": True,
    "price_level_structure": "linear_cent",
    "category": "Politics",
}


def _provider(handler, **kwargs) -> KalshiPredictionMarketData:
    return KalshiPredictionMarketData(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_backoff_seconds=0,
        **kwargs,
    )


def _market_response(row=None) -> httpx.Response:
    return httpx.Response(200, json={"market": dict(row or MARKET)})


def test_instruments_and_market_metadata_are_canonical() -> None:
    scalar = dict(MARKET, ticker="RANGE-2028", market_type="scalar")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/trade-api/v2/markets"
        return httpx.Response(200, json={"markets": [MARKET, scalar], "cursor": ""})

    async def run():
        provider = _provider(handler)
        instruments = await provider.get_instruments()
        markets = await provider.get_markets(status="active")
        await provider.close()
        return instruments, markets

    instruments, markets = asyncio.run(run())
    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.instrument_id == "prediction:kalshi:PRES-2028-DEM:binary"
    assert instrument.asset_class.value == "prediction"
    assert instrument.contract_type.value == "binary"
    assert instrument.tick_size == 0.01
    assert instrument.minimum_quantity == 1.0
    assert instrument.quote_currency == "USD"
    assert instrument.expiry == datetime(2028, 11, 7, tzinfo=UTC)
    assert instrument.extra_dict["event_ticker"] == "PRES-2028"
    assert instrument.extra_dict["rules_hash"]

    assert len(markets) == 2
    info = next(item for item in markets if item.ticker == TICKER)
    assert info.market_id == instrument.instrument_id
    assert info.status.value == "active"
    assert info.yes_bid == 0.42
    assert info.yes_ask == 0.44
    assert info.result.value == "unresolved"
    assert info.is_settled is False
    assert info.settlement_source == "kalshi_rules"
    assert "Democratic" in info.rules_primary


def test_quote_preserves_probability_units_and_capture_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/trade-api/v2/markets/{TICKER}"
        return _market_response()

    async def run():
        provider = _provider(handler)
        quote = await provider.get_prediction_quote(
            "prediction:kalshi:PRES-2028-DEM:binary"
        )
        await provider.close()
        return quote

    quote = asyncio.run(run())
    assert quote.yes_bid == 0.42
    assert quote.yes_ask == 0.44
    assert quote.no_bid == 0.56
    assert quote.no_ask == 0.58
    assert quote.mid_yes == pytest.approx(0.43)
    assert quote.event_time == NOW
    assert quote.available_at >= quote.event_time
    assert quote.status.value == "active"


def test_dollar_denominated_fields_are_supported() -> None:
    row = {
        key: value
        for key, value in MARKET.items()
        if key not in {"yes_bid", "yes_ask", "no_bid", "no_ask", "last_price"}
    }
    row.update(
        {
            "yes_bid_dollars": "0.4200",
            "yes_ask_dollars": "0.4400",
            "no_bid_dollars": "0.5600",
            "no_ask_dollars": "0.5800",
            "last_price_dollars": "0.4300",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _market_response(row)

    async def run():
        provider = _provider(handler)
        quote = await provider.get_prediction_quote(INSTRUMENT)
        await provider.close()
        return quote

    quote = asyncio.run(run())
    assert quote.yes_bid == 0.42
    assert quote.last_price == 0.43


def test_order_book_derives_yes_asks_from_no_bids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/trade-api/v2/markets/{TICKER}/orderbook"
        return httpx.Response(
            200,
            json={
                "orderbook": {
                    "yes": [[42, 100], [41, 50]],
                    "no": [[56, 80], [55, 20]],
                }
            },
        )

    async def run():
        provider = _provider(handler)
        book = await provider.get_prediction_order_book(INSTRUMENT, depth=2)
        await provider.close()
        return book

    book = asyncio.run(run())
    assert book.yes_bids == ((0.42, 100.0), (0.41, 50.0))
    assert book.yes_asks == ((0.44, 80.0), (0.45, 20.0))
    assert book.best_yes_bid == 0.42
    assert book.best_yes_ask == 0.44
    assert book.mid_yes == pytest.approx(0.43)
    assert book.depth(1) == (100.0, 80.0)
    assert book.imbalance(1) == pytest.approx((100 - 80) / (100 + 80))
    assert "capture_time_used" in book.provenance.quality_flags


def test_crossed_prediction_order_book_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"orderbook": {"yes": [[60, 100]], "no": [[50, 100]]}},
        )

    async def run():
        provider = _provider(handler)
        with pytest.raises(ProviderDataError, match="crossed"):
            await provider.get_prediction_order_book(INSTRUMENT)
        await provider.close()

    asyncio.run(run())


def test_trades_allow_zero_and_one_prices_and_paginate() -> None:
    first_time = NOW - timedelta(minutes=5)
    trades = [
        {
            "trade_id": "t1",
            "ticker": TICKER,
            "created_time": first_time.isoformat(),
            "yes_price": 43,
            "count": 10,
            "taker_side": "yes",
        },
        {
            "trade_id": "t2",
            "ticker": TICKER,
            "created_time": (first_time + timedelta(minutes=1)).isoformat(),
            "yes_price": 0,
            "count": 5,
            "taker_side": "no",
        },
    ]
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/trade-api/v2/markets/trades"
        calls["count"] += 1
        if request.url.params.get("cursor") == "next":
            return httpx.Response(200, json={"trades": [trades[1]], "cursor": ""})
        return httpx.Response(200, json={"trades": [trades[0]], "cursor": "next"})

    async def run():
        provider = _provider(handler)
        records = await provider.get_prediction_trades(
            INSTRUMENT, NOW - timedelta(hours=1), NOW
        )
        await provider.close()
        return records

    records = asyncio.run(run())
    assert [record.trade_id for record in records] == ["t1", "t2"]
    assert records[0].yes_price == 0.43
    assert records[0].no_price == pytest.approx(0.57)
    assert records[0].taker_side.value == "yes"
    assert records[1].yes_price == 0.0
    assert records[1].no_price == 1.0
    assert records[1].taker_side.value == "no"
    assert calls["count"] == 2


def test_bars_use_period_end_and_drop_unclosed_candles() -> None:
    closed_end = NOW - timedelta(hours=1)
    future_end = NOW + timedelta(hours=1)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/trade-api/v2/markets/candlesticks"
        assert request.url.params["period_interval"] == "60"
        return httpx.Response(
            200,
            json={
                "markets": [
                    {
                        "market_ticker": TICKER,
                        "candlesticks": [
                            {
                                "end_period_ts": int(closed_end.timestamp()),
                                "price": {
                                    "open": 40,
                                    "high": 46,
                                    "low": 39,
                                    "close": 45,
                                },
                                "volume": 120,
                            },
                            {
                                "end_period_ts": int(future_end.timestamp()),
                                "price": {
                                    "open": 45,
                                    "high": 50,
                                    "low": 44,
                                    "close": 49,
                                },
                                "volume": 10,
                            },
                        ],
                    }
                ]
            },
        )

    async def run():
        provider = _provider(handler)
        bars = await provider.get_prediction_bars(
            INSTRUMENT, NOW - timedelta(days=1), NOW + timedelta(hours=2), interval="1h"
        )
        await provider.close()
        return bars

    bars = asyncio.run(run())
    assert len(bars) == 1
    assert bars[0].event_time == closed_end - timedelta(hours=1)
    assert bars[0].available_at == closed_end
    assert bars[0].open == 0.40
    assert bars[0].high == 0.46
    assert bars[0].low == 0.39
    assert bars[0].close == 0.45
    assert bars[0].volume == 120


def test_health_and_unsupported_capabilities_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/trade-api/v2/exchange/status"
        return httpx.Response(
            200, json={"exchange_active": True, "trading_active": False}
        )

    async def run():
        provider = _provider(handler)
        health = await provider.health_check()
        with pytest.raises(CapabilityError, match="trades"):
            await provider.get_trades(INSTRUMENT, NOW - timedelta(hours=1), NOW)
        with pytest.raises(ValueError, match="unsupported Kalshi interval"):
            await provider.get_prediction_bars(
                INSTRUMENT, NOW - timedelta(hours=1), NOW, interval="5m"
            )
        await provider.close()
        return health

    health = asyncio.run(run())
    assert health.healthy is True
    assert dict(health.details)["trading_active"] == "False"


def test_http_errors_and_malformed_markets_are_rejected() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="maintenance")

    async def run_failure():
        provider = _provider(failing, max_retries=0)
        with pytest.raises(ProviderRequestError, match="maintenance"):
            await provider.get_markets()
        await provider.close()

    asyncio.run(run_failure())

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markets": [{"title": "missing ticker"}]})

    async def run_malformed():
        provider = _provider(malformed)
        with pytest.raises(ProviderDataError, match="ticker"):
            await provider.get_markets()
        await provider.close()

    asyncio.run(run_malformed())


def test_missing_market_and_missing_quote_fields_fail_closed() -> None:
    def no_market(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async def run_no_market():
        provider = _provider(no_market)
        with pytest.raises(ProviderDataError, match="no market object"):
            await provider.get_market(INSTRUMENT)
        await provider.close()

    asyncio.run(run_no_market())

    incomplete = dict(MARKET)
    incomplete.pop("yes_bid")
    incomplete.pop("yes_bid_dollars", None)

    def incomplete_market(request: httpx.Request) -> httpx.Response:
        return _market_response(incomplete)

    async def run_incomplete():
        provider = _provider(incomplete_market)
        with pytest.raises(ProviderDataError, match="quote fields"):
            await provider.get_prediction_quote(INSTRUMENT)
        await provider.close()

    asyncio.run(run_incomplete())
