"""
Binance public market-data adapter (read-only, no API key).

This adapter deliberately uses only public market-data endpoints. It has no
account, order, withdrawal or leverage capability. That separation is enforced
by the class surface: there is no ``submit_order`` method here.

Capabilities: instruments, historical bars, aggregated trades, order-book
snapshots, perpetual funding rates, open interest and health.

Failure rules:
* HTTP errors raise ``ProviderRequestError`` after bounded retries.
* Malformed payloads raise ``ProviderDataError``.
* An unsupported operation raises ``CapabilityError`` via the base class.
* Incomplete in-progress candles are dropped rather than presented as closed.
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
from axel.data.marketdata import (
    FundingRateObservation,
    OpenInterestObservation,
    OrderBookSnapshot,
    TradeSide,
    TradeTick,
)
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

SPOT_BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"
MAX_PAGE_SIZE = 1000
MAX_PAGES = 100

_INTERVALS = frozenset(
    {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
)
_INTERVAL_ALIASES = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "1hour": "1h",
    "4hour": "4h",
    "1day": "1d",
    "1week": "1w",
}
_DEPTH_LIMITS = (5, 10, 20, 50, 100, 500, 1000)
_FUNDING_INTERVAL_HOURS = 8.0


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _from_ms(value: int | str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)


def _normalise_interval(interval: str) -> str:
    candidate = _INTERVAL_ALIASES.get(interval.lower(), interval)
    if candidate not in _INTERVALS:
        raise ValueError(f"unsupported Binance interval: {interval!r}")
    return candidate


def _float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"invalid numeric field {field!r}: {value!r}") from exc


def _provenance(source_id: str, raw: object, ingested: datetime) -> Provenance:
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return Provenance(
        source="binance",
        source_id=source_id,
        source_hash=sha256_text(encoded),
        ingestion_timestamp=ingested,
    )


class BinancePublicMarketData(CapabilityAwareProvider):
    """Public Binance spot/futures market-data client."""

    name = "binance"
    capabilities = ProviderCapabilities(
        frozenset(
            {
                Capability.INSTRUMENTS,
                Capability.HISTORICAL_BARS,
                Capability.TRADES,
                Capability.ORDER_BOOK,
                Capability.FUNDING_RATES,
                Capability.OPEN_INTEREST,
                Capability.HEALTH,
            }
        )
    )

    def __init__(
        self,
        *,
        spot_base_url: str = SPOT_BASE_URL,
        futures_base_url: str = FUTURES_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        venue: str = "binance",
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        funding_interval_hours: float = _FUNDING_INTERVAL_HOURS,
    ) -> None:
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("retry settings cannot be negative")
        if funding_interval_hours <= 0:
            raise ValueError("funding_interval_hours must be positive")
        self._spot_base_url = spot_base_url.rstrip("/")
        self._futures_base_url = futures_base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.venue = venue
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.funding_interval_hours = funding_interval_hours

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _symbol(instrument_id: str) -> str:
        symbol = instrument_symbol(instrument_id).strip()
        if not symbol:
            raise ValueError("instrument_id must contain a Binance symbol")
        return symbol.upper()

    def _instrument_id(self, symbol: str, contract_type: ContractType = ContractType.SPOT) -> str:
        return make_instrument_id(
            AssetClass.CRYPTO,
            self.venue,
            symbol.upper(),
            contract_type=contract_type,
        )

    async def _request_json(
        self,
        base_url: str,
        path: str,
        params: Mapping[str, Any],
        operation: str,
    ) -> tuple[Any, str]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.get(f"{base_url}{path}", params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))
                    continue
                raise ProviderRequestError(
                    self.name, operation, f"transport error: {exc}"
                ) from exc

            if response.status_code in {418, 429, 500, 502, 503, 504} and attempt < self.max_retries:
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
                detail = response.text[:300] if response.text else "no response body"
                raise ProviderRequestError(
                    self.name,
                    operation,
                    detail,
                    status_code=response.status_code,
                )
            try:
                return response.json(), response.text
            except ValueError as exc:
                raise ProviderDataError(
                    f"{self.name} {operation} returned invalid JSON: {exc}"
                ) from exc
        raise ProviderRequestError(self.name, operation, f"request failed: {last_error}")

    async def get_instruments(self) -> Sequence[Instrument]:
        self.require_capability(Capability.INSTRUMENTS)
        payload, _ = await self._request_json(
            self._spot_base_url, "/api/v3/exchangeInfo", {}, "get_instruments"
        )
        symbols = payload.get("symbols") if isinstance(payload, Mapping) else None
        if not isinstance(symbols, list):
            raise ProviderDataError("Binance exchangeInfo payload lacks a symbols list")

        instruments: list[Instrument] = []
        for item in symbols:
            if not isinstance(item, Mapping) or item.get("status") != "TRADING":
                continue
            symbol = str(item.get("symbol", ""))
            base = str(item.get("baseAsset", ""))
            quote = str(item.get("quoteAsset", ""))
            if not symbol or not base or not quote:
                continue
            filters = {
                str(entry.get("filterType")): entry
                for entry in item.get("filters", [])
                if isinstance(entry, Mapping)
            }
            price_filter = filters.get("PRICE_FILTER", {})
            lot_filter = filters.get("LOT_SIZE", {})
            notional_filter = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
            instruments.append(
                Instrument(
                    instrument_id=self._instrument_id(symbol),
                    asset_class=AssetClass.CRYPTO,
                    provider_id=self.name,
                    provider_symbol=symbol,
                    venue=self.venue,
                    base_currency=base,
                    quote_currency=quote,
                    exchange="BINANCE",
                    contract_type=ContractType.SPOT,
                    tick_size=_float(price_filter.get("tickSize", 0), "tickSize"),
                    lot_size=_float(lot_filter.get("stepSize", 0), "stepSize"),
                    minimum_quantity=_float(lot_filter.get("minQty", 0), "minQty"),
                    settlement_currency=quote,
                    trading_calendar="24/7",
                    timezone="UTC",
                    extra=(
                        ("notional_min", str(notional_filter.get("minNotional", ""))),
                        ("status", str(item.get("status", ""))),
                    ),
                )
            )
        return tuple(sorted(instruments, key=lambda instrument: instrument.provider_symbol))

    async def get_historical_bars(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1d",
    ) -> Sequence[BarRecord]:
        self.require_capability(Capability.HISTORICAL_BARS)
        self.validate_window(start_time, end_time)
        symbol = self._symbol(instrument_id)
        timeframe = _normalise_interval(interval)
        ingested = now_utc()
        records: list[BarRecord] = []
        seen: set[str] = set()
        cursor = start_time
        pages = 0

        while cursor < end_time:
            if pages >= MAX_PAGES:
                raise ProviderDataError(
                    f"Binance bar pagination exceeded {MAX_PAGES} pages; narrow the window"
                )
            payload, _ = await self._request_json(
                self._spot_base_url,
                "/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": timeframe,
                    "startTime": _to_ms(cursor),
                    "endTime": _to_ms(end_time),
                    "limit": MAX_PAGE_SIZE,
                },
                "get_historical_bars",
            )
            if not isinstance(payload, list):
                raise ProviderDataError("Binance klines payload is not a list")
            if not payload:
                break
            pages += 1
            for raw in payload:
                if not isinstance(raw, list) or len(raw) < 7:
                    raise ProviderDataError("malformed Binance kline row")
                open_time = _from_ms(raw[0])
                close_time = _from_ms(raw[6])
                available_at = close_time + timedelta(milliseconds=1)
                # Never present a still-forming candle as a closed observation.
                if available_at > ingested:
                    continue
                key = f"{symbol}:{timeframe}:{open_time.isoformat()}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    BarRecord(
                        symbol=symbol,
                        venue=self.venue,
                        instrument_id=self._instrument_id(symbol),
                        event_time=open_time,
                        available_at=available_at,
                        open=_float(raw[1], "open"),
                        high=_float(raw[2], "high"),
                        low=_float(raw[3], "low"),
                        close=_float(raw[4], "close"),
                        volume=_float(raw[5], "volume"),
                        adjustment_status="raw",
                        provenance=_provenance(key, raw, ingested),
                    )
                )
            last_close = _from_ms(payload[-1][6])
            next_cursor = last_close + timedelta(milliseconds=1)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(payload) < MAX_PAGE_SIZE:
                break

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
        symbol = self._symbol(instrument_id)
        ingested = now_utc()
        records: list[TradeTick] = []
        seen: set[str] = set()
        params: dict[str, Any] = {
            "symbol": symbol,
            "startTime": _to_ms(start_time),
            "endTime": _to_ms(end_time),
            "limit": MAX_PAGE_SIZE,
        }
        pages = 0
        truncated = False
        while True:
            if pages >= MAX_PAGES:
                truncated = True
                break
            payload, _ = await self._request_json(
                self._spot_base_url, "/api/v3/aggTrades", params, "get_trades"
            )
            if not isinstance(payload, list):
                raise ProviderDataError("Binance aggTrades payload is not a list")
            if not payload:
                break
            pages += 1
            for raw in payload:
                if not isinstance(raw, Mapping):
                    raise ProviderDataError("malformed Binance aggTrade row")
                trade_id = str(raw.get("a", ""))
                if not trade_id or trade_id in seen:
                    continue
                event_time = _from_ms(raw["T"])
                if event_time < start_time or event_time > end_time:
                    continue
                seen.add(trade_id)
                # m=True means the buyer is the maker, so the aggressor sold.
                side = TradeSide.SELL if bool(raw.get("m")) else TradeSide.BUY
                records.append(
                    TradeTick(
                        instrument_id=self._instrument_id(symbol),
                        venue=self.venue,
                        event_time=event_time,
                        available_at=event_time,
                        price=_float(raw.get("p"), "price"),
                        quantity=_float(raw.get("q"), "quantity"),
                        side=side,
                        trade_id=trade_id,
                        sequence=int(trade_id),
                        provenance=_provenance(f"{symbol}:aggTrade:{trade_id}", raw, ingested),
                    )
                )
            if len(payload) < MAX_PAGE_SIZE:
                break
            # A fromId page can run past the requested window; stop rather than
            # paging through unrelated future trades.
            if _from_ms(payload[0]["T"]) > end_time:
                break
            last_id = int(payload[-1]["a"])
            params = {"symbol": symbol, "fromId": last_id + 1, "limit": MAX_PAGE_SIZE}
        if truncated:
            raise ProviderDataError(
                f"Binance trade pagination exceeded {MAX_PAGES} pages; narrow the window"
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
        symbol = self._symbol(instrument_id)
        limit = next((value for value in _DEPTH_LIMITS if value >= depth), _DEPTH_LIMITS[-1])
        payload, raw_text = await self._request_json(
            self._spot_base_url,
            "/api/v3/depth",
            {"symbol": symbol, "limit": limit},
            "get_order_book",
        )
        if not isinstance(payload, Mapping):
            raise ProviderDataError("Binance depth payload is not an object")
        bids = payload.get("bids")
        asks = payload.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list):
            raise ProviderDataError("Binance depth payload lacks bids/asks")
        if not bids or not asks:
            raise ProviderDataError(f"Binance returned an empty order book for {symbol}")
        captured = now_utc()
        return OrderBookSnapshot(
            instrument_id=self._instrument_id(symbol),
            venue=self.venue,
            event_time=captured,
            available_at=captured,
            bids=tuple((_float(level[0], "bid price"), _float(level[1], "bid size")) for level in bids),
            asks=tuple((_float(level[0], "ask price"), _float(level[1], "ask size")) for level in asks),
            sequence=int(payload.get("lastUpdateId", 0)),
            provenance=Provenance(
                source="binance",
                source_id=f"{symbol}:depth",
                source_hash=sha256_text(raw_text),
                ingestion_timestamp=captured,
            ),
        )

    async def get_funding_rates(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[FundingRateObservation]:
        self.require_capability(Capability.FUNDING_RATES)
        self.validate_window(start_time, end_time)
        symbol = self._symbol(instrument_id)
        payload, _ = await self._request_json(
            self._futures_base_url,
            "/fapi/v1/fundingRate",
            {
                "symbol": symbol,
                "startTime": _to_ms(start_time),
                "endTime": _to_ms(end_time),
                "limit": MAX_PAGE_SIZE,
            },
            "get_funding_rates",
        )
        if not isinstance(payload, list):
            raise ProviderDataError("Binance fundingRate payload is not a list")
        ingested = now_utc()
        records: list[FundingRateObservation] = []
        for raw in payload:
            if not isinstance(raw, Mapping):
                raise ProviderDataError("malformed Binance fundingRate row")
            event_time = _from_ms(raw["fundingTime"])
            if event_time < start_time or event_time > end_time:
                continue
            mark_price = raw.get("markPrice")
            records.append(
                FundingRateObservation(
                    instrument_id=self._instrument_id(symbol, ContractType.PERPETUAL),
                    venue=self.venue,
                    event_time=event_time,
                    available_at=event_time,
                    rate=_float(raw.get("fundingRate"), "fundingRate"),
                    interval_hours=self.funding_interval_hours,
                    mark_price=_float(mark_price, "markPrice") if mark_price is not None else None,
                    provenance=_provenance(
                        f"{symbol}:funding:{event_time.isoformat()}", raw, ingested
                    ),
                )
            )
        records.sort(key=lambda record: record.event_time)
        return tuple(records)

    async def get_open_interest(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
        *,
        period: str = "5m",
    ) -> Sequence[OpenInterestObservation]:
        self.require_capability(Capability.OPEN_INTEREST)
        self.validate_window(start_time, end_time)
        if period not in {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}:
            raise ValueError(f"unsupported open-interest period: {period!r}")
        symbol = self._symbol(instrument_id)
        payload, _ = await self._request_json(
            self._futures_base_url,
            "/futures/data/openInterestHist",
            {
                "symbol": symbol,
                "period": period,
                "startTime": _to_ms(start_time),
                "endTime": _to_ms(end_time),
                "limit": 500,
            },
            "get_open_interest",
        )
        if not isinstance(payload, list):
            raise ProviderDataError("Binance openInterestHist payload is not a list")
        ingested = now_utc()
        records: list[OpenInterestObservation] = []
        for raw in payload:
            if not isinstance(raw, Mapping):
                raise ProviderDataError("malformed Binance openInterest row")
            event_time = _from_ms(raw["timestamp"])
            if event_time < start_time or event_time > end_time:
                continue
            notional = raw.get("sumOpenInterestValue")
            records.append(
                OpenInterestObservation(
                    instrument_id=self._instrument_id(symbol, ContractType.PERPETUAL),
                    venue=self.venue,
                    event_time=event_time,
                    available_at=event_time,
                    contracts=_float(raw.get("sumOpenInterest"), "sumOpenInterest"),
                    notional=_float(notional, "sumOpenInterestValue") if notional is not None else None,
                    provenance=_provenance(
                        f"{symbol}:open-interest:{event_time.isoformat()}", raw, ingested
                    ),
                )
            )
        records.sort(key=lambda record: record.event_time)
        return tuple(records)

    async def get_server_time(self) -> datetime:
        """Return Binance's clock for explicit drift checks."""
        payload, _ = await self._request_json(
            self._spot_base_url, "/api/v3/time", {}, "get_server_time"
        )
        if not isinstance(payload, Mapping) or "serverTime" not in payload:
            raise ProviderDataError("Binance time payload lacks serverTime")
        return _from_ms(payload["serverTime"])

    async def health_check(self) -> ProviderHealth:
        self.require_capability(Capability.HEALTH)
        started = monotonic()
        checked = now_utc()
        try:
            await self._request_json(self._spot_base_url, "/api/v3/ping", {}, "health_check")
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


__all__ = ["FUTURES_BASE_URL", "SPOT_BASE_URL", "BinancePublicMarketData"]
