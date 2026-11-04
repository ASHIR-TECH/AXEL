import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from axel.data.providers import CapabilityError, DeribitOptionsMarketData
from axel.data.providers.base import ProviderDataError, ProviderRequestError

NOW = datetime.now(UTC).replace(microsecond=0)
INSTRUMENT = "option:deribit:BTC-20261231-100000-C:option:20261231T0800Z:K100000:C"


def _provider(handler, **kwargs) -> DeribitOptionsMarketData:
    return DeribitOptionsMarketData(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_backoff_seconds=0,
        **kwargs,
    )


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def test_option_instruments_keep_contract_terms_and_canonical_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/public/get_instruments"
        assert request.url.params["kind"] == "option"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "result": [
                    {
                        "instrument_name": "BTC-20261231-100000-C",
                        "kind": "option",
                        "base_currency": "BTC",
                        "quote_currency": "USD",
                        "settlement_currency": "BTC",
                        "tick_size": 0.0005,
                        "contract_size": 1.0,
                        "min_trade_amount": 0.1,
                        "expiration_timestamp": _ms(datetime(2026, 12, 31, 8, tzinfo=UTC)),
                        "strike": 100000.0,
                        "option_type": "call",
                        "settlement_period": "month",
                        "maker_commission": 0.0003,
                        "taker_commission": 0.0003,
                    },
                    {"instrument_name": "BTC-PERPETUAL", "kind": "future"},
                ],
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
    assert instrument.instrument_id == INSTRUMENT
    assert instrument.asset_class.value == "option"
    assert instrument.contract_type.value == "option"
    assert instrument.strike == 100000.0
    assert instrument.option_type == "call"
    assert instrument.expiry == datetime(2026, 12, 31, 8, tzinfo=UTC)
    assert instrument.settlement_currency == "BTC"
    assert instrument.tick_size == 0.0005
    assert instrument.minimum_quantity == 0.1
    assert instrument.extra_dict["settlement_period"] == "month"


def test_bars_use_canonical_option_id_and_drop_forming_candle() -> None:
    closed_open = NOW - timedelta(hours=2)
    closed_close = NOW - timedelta(hours=1)
    forming_open = NOW - timedelta(minutes=10)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/public/get_tradingview_chart_data"
        assert request.url.params["instrument_name"] == "BTC-20261231-100000-C"
        assert request.url.params["resolution"] == "60"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "result": {
                    "status": "ok",
                    "ticks": [_ms(closed_open), _ms(forming_open)],
                    "open": [0.05, 0.06],
                    "high": [0.06, 0.07],
                    "low": [0.04, 0.05],
                    "close": [0.055, 0.065],
                    "volume": [12.0, 3.0],
                },
            },
        )

    async def run():
        provider = _provider(handler)
        bars = await provider.get_historical_bars(
            INSTRUMENT, NOW - timedelta(days=1), NOW, interval="1h"
        )
        await provider.close()
        return bars

    bars = asyncio.run(run())
    assert len(bars) == 1
    assert bars[0].instrument_id == INSTRUMENT
    assert bars[0].event_time == closed_open
    assert bars[0].available_at == closed_close
    assert bars[0].close == 0.055


def test_trades_map_aggressor_side_and_support_pagination(monkeypatch) -> None:
    from axel.data.providers import deribit

    monkeypatch.setattr(deribit, "MAX_PAGE_SIZE", 2)
    first_time = NOW - timedelta(minutes=3)
    rows = [
        {"trade_id": 1, "timestamp": _ms(first_time), "price": 0.05, "amount": 1.0, "direction": "buy"},
        {"trade_id": 2, "timestamp": _ms(first_time + timedelta(minutes=1)), "price": 0.051, "amount": 2.0, "direction": "sell"},
        {"trade_id": 3, "timestamp": _ms(first_time + timedelta(minutes=2)), "price": 0.052, "amount": 3.0, "direction": "unknown"},
    ]
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/public/get_last_trades_by_instrument"
        assert request.url.params["instrument_name"] == "BTC-20261231-100000-C"
        start = int(request.url.params["start_timestamp"])
        calls["count"] += 1
        selected = [row for row in rows if row["timestamp"] >= start][:2]
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"trades": selected}})

    async def run():
        provider = _provider(handler)
        trades = await provider.get_trades(INSTRUMENT, NOW - timedelta(hours=1), NOW)
        await provider.close()
        return trades

    trades = asyncio.run(run())
    assert [trade.trade_id for trade in trades] == ["1", "2", "3"]
    assert [trade.side.value for trade in trades] == ["buy", "sell", "unknown"]
    assert calls["count"] == 3


def test_order_book_and_quote_expose_sequence_and_sizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/public/get_order_book"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "result": {
                    "timestamp": _ms(NOW),
                    "change_id": 987,
                    "bids": [[0.0500, 10.0], [0.0499, 5.0]],
                    "asks": [[0.0501, 8.0], [0.0502, 4.0]],
                },
            },
        )

    async def run():
        provider = _provider(handler)
        book = await provider.get_order_book(INSTRUMENT, depth=2)
        quote = await provider.get_quote(INSTRUMENT)
        await provider.close()
        return book, quote

    book, quote = asyncio.run(run())
    assert book.sequence == 987
    assert book.best_bid == 0.05
    assert book.best_ask == 0.0501
    assert quote.bid == 0.05
    assert quote.ask == 0.0501
    assert quote.bid_size == 10.0


def test_health_check_and_json_rpc_errors_fail_closed() -> None:
    def healthy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": _ms(NOW)})

    async def run_health():
        provider = _provider(healthy)
        health = await provider.health_check()
        await provider.close()
        return health

    assert asyncio.run(run_health()).healthy is True

    def error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "error": {"code": -32602, "message": "invalid params"}},
        )

    async def run_error():
        provider = _provider(error)
        with pytest.raises(ProviderRequestError, match="invalid params"):
            await provider.get_order_book(INSTRUMENT)
        await provider.close()

    asyncio.run(run_error())


def test_malformed_chart_and_unsupported_capability_are_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "result": {"ticks": [1], "open": [1.0]}},
        )

    async def run():
        provider = _provider(handler)
        with pytest.raises(ProviderDataError, match="OHLCV"):
            await provider.get_historical_bars(
                INSTRUMENT, NOW - timedelta(hours=2), NOW, interval="1h"
            )
        with pytest.raises(CapabilityError, match="funding_rates"):
            await provider.get_funding_rates(
                INSTRUMENT, NOW - timedelta(hours=2), NOW
            )
        await provider.close()

    asyncio.run(run())
