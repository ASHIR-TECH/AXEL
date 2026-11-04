"""
OANDA v20 FX market-data adapter (read-only).

Requires an OANDA API token. Instruments and account pricing additionally require
an account id; candles and health probes work with the token alone. The adapter
never submits orders, transfers funds or enables leverage.

OANDA candles are mid-priced by default. Bid/ask data is available through the
quote endpoint and should be used for execution-sensitive research. The
``BarRecord`` price basis is recorded in provenance metadata so a mid-price bar
is never mistaken for a tradeable bid/ask series.
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
from axel.data.marketdata import QuoteTick
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

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL = "https://api-fxtrade.oanda.com"
MAX_CANDLES = 5000
MAX_PAGES = 100

_GRANULARITIES = {
    "5s": "S5",
    "10s": "S10",
    "30s": "S30",
    "1m": "M1",
    "2m": "M2",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "2h": "H2",
    "4h": "H4",
    "8h": "H8",
    "1d": "D",
    "1w": "W",
}

_DURATIONS = {
    "S5": timedelta(seconds=5),
    "S10": timedelta(seconds=10),
    "S30": timedelta(seconds=30),
    "M1": timedelta(minutes=1),
    "M2": timedelta(minutes=2),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "H2": timedelta(hours=2),
    "H4": timedelta(hours=4),
    "H8": timedelta(hours=8),
    "D": timedelta(days=1),
    "W": timedelta(weeks=1),
}


def _parse_time(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProviderDataError(f"invalid OANDA timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ProviderDataError(f"OANDA timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(UTC)


def _provenance(source_id: str, raw: object, ingested: datetime) -> Provenance:
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return Provenance(
        source="oanda",
        source_id=source_id,
        source_hash=sha256_text(encoded),
        ingestion_timestamp=ingested,
    )


def _float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"invalid OANDA numeric field {field!r}: {value!r}") from exc


class OandaMarketData(CapabilityAwareProvider):
    """OANDA v20 read-only FX market data."""

    name = "oanda"
    capabilities = ProviderCapabilities(
        frozenset(
            {
                Capability.INSTRUMENTS,
                Capability.HISTORICAL_BARS,
                Capability.QUOTES,
                Capability.HEALTH,
            }
        )
    )

    def __init__(
        self,
        api_token: str,
        *,
        account_id: str | None = None,
        environment: str = "practice",
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        venue: str = "oanda",
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
    ) -> None:
        if not api_token:
            raise ValueError("OANDA API token is required")
        if environment not in {"practice", "live"}:
            raise ValueError("environment must be 'practice' or 'live'")
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("retry settings cannot be negative")
        self._base_url = PRACTICE_URL if environment == "practice" else LIVE_URL
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept-Datetime-Format": "RFC3339",
        }
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.account_id = account_id
        self.environment = environment
        self.venue = venue
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _symbol(instrument_id: str) -> str:
        symbol = instrument_symbol(instrument_id).strip().upper()
        if "_" not in symbol:
            raise ValueError(f"OANDA instrument must look like EUR_USD, got {instrument_id!r}")
        return symbol

    def _instrument_id(self, symbol: str) -> str:
        return make_instrument_id(AssetClass.FOREX, self.venue, symbol.upper())

    def _require_account(self) -> str:
        if not self.account_id:
            raise ValueError("OANDA account_id is required for this operation")
        return self.account_id

    async def _request_json(
        self, path: str, params: Mapping[str, Any], operation: str
    ) -> tuple[Any, str]:
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.get(
                    f"{self._base_url}{path}", headers=self._headers, params=params
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
                return response.json(), response.text
            except ValueError as exc:
                raise ProviderDataError(
                    f"{self.name} {operation} returned invalid JSON: {exc}"
                ) from exc
        raise ProviderRequestError(self.name, operation, "request failed")

    async def get_instruments(self) -> Sequence[Instrument]:
        self.require_capability(Capability.INSTRUMENTS)
        account = self._require_account()
        payload, _ = await self._request_json(
            f"/v3/accounts/{account}/instruments", {}, "get_instruments"
        )
        rows = payload.get("instruments") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise ProviderDataError("OANDA instruments payload lacks an instruments list")
        instruments: list[Instrument] = []
        for row in rows:
            if not isinstance(row, Mapping) or row.get("type") != "CURRENCY":
                continue
            symbol = str(row.get("name", "")).upper()
            if "_" not in symbol:
                continue
            base, quote = symbol.split("_", 1)
            pip_location = int(row.get("pipLocation", -4))
            display_precision = int(row.get("displayPrecision", -pip_location + 1))
            precision = int(row.get("tradeUnitsPrecision", 0))
            instruments.append(
                Instrument(
                    instrument_id=self._instrument_id(symbol),
                    asset_class=AssetClass.FOREX,
                    provider_id=self.name,
                    provider_symbol=symbol,
                    venue=self.venue,
                    base_currency=base,
                    quote_currency=quote,
                    exchange="OANDA",
                    contract_type=ContractType.SPOT,
                    tick_size=10.0 ** (-display_precision),
                    lot_size=10.0 ** (-precision) if precision > 0 else 1.0,
                    minimum_quantity=_float(row.get("minimumTradeSize", 1), "minimumTradeSize"),
                    settlement_currency=quote,
                    trading_calendar="FX_24_5",
                    timezone="UTC",
                    extra=(
                        ("margin_rate", str(row.get("marginRate", ""))),
                        ("display_name", str(row.get("displayName", ""))),
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
        symbol = self._symbol(instrument_id)
        if interval == "1M":
            # OANDA's "M" granularity is monthly; lowercasing it would silently
            # reinterpret the request as one-minute candles.
            raise ValueError(
                "unsupported OANDA interval: '1M' (monthly candles are not supported "
                "because month length is not fixed for point-in-time availability)"
            )
        granularity = _GRANULARITIES.get(interval.lower())
        if granularity is None:
            raise ValueError(f"unsupported OANDA interval: {interval!r}")
        duration = _DURATIONS[granularity]
        ingested = now_utc()
        records: list[BarRecord] = []
        seen: set[str] = set()
        cursor = start_time
        pages = 0

        while cursor < end_time:
            if pages >= MAX_PAGES:
                raise ProviderDataError(
                    f"OANDA candle pagination exceeded {MAX_PAGES} pages; narrow the window"
                )
            payload, _ = await self._request_json(
                f"/v3/instruments/{symbol}/candles",
                {
                    "granularity": granularity,
                    "from": cursor.isoformat().replace("+00:00", "Z"),
                    "to": end_time.isoformat().replace("+00:00", "Z"),
                    "price": "M",
                    "count": MAX_CANDLES,
                },
                "get_historical_bars",
            )
            rows = payload.get("candles") if isinstance(payload, Mapping) else None
            if not isinstance(rows, list):
                raise ProviderDataError("OANDA candles payload lacks a candles list")
            if not rows:
                break
            pages += 1
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ProviderDataError("malformed OANDA candle row")
                if not bool(row.get("complete", False)):
                    continue
                event_time = _parse_time(str(row["time"]))
                if event_time < start_time or event_time > end_time:
                    continue
                mid = row.get("mid")
                if not isinstance(mid, Mapping):
                    raise ProviderDataError("OANDA candle lacks mid prices")
                key = f"{symbol}:{granularity}:{event_time.isoformat()}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    BarRecord(
                        symbol=symbol,
                        venue=self.venue,
                        instrument_id=self._instrument_id(symbol),
                        event_time=event_time,
                        available_at=event_time + duration,
                        open=_float(mid.get("o"), "mid.o"),
                        high=_float(mid.get("h"), "mid.h"),
                        low=_float(mid.get("l"), "mid.l"),
                        close=_float(mid.get("c"), "mid.c"),
                        volume=_float(row.get("volume", 0), "volume"),
                        adjustment_status="raw",
                        provenance=_provenance(key, row, ingested).with_flags("mid_price"),
                    )
                )
            last_time = _parse_time(str(rows[-1]["time"]))
            next_cursor = last_time + duration
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(rows) < MAX_CANDLES:
                break

        records.sort(key=lambda record: record.event_time)
        return tuple(records)

    async def get_quote(self, instrument_id: str) -> QuoteTick:
        self.require_capability(Capability.QUOTES)
        account = self._require_account()
        symbol = self._symbol(instrument_id)
        payload, _ = await self._request_json(
            f"/v3/accounts/{account}/pricing",
            {"instruments": symbol},
            "get_quote",
        )
        prices = payload.get("prices") if isinstance(payload, Mapping) else None
        if not isinstance(prices, list) or not prices:
            raise ProviderDataError(f"OANDA returned no pricing for {symbol}")
        row = prices[0]
        if not isinstance(row, Mapping):
            raise ProviderDataError("malformed OANDA pricing row")
        bids = row.get("bids")
        asks = row.get("asks")
        if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
            raise ProviderDataError("OANDA pricing row lacks bid/ask levels")
        event_time = _parse_time(str(row.get("time", "")))
        captured = now_utc()
        return QuoteTick(
            instrument_id=self._instrument_id(symbol),
            venue=self.venue,
            event_time=event_time,
            available_at=event_time,
            bid=_float(bids[0].get("price"), "bid.price"),
            ask=_float(asks[0].get("price"), "ask.price"),
            bid_size=_float(bids[0].get("liquidity", 0), "bid.liquidity"),
            ask_size=_float(asks[0].get("liquidity", 0), "ask.liquidity"),
            provenance=Provenance(
                source="oanda",
                source_id=f"{symbol}:pricing",
                source_hash=sha256_text(json.dumps(row, sort_keys=True, default=str)),
                ingestion_timestamp=captured,
            ),
        )

    async def health_check(self) -> ProviderHealth:
        self.require_capability(Capability.HEALTH)
        started = monotonic()
        checked = now_utc()
        try:
            await self._request_json(
                "/v3/instruments/EUR_USD/candles",
                {"count": 1, "granularity": "M1", "price": "M"},
                "health_check",
            )
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


__all__ = ["LIVE_URL", "PRACTICE_URL", "OandaMarketData"]
