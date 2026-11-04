"""
Kalshi public prediction-market data adapter (read-only).

Kalshi is a binary-contract venue: a YES price is a probability in ``[0, 1]``
and both 0 and 1 are valid. This adapter therefore returns the dedicated
``PredictionQuote`` / ``PredictionTrade`` / ``PredictionBar`` /
``PredictionOrderBook`` records instead of forcing prediction markets through
equity-shaped validators.

Only public market-data endpoints are used. The adapter has no order, portfolio,
settlement or account capability. Authenticated endpoints are deliberately out
of scope; a caller that needs them should provide a separate, explicitly
authorised client.

Kalshi timestamps are venue timestamps. When an endpoint does not expose one
(the order-book snapshot), the capture time is used and the provenance carries
a ``capture_time_used`` flag so a backtest cannot mistake it for an exchange
timestamp.
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
from axel.data.prediction import (
    PredictionBar,
    PredictionMarketInfo,
    PredictionMarketStatus,
    PredictionOrderBook,
    PredictionQuote,
    PredictionResult,
    PredictionSide,
    PredictionTrade,
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
from axel.data.schemas import Provenance, sha256_text

KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
MAX_PAGE_SIZE = 1000
MAX_PAGES = 100
MAX_ORDER_BOOK_DEPTH = 100

_INTERVALS = {"1m": 1, "1h": 60, "1d": 1440}
_STATUS_BY_VALUE = {status.value: status for status in PredictionMarketStatus}
_RESULT_BY_VALUE = {
    "yes": PredictionResult.YES,
    "no": PredictionResult.NO,
    "void": PredictionResult.VOID,
}


def _parse_time(value: Any, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProviderDataError(f"invalid Kalshi timestamp in {field!r}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderDataError(f"Kalshi {field!r} timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(UTC)


def _float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"invalid Kalshi numeric field {field!r}: {value!r}") from exc


def _probability(
    row: Mapping[str, Any], cents_field: str, dollars_field: str
) -> float | None:
    """Read a Kalshi price, preferring the newer ``_dollars`` representation."""
    dollars = row.get(dollars_field)
    if dollars is not None and dollars != "":
        value = _float(dollars, dollars_field)
        if not 0.0 <= value <= 1.0:
            raise ProviderDataError(f"Kalshi {dollars_field!r} must be in [0,1]: {value!r}")
        return value
    cents = row.get(cents_field)
    if cents is None or cents == "":
        return None
    value = _float(cents, cents_field)
    if not 0.0 <= value <= 100.0:
        raise ProviderDataError(f"Kalshi {cents_field!r} must be in [0,100]: {value!r}")
    return value / 100.0


def _money(row: Mapping[str, Any], cents_field: str, dollars_field: str) -> float | None:
    dollars = row.get(dollars_field)
    if dollars is not None and dollars != "":
        value = _float(dollars, dollars_field)
        if value < 0:
            raise ProviderDataError(f"Kalshi {dollars_field!r} cannot be negative")
        return value
    cents = row.get(cents_field)
    if cents is None or cents == "":
        return None
    value = _float(cents, cents_field)
    if value < 0:
        raise ProviderDataError(f"Kalshi {cents_field!r} cannot be negative")
    return value / 100.0


def _number(
    row: Mapping[str, Any], field: str, fixed_point_field: str | None = None
) -> float | None:
    for name in (fixed_point_field, field):
        if name is None:
            continue
        value = row.get(name)
        if value is None or value == "":
            continue
        parsed = _float(value, name)
        if parsed < 0:
            raise ProviderDataError(f"Kalshi {name!r} cannot be negative")
        return parsed
    return None


def _orderbook_price(value: Any) -> float:
    """Kalshi's order book is in cents; tolerate decimal dollar strings too."""
    raw = str(value).strip()
    parsed = _float(value, "orderbook price")
    if "." in raw and 0.0 <= parsed <= 1.0:
        return parsed
    if not 0.0 <= parsed <= 100.0:
        raise ProviderDataError(f"Kalshi order-book price out of range: {value!r}")
    return parsed / 100.0


def _provenance(
    source_id: str, raw: object, ingested: datetime, *, flags: tuple[str, ...] = ()
) -> Provenance:
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    provenance = Provenance(
        source="kalshi",
        source_id=source_id,
        source_hash=sha256_text(encoded),
        ingestion_timestamp=ingested,
    )
    return provenance.with_flags(*flags) if flags else provenance


def _market_status(value: Any) -> PredictionMarketStatus:
    return _STATUS_BY_VALUE.get(str(value or "").strip().lower(), PredictionMarketStatus.UNKNOWN)


def _market_result(value: Any) -> PredictionResult:
    return _RESULT_BY_VALUE.get(str(value or "").strip().lower(), PredictionResult.UNRESOLVED)


class KalshiPredictionMarketData(CapabilityAwareProvider):
    """Read-only Kalshi binary-market data client."""

    name = "kalshi"
    capabilities = ProviderCapabilities(
        frozenset(
            {
                Capability.INSTRUMENTS,
                Capability.PREDICTION_MARKETS,
                Capability.PREDICTION_QUOTES,
                Capability.PREDICTION_ORDER_BOOK,
                Capability.PREDICTION_TRADES,
                Capability.PREDICTION_BARS,
                Capability.HEALTH,
            }
        )
    )

    def __init__(
        self,
        *,
        base_url: str = KALSHI_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        venue: str = "kalshi",
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("retry settings cannot be negative")
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._headers = dict(headers or {})
        self.venue = venue
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _instrument_id(self, ticker: str) -> str:
        return make_instrument_id(
            AssetClass.PREDICTION,
            self.venue,
            ticker.upper(),
            contract_type=ContractType.BINARY,
        )

    @staticmethod
    def _ticker(instrument_id: str) -> str:
        ticker = instrument_symbol(instrument_id).strip().upper()
        if not ticker:
            raise ValueError("instrument_id must contain a Kalshi ticker")
        return ticker

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

    async def _fetch_markets(
        self,
        *,
        status: str | None = None,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
    ) -> list[Mapping[str, Any]]:
        params: dict[str, Any] = {"limit": MAX_PAGE_SIZE}
        if status:
            params["status"] = status
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker

        collected: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        pages = 0
        while True:
            if pages >= MAX_PAGES:
                raise ProviderDataError(
                    f"Kalshi market pagination exceeded {MAX_PAGES} pages; narrow the query"
                )
            request_params = dict(params)
            if cursor:
                request_params["cursor"] = cursor
            payload, _ = await self._request_json("/markets", request_params, "get_markets")
            if not isinstance(payload, Mapping):
                raise ProviderDataError("Kalshi markets payload is not an object")
            rows = payload.get("markets")
            if not isinstance(rows, list):
                raise ProviderDataError("Kalshi markets payload lacks a markets list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ProviderDataError("malformed Kalshi market row")
                ticker = str(row.get("ticker", "")).strip()
                if not ticker:
                    raise ProviderDataError("Kalshi market row lacks a ticker")
                if ticker in seen:
                    continue
                seen.add(ticker)
                collected.append(row)
            next_cursor = payload.get("cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
            if cursor in seen_cursors:
                raise ProviderDataError("Kalshi market pagination cursor repeated")
            seen_cursors.add(cursor)
            pages += 1
        return collected

    def _instrument_from_market(self, row: Mapping[str, Any], ingested: datetime) -> Instrument | None:
        market_type = str(row.get("market_type", "binary") or "binary").strip().lower()
        if market_type not in {"binary", ""}:
            # Scalar/range contracts do not share binary YES/NO semantics.
            return None
        ticker = str(row["ticker"]).strip().upper()
        event_ticker = str(row.get("event_ticker", "")).strip()
        if not event_ticker:
            raise ProviderDataError(f"Kalshi market {ticker!r} lacks an event_ticker")
        price_structure = str(row.get("price_level_structure", "linear_cent")).lower()
        tick_size = 0.001 if price_structure == "deci_cent" else 0.01
        expiry = _parse_time(row.get("expiration_time"), "expiration_time") or _parse_time(
            row.get("close_time"), "close_time"
        )
        rules = f"{row.get('rules_primary', '')}\n{row.get('rules_secondary', '')}".strip()
        return Instrument(
            instrument_id=self._instrument_id(ticker),
            asset_class=AssetClass.PREDICTION,
            provider_id=self.name,
            provider_symbol=ticker,
            venue=self.venue,
            quote_currency="USD",
            settlement_currency="USD",
            exchange="KALSHI",
            contract_type=ContractType.BINARY,
            tick_size=tick_size,
            lot_size=1.0,
            minimum_quantity=1.0,
            expiry=expiry,
            trading_calendar="24/7",
            timezone="UTC",
            extra=(
                ("event_ticker", event_ticker),
                ("market_type", market_type or "binary"),
                ("status", _market_status(row.get("status")).value),
                ("result", _market_result(row.get("result")).value),
                ("price_level_structure", price_structure),
                ("category", str(row.get("category", ""))),
                ("rules_hash", sha256_text(rules)),
            ),
        )

    def _market_info(self, row: Mapping[str, Any], ingested: datetime) -> PredictionMarketInfo:
        ticker = str(row["ticker"]).strip().upper()
        event_ticker = str(row.get("event_ticker", "")).strip()
        if not event_ticker:
            raise ProviderDataError(f"Kalshi market {ticker!r} lacks an event_ticker")
        return PredictionMarketInfo(
            market_id=self._instrument_id(ticker),
            venue=self.venue,
            ticker=ticker,
            event_ticker=event_ticker,
            title=str(row.get("title") or ticker),
            status=_market_status(row.get("status")),
            provenance=_provenance(ticker, row, ingested),
            open_time=_parse_time(row.get("open_time"), "open_time"),
            close_time=_parse_time(row.get("close_time"), "close_time"),
            expiration_time=_parse_time(row.get("expiration_time"), "expiration_time"),
            settlement_time=_parse_time(row.get("settlement_time"), "settlement_time"),
            updated_time=_parse_time(row.get("updated_time"), "updated_time"),
            yes_bid=_probability(row, "yes_bid", "yes_bid_dollars"),
            yes_ask=_probability(row, "yes_ask", "yes_ask_dollars"),
            no_bid=_probability(row, "no_bid", "no_bid_dollars"),
            no_ask=_probability(row, "no_ask", "no_ask_dollars"),
            last_price=_probability(row, "last_price", "last_price_dollars"),
            volume=_number(row, "volume", "volume_fp") or 0.0,
            open_interest=_number(row, "open_interest", "open_interest_fp") or 0.0,
            liquidity=_money(row, "liquidity", "liquidity_dollars"),
            result=_market_result(row.get("result")),
            settlement_source="kalshi_rules",
            rules_primary=str(row.get("rules_primary", "")),
            rules_secondary=str(row.get("rules_secondary", "")),
            can_close_early=bool(row.get("can_close_early", False)),
            fractional_trading_enabled=bool(row.get("fractional_trading_enabled", False)),
            extra=(
                ("market_type", str(row.get("market_type", "binary") or "binary")),
                (
                    "price_level_structure",
                    str(row.get("price_level_structure", "linear_cent")),
                ),
                ("category", str(row.get("category", ""))),
            ),
        )

    async def get_instruments(self) -> Sequence[Instrument]:
        self.require_capability(Capability.INSTRUMENTS)
        ingested = now_utc()
        instruments = [
            instrument
            for row in await self._fetch_markets()
            if (instrument := self._instrument_from_market(row, ingested)) is not None
        ]
        return tuple(sorted(instruments, key=lambda instrument: instrument.provider_symbol))

    async def get_markets(
        self,
        *,
        status: str | None = None,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
    ) -> Sequence[PredictionMarketInfo]:
        self.require_capability(Capability.PREDICTION_MARKETS)
        ingested = now_utc()
        rows = await self._fetch_markets(
            status=status, event_ticker=event_ticker, series_ticker=series_ticker
        )
        return tuple(
            sorted(
                (self._market_info(row, ingested) for row in rows),
                key=lambda info: info.ticker,
            )
        )

    async def get_market(self, instrument_id: str) -> PredictionMarketInfo:
        self.require_capability(Capability.PREDICTION_MARKETS)
        ticker = self._ticker(instrument_id)
        payload, _ = await self._request_json(
            f"/markets/{ticker}", {}, "get_market"
        )
        row = payload.get("market") if isinstance(payload, Mapping) else None
        if not isinstance(row, Mapping):
            raise ProviderDataError(f"Kalshi returned no market object for {ticker}")
        return self._market_info(row, now_utc())

    async def get_prediction_quote(self, instrument_id: str) -> PredictionQuote:
        self.require_capability(Capability.PREDICTION_QUOTES)
        info = await self.get_market(instrument_id)
        missing = [
            name
            for name in ("yes_bid", "yes_ask", "no_bid", "no_ask")
            if getattr(info, name) is None
        ]
        if missing:
            raise ProviderDataError(
                f"Kalshi market {info.ticker!r} lacks quote fields: {', '.join(missing)}"
            )
        captured = now_utc()
        event_time = info.updated_time or captured
        if event_time > captured:
            raise ProviderDataError(
                f"Kalshi quote timestamp for {info.ticker!r} is in the future"
            )
        return PredictionQuote(
            instrument_id=info.market_id,
            venue=self.venue,
            event_time=event_time,
            available_at=captured,
            yes_bid=info.yes_bid,
            yes_ask=info.yes_ask,
            no_bid=info.no_bid,
            no_ask=info.no_ask,
            last_price=info.last_price,
            volume=info.volume,
            open_interest=info.open_interest,
            status=info.status,
            provenance=info.provenance,
        )

    @staticmethod
    def _levels(rows: Any, side: str) -> tuple[tuple[float, float], ...]:
        if not isinstance(rows, list):
            raise ProviderDataError(f"Kalshi order book lacks a {side!r} list")
        levels: list[tuple[float, float]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                raise ProviderDataError(f"malformed Kalshi {side!r} order-book level")
            price = _orderbook_price(row[0])
            quantity = _float(row[1], f"{side} quantity")
            if quantity < 0:
                raise ProviderDataError("Kalshi order-book quantity cannot be negative")
            if quantity == 0:
                continue
            levels.append((price, quantity))
        return tuple(levels)

    async def get_prediction_order_book(
        self, instrument_id: str, *, depth: int = 5
    ) -> PredictionOrderBook:
        self.require_capability(Capability.PREDICTION_ORDER_BOOK)
        if depth <= 0:
            raise ValueError("depth must be positive")
        ticker = self._ticker(instrument_id)
        payload, _ = await self._request_json(
            f"/markets/{ticker}/orderbook",
            {"depth": min(depth, MAX_ORDER_BOOK_DEPTH)},
            "get_prediction_order_book",
        )
        book = payload.get("orderbook") if isinstance(payload, Mapping) else None
        if not isinstance(book, Mapping):
            raise ProviderDataError(f"Kalshi returned no order book for {ticker}")
        yes_bids = tuple(sorted(self._levels(book.get("yes"), "yes"), key=lambda level: -level[0]))
        no_bids = tuple(sorted(self._levels(book.get("no"), "no"), key=lambda level: -level[0]))
        # Complement prices are derived; round away binary-float noise so a
        # locked book (YES bid 0.44, NO bid 0.56) is not seen as crossed.
        yes_asks = tuple(
            sorted(
                ((round(1.0 - price, 6), qty) for price, qty in no_bids),
                key=lambda level: level[0],
            )
        )
        no_asks = tuple(
            sorted(
                ((round(1.0 - price, 6), qty) for price, qty in yes_bids),
                key=lambda level: level[0],
            )
        )
        captured = now_utc()
        try:
            return PredictionOrderBook(
                instrument_id=self._instrument_id(ticker),
                venue=self.venue,
                event_time=captured,
                available_at=captured,
                yes_bids=yes_bids,
                yes_asks=yes_asks,
                no_bids=no_bids,
                no_asks=no_asks,
                provenance=_provenance(
                    f"{ticker}:orderbook",
                    book,
                    captured,
                    flags=("capture_time_used",),
                ),
            )
        except ValueError as exc:
            raise ProviderDataError(f"invalid Kalshi order book for {ticker}: {exc}") from exc

    async def get_prediction_trades(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[PredictionTrade]:
        self.require_capability(Capability.PREDICTION_TRADES)
        self.validate_window(start_time, end_time)
        ticker = self._ticker(instrument_id)
        ingested = now_utc()
        params: dict[str, Any] = {
            "ticker": ticker,
            "min_ts": int(start_time.timestamp()),
            "max_ts": int(end_time.timestamp()),
            "limit": MAX_PAGE_SIZE,
        }
        records: list[PredictionTrade] = []
        seen: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        pages = 0
        while True:
            if pages >= MAX_PAGES:
                raise ProviderDataError(
                    f"Kalshi trade pagination exceeded {MAX_PAGES} pages; narrow the window"
                )
            request_params = dict(params)
            if cursor:
                request_params["cursor"] = cursor
            payload, _ = await self._request_json(
                "/markets/trades", request_params, "get_prediction_trades"
            )
            rows = payload.get("trades") if isinstance(payload, Mapping) else None
            if not isinstance(rows, list):
                raise ProviderDataError("Kalshi trades payload lacks a trades list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ProviderDataError("malformed Kalshi trade row")
                trade_id = str(row.get("trade_id", "")).strip()
                if not trade_id:
                    raise ProviderDataError("Kalshi trade row lacks a trade_id")
                if trade_id in seen:
                    continue
                event_time = _parse_time(row.get("created_time"), "created_time")
                if event_time is None:
                    raise ProviderDataError("Kalshi trade row lacks created_time")
                if event_time < start_time or event_time > end_time:
                    continue
                yes_price = _probability(row, "yes_price", "yes_price_dollars")
                if yes_price is None:
                    raise ProviderDataError("Kalshi trade row lacks yes_price")
                quantity = _number(row, "count", "count_fp")
                if quantity is None or quantity <= 0:
                    raise ProviderDataError("Kalshi trade row has non-positive count")
                try:
                    taker_side = PredictionSide(str(row.get("taker_side", "")).lower())
                except ValueError:
                    taker_side = PredictionSide.UNKNOWN
                seen.add(trade_id)
                records.append(
                    PredictionTrade(
                        instrument_id=self._instrument_id(ticker),
                        venue=self.venue,
                        event_time=event_time,
                        available_at=event_time,
                        yes_price=yes_price,
                        quantity=quantity,
                        taker_side=taker_side,
                        trade_id=trade_id,
                        provenance=_provenance(f"{ticker}:trade:{trade_id}", row, ingested),
                    )
                )
            next_cursor = payload.get("cursor") if isinstance(payload, Mapping) else None
            if not next_cursor:
                break
            cursor = str(next_cursor)
            if cursor in seen_cursors:
                raise ProviderDataError("Kalshi trade pagination cursor repeated")
            seen_cursors.add(cursor)
            pages += 1
        records.sort(key=lambda record: (record.event_time, record.trade_id))
        return tuple(records)

    async def get_prediction_bars(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1h",
    ) -> Sequence[PredictionBar]:
        self.require_capability(Capability.PREDICTION_BARS)
        self.validate_window(start_time, end_time)
        ticker = self._ticker(instrument_id)
        minutes = _INTERVALS.get(interval.lower())
        if minutes is None:
            raise ValueError(f"unsupported Kalshi interval: {interval!r}")
        payload, _ = await self._request_json(
            "/markets/candlesticks",
            {
                "market_tickers": ticker,
                "start_ts": int(start_time.timestamp()),
                "end_ts": int(end_time.timestamp()),
                "period_interval": minutes,
            },
            "get_prediction_bars",
        )
        markets = payload.get("markets") if isinstance(payload, Mapping) else None
        if not isinstance(markets, list):
            raise ProviderDataError("Kalshi candlesticks payload lacks a markets list")
        selected: Mapping[str, Any] | None = None
        for entry in markets:
            if not isinstance(entry, Mapping):
                raise ProviderDataError("malformed Kalshi candlestick market entry")
            if str(entry.get("market_ticker", "")).upper() == ticker:
                selected = entry
                break
        if selected is None:
            return ()
        candles = selected.get("candlesticks")
        if not isinstance(candles, list):
            raise ProviderDataError("Kalshi candlestick entry lacks a candlesticks list")
        captured = now_utc()
        duration = timedelta(minutes=minutes)
        records: list[PredictionBar] = []
        for candle in candles:
            if not isinstance(candle, Mapping):
                raise ProviderDataError("malformed Kalshi candlestick row")
            end_ts = candle.get("end_period_ts")
            if end_ts is None:
                raise ProviderDataError("Kalshi candlestick lacks end_period_ts")
            available_at = datetime.fromtimestamp(int(end_ts), tz=UTC)
            event_time = available_at - duration
            if event_time < start_time or available_at > end_time or available_at > captured:
                continue
            prices = candle.get("price", candle)
            if not isinstance(prices, Mapping):
                raise ProviderDataError("Kalshi candlestick lacks a price object")
            open_price = _probability(prices, "open", "open_dollars")
            high_price = _probability(prices, "high", "high_dollars")
            low_price = _probability(prices, "low", "low_dollars")
            close_price = _probability(prices, "close", "close_dollars")
            if None in (open_price, high_price, low_price, close_price):
                raise ProviderDataError("Kalshi candlestick lacks OHLC prices")
            volume = _number(candle, "volume", "volume_fp") or 0.0
            records.append(
                PredictionBar(
                    instrument_id=self._instrument_id(ticker),
                    venue=self.venue,
                    event_time=event_time,
                    available_at=available_at,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                    provenance=_provenance(
                        f"{ticker}:candle:{int(end_ts)}", candle, captured
                    ),
                )
            )
        records.sort(key=lambda record: record.event_time)
        return tuple(records)

    async def health_check(self) -> ProviderHealth:
        self.require_capability(Capability.HEALTH)
        started = monotonic()
        checked = now_utc()
        try:
            payload, _ = await self._request_json("/exchange/status", {}, "health_check")
        except (ProviderRequestError, ProviderDataError) as exc:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                checked_at=checked,
                latency_ms=(monotonic() - started) * 1000.0,
                message=str(exc),
            )
        if not isinstance(payload, Mapping):
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                checked_at=checked,
                latency_ms=(monotonic() - started) * 1000.0,
                message="exchange status payload is not an object",
            )
        exchange_active = bool(payload.get("exchange_active", False))
        trading_active = bool(payload.get("trading_active", False))
        return ProviderHealth(
            provider=self.name,
            healthy=exchange_active,
            checked_at=checked,
            latency_ms=(monotonic() - started) * 1000.0,
            message="ok" if exchange_active else "exchange inactive",
            details=(("trading_active", str(trading_active)),),
        )


__all__ = ["KALSHI_BASE_URL", "KalshiPredictionMarketData"]
