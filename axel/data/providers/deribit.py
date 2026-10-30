"""
Deribit public options market-data adapter (read-only).

Deribit exposes option contract metadata, order books, trades and chart candles
through public JSON-RPC endpoints. This adapter normalises those payloads into
the cross-asset ``Instrument`` / ``BarRecord`` / ``TradeTick`` /
``OrderBookSnapshot`` records. It does not place orders or touch accounts.

The adapter is intentionally option-only: a perpetual or spot instrument from
the same venue is a different asset class and must not be silently mixed in.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

import httpx

from axel.data.instruments import (
    AssetClass,
    ContractType,
    Instrument,
    instrument_symbol,
    make_instrument_id,
)
from axel.data.marketdata import OrderBookSnapshot, QuoteTick, TradeSide, TradeTick
from axel.data.providers.base import (
    Capability,
    CapabilityAwareProvider,
    ProviderCapabilities,
    ProviderDataError,
    ProviderHealth,
    ProviderRequestError,
    now_utc,
)
from axel.data.schemas import BarRecord, Provenance, sha256_text

DERIBIT_BASE_URL = "https://www.deribit.com/api/v2"
MAX_PAGE_SIZE = 1000
MAX_PAGES = 100

_RESOLUTIONS = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "1D",
}

_DURATIONS = {
    "1": timedelta(minutes=1),
    "3": timedelta(minutes=3),
    "5": timedelta(minutes=5),
    "10": timedelta(minutes=10),
    "15": timedelta(minutes=15),
    "30": timedelta(minutes=30),
    "60": timedelta(hours=1),
    "120": timedelta(hours=2),
    "240": timedelta(hours=4),
    "360": timedelta(hours=6),
    "720": timedelta(hours=12),
    "1D": timedelta(days=1),
}


def _ms_to_dt(value: int | str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)


def _provenance(source_id: str, raw: object, ingested: datetime) -> Provenance:
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return Provenance(
        source="deribit",
        source_id=source_id,
        source_hash=sha256_text(encoded),
        ingestion_timestamp=ingested,
    )


def _float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"invalid Deribit numeric field {field!r}: {value!r}") from exc


def _positive(value: Any, field: str) -> float:
    parsed = _float(value, field)
    if parsed <= 0:
        raise ProviderDataError(f"Deribit field {field!r} must be positive, got {parsed!r}")
    return parsed


class DeribitOptionsMarketData(CapabilityAwareProvider):
    """Public Deribit option-chain and market-data client."""

    name = "deribit"
    capabilities = ProviderCapabilities(
        frozenset(
            {
                Capability.INSTRUMENTS,
                Capability.HISTORICAL_BARS,
                Capability.TRADES,
                Capability.ORDER_BOOK,
                Capability.QUOTES,
                Capability.HEALTH,
            }
        )
    )

    def __init__(
        self,
        *,
        currency: str = "BTC",
        base_url: str = DERIBIT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        venue: str = "deribit",
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
    ) -> None:
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("retry settings cannot be negative")
        self.currency = currency.upper()
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.venue = venue
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _instrument_name(instrument_id: str) -> str:
        name = instrument_symbol(instrument_id).strip().upper()
        if not name:
            raise ValueError("instrument_id must contain a Deribit instrument name")
        return name

    async def _request_json(
        self, path: str, params: Mapping[str, Any], operation: str
    ) -> tuple[Any, str]:
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.get(
                    f"{self._base_url}{path}", params=params
                )
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))
                    continue
                raise ProviderRequestError(
                    self.name, operation, f"transport error: {exc}"
                ) from exc

            if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                delay = self.retry_backoff_seconds * (2**attempt)
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                await asyncio.sleep(min(delay, 5.0))
                continue
            if response.status_code >= 400:
                raise ProviderRequestError(
                    self.name,
                    operation,
                    response.text[:300] or "no response body",
                    status_code=response.status_code,
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderDataError(
                    f"{self.name} {operation} returned invalid JSON: {exc}"
                ) from exc
            if isinstance(payload, Mapping) and payload.get("error"):
                error = payload["error"]
                message = error.get("message") if isinstance(error, Mapping) else str(error)
                raise ProviderRequestError(self.name, operation, str(message))
            return payload, response.text
        raise ProviderRequestError(self.name, operation, "request failed")

    async def get_instruments(self) -> Sequence[Instrument]:
        self.require_capability(Capability.INSTRUMENTS)
        payload, _ = await self._request_json(
            "/public/get_instruments",
            {"currency": self.currency, "kind": "option", "expired": "false"},
            "get_instruments",
        )
        rows = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise ProviderDataError("Deribit get_instruments result is not a list")
        instruments: list[Instrument] = []
        for row in rows:
            if not isinstance(row, Mapping) or row.get("kind") != "option":
                continue
            name = str(row.get("instrument_name", "")).upper()
            if not name:
                continue
            expiry = _ms_to_dt(row["expiration_timestamp"])
            strike = _positive(row.get("strike"), "strike")
            option_type = str(row.get("option_type", "")).lower()
            instruments.append(
                Instrument(
                    instrument_id=make_instrument_id(
                        AssetClass.OPTION,
                        self.venue,
                        name,
                        contract_type=ContractType.OPTION,
                        expiry=expiry,
                        strike=strike,
                        option_type=option_type,
                    ),
                    asset_class=AssetClass.OPTION,
                    provider_id=self.name,
                    provider_symbol=name,
                    venue=self.venue,
                    base_currency=str(row.get("base_currency", self.currency)),
                    quote_currency=str(row.get("quote_currency", "USD")),
                    exchange="DERIBIT",
                    contract_type=ContractType.OPTION,
                    tick_size=_positive(row.get("tick_size"), "tick_size"),
                    lot_size=_positive(row.get("min_trade_amount"), "min_trade_amount"),
                    minimum_quantity=_positive(
                        row.get("min_trade_amount"), "min_trade_amount"
                    ),
                    expiry=expiry,
                    strike=strike,
                    option_type=option_type,
                    settlement_currency=str(
                        row.get("settlement_currency", row.get("base_currency", self.currency))
                    ),
                    trading_calendar="24/7",
                    timezone="UTC",
                    multiplier=_positive(row.get("contract_size", 1.0), "contract_size"),
                    extra=(
                        ("settlement_period", str(row.get("settlement_period", ""))),
                        ("maker_commission", str(row.get("maker_commission", ""))),
                        ("taker_commission", str(row.get("taker_commission", ""))),
                    ),
                )
            )
        return tuple(sorted(instruments, key=lambda item: item.provider_symbol))

    async def get_historical_bars(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1h",
    ) -> Sequence[BarRecord]:
        self.require_capability(Capability.HISTORICAL_BARS)
        self.validate_window(start_time, end_time)
        name = self._instrument_name(instrument_id)
        resolution = _RESOLUTIONS.get(interval.lower())
        if resolution is None:
            raise ValueError(f"unsupported Deribit interval: {interval!r}")
        duration = _DURATIONS[resolution]
        payload, _ = await self._request_json(
            "/public/get_tradingview_chart_data",
            {
                "instrument_name": name,
                "start_timestamp": int(start_time.timestamp() * 1000),
                "end_timestamp": int(end_time.timestamp() * 1000),
                "resolution": resolution,
            },
            "get_historical_bars",
        )
        result = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(result, Mapping):
            raise ProviderDataError("Deribit chart result is not an object")
        ticks = result.get("ticks")
        opens = result.get("open")
        highs = result.get("high")
        lows = result.get("low")
        closes = result.get("close")
        volumes = result.get("volume")
        if not all(isinstance(values, list) for values in (ticks, opens, highs, lows, closes, volumes)):
            raise ProviderDataError("Deribit chart payload lacks OHLCV arrays")
        if not (len(ticks) == len(opens) == len(highs) == len(lows) == len(closes) == len(volumes)):
            raise ProviderDataError("Deribit chart arrays have inconsistent lengths")
        ingested = now_utc()
        records: list[BarRecord] = []
        for index, tick in enumerate(ticks):
            event_time = _ms_to_dt(tick)
            available_at = event_time + duration
            if event_time < start_time or event_time > end_time or available_at > ingested:
                continue
            key = f"{name}:{resolution}:{event_time.isoformat()}"
            records.append(
                BarRecord(
                    symbol=name,
                    venue=self.venue,
                    instrument_id=instrument_id,
                    event_time=event_time,
                    available_at=available_at,
                    open=_float(opens[index], "open"),
                    high=_float(highs[index], "high"),
                    low=_float(lows[index], "low"),
                    close=_float(closes[index], "close"),
                    volume=_float(volumes[index], "volume"),
                    adjustment_status="raw",
                    provenance=_provenance(
                        key, [tick, opens[index], highs[index], lows[index], closes[index], volumes[index]], ingested
                    ),
                )
            )
        records.sort(key=lambda record: record.event_time)
        return tuple(records)

    async def get_trades(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[TradeTick]:
        self.require_capability(Capability.TRADES)
        self.validate_window(start_time, end_time)
        name = self._instrument_name(instrument_id)
        ingested = now_utc()
        records: list[TradeTick] = []
        seen: set[str] = set()
        cursor = start_time
        pages = 0
        truncated = False

        while True:
            if pages >= MAX_PAGES:
                truncated = True
                break
            payload, _ = await self._request_json(
                "/public/get_last_trades_by_instrument",
                {
                    "instrument_name": name,
                    "start_timestamp": int(cursor.timestamp() * 1000),
                    "end_timestamp": int(end_time.timestamp() * 1000),
                    "count": MAX_PAGE_SIZE,
                    "sorting": "asc",
                },
                "get_trades",
            )
            result = payload.get("result") if isinstance(payload, Mapping) else None
            rows = result.get("trades") if isinstance(result, Mapping) else None
            if not isinstance(rows, list):
                raise ProviderDataError("Deribit trades result lacks a trades list")
            if not rows:
                break
            pages += 1
            added = 0
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ProviderDataError("malformed Deribit trade row")
                event_time = _ms_to_dt(row["timestamp"])
                if event_time < start_time or event_time > end_time:
                    continue
                trade_id = str(row.get("trade_id", ""))
                if not trade_id:
                    raise ProviderDataError("Deribit trade row lacks trade_id")
                if trade_id in seen:
                    continue
                direction = str(row.get("direction", "")).lower()
                side = {"buy": TradeSide.BUY, "sell": TradeSide.SELL}.get(
                    direction, TradeSide.UNKNOWN
                )
                seen.add(trade_id)
                added += 1
                records.append(
                    TradeTick(
                        instrument_id=instrument_id,
                        venue=self.venue,
                        event_time=event_time,
                        available_at=event_time,
                        price=_positive(row.get("price"), "price"),
                        quantity=_positive(row.get("amount"), "amount"),
                        side=side,
                        trade_id=trade_id,
                        sequence=int(trade_id) if trade_id.isdigit() else None,
                        provenance=_provenance(f"{name}:trade:{trade_id}", row, ingested),
                    )
                )
            if len(rows) < MAX_PAGE_SIZE:
                break
            last_time = _ms_to_dt(rows[-1]["timestamp"])
            if last_time > end_time:
                break
            if last_time < cursor or (last_time == cursor and added == 0):
                raise ProviderDataError(
                    "Deribit trade pagination cannot advance within the requested window; "
                    "narrow the time range"
                )
            # Inclusive cursor; duplicates are filtered by trade_id above.
            cursor = last_time

        if truncated:
            raise ProviderDataError(
                f"Deribit trade pagination exceeded {MAX_PAGES} pages; narrow the window"
            )
        records.sort(key=lambda record: (record.event_time, record.trade_id))
        return tuple(records)

    async def get_order_book(
        self,
        instrument_id: str,
        *,
        depth: int = 5,
    ) -> OrderBookSnapshot:
        self.require_capability(Capability.ORDER_BOOK)
        if depth <= 0:
            raise ValueError("depth must be positive")
        name = self._instrument_name(instrument_id)
        payload, _ = await self._request_json(
            "/public/get_order_book",
            {"instrument_name": name, "depth": depth},
            "get_order_book",
        )
        result = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(result, Mapping):
            raise ProviderDataError("Deribit order-book result is not an object")
        bids = result.get("bids")
        asks = result.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list):
            raise ProviderDataError("Deribit order book lacks bids/asks")
        if not bids or not asks:
            raise ProviderDataError(f"Deribit returned an empty order book for {name}")
        if "timestamp" not in result:
            raise ProviderDataError("Deribit order book lacks a timestamp")
        event_time = _ms_to_dt(result["timestamp"])
        try:
            parsed_bids = tuple(
                (_positive(level[0], "bid price"), _positive(level[1], "bid size"))
                for level in bids
            )
            parsed_asks = tuple(
                (_positive(level[0], "ask price"), _positive(level[1], "ask size"))
                for level in asks
            )
        except (IndexError, TypeError) as exc:
            raise ProviderDataError("malformed Deribit order-book level") from exc
        change_id = result.get("change_id")
        try:
            return OrderBookSnapshot(
                instrument_id=instrument_id,
                venue=self.venue,
                event_time=event_time,
                available_at=event_time,
                bids=parsed_bids,
                asks=parsed_asks,
                sequence=int(change_id) if change_id is not None else None,
                provenance=_provenance(f"{name}:order-book", result, now_utc()),
            )
        except ValueError as exc:
            raise ProviderDataError(f"invalid Deribit order book for {name}: {exc}") from exc

    async def get_quote(self, instrument_id: str) -> QuoteTick:
        self.require_capability(Capability.QUOTES)
        book = await self.get_order_book(instrument_id, depth=1)
        return QuoteTick(
            instrument_id=book.instrument_id,
            venue=book.venue,
            event_time=book.event_time,
            available_at=book.available_at,
            bid=book.best_bid,
            ask=book.best_ask,
            bid_size=book.bids[0][1],
            ask_size=book.asks[0][1],
            provenance=book.provenance,
        )

    async def health_check(self) -> ProviderHealth:
        self.require_capability(Capability.HEALTH)
        started = monotonic()
        checked = now_utc()
        try:
            await self._request_json("/public/get_time", {}, "health_check")
        except (ProviderRequestError, ProviderDataError) as exc:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                checked_at=checked,
                latency_ms=(monotonic() - started) * 1000.0,
                message=str(exc),
            )
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=checked,
            latency_ms=(monotonic() - started) * 1000.0,
            message="ok",
        )


__all__ = ["DERIBIT_BASE_URL", "DeribitOptionsMarketData"]
