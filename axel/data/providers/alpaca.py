"""Alpaca/Polygon-compatible market-data provider (read-only, IEX feed)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from axel.data.models import Bar
from axel.data.providers.base import ProviderResult, now_utc
from axel.data.providers.keypool import (
    AllCredentialsExhausted,
    Credential,
    CredentialPool,
    pool_from_pairs,
)
from axel.data.schemas import BarRecord, Provenance, RawPayload, parse_dt, sha256_text
from axel.observability.api_health import record_http_call

BASE_URL = "https://data.alpaca.markets/v2"
PAGE_LIMIT = 10_000
MAX_PAGES = 100

_TIMEFRAME_DURATIONS: dict[str, timedelta] = {
    "1Min": timedelta(minutes=1),
    "5Min": timedelta(minutes=5),
    "15Min": timedelta(minutes=15),
    "30Min": timedelta(minutes=30),
    "1Hour": timedelta(hours=1),
    "1Day": timedelta(days=1),
    "1Week": timedelta(weeks=1),
}


_ADJUSTMENT_STATUS = {
    "raw": "raw",
    "split": "split_adjusted",
    "all": "split_and_dividend_adjusted",
}


def timeframe_duration(timeframe: str) -> timedelta:
    """Bar availability offset: a bar becomes usable only once it has closed."""
    return _TIMEFRAME_DURATIONS.get(timeframe, timedelta(days=1))


def parse_bars(
    body_text: str,
    *,
    symbol: str,
    venue: str = "alpaca-iex",
    timeframe: str = "1Day",
    ingestion_timestamp: datetime | None = None,
    adjustment_status: str = "raw",
) -> list[BarRecord]:
    """Convert an Alpaca bars payload into canonical, availability-aware bar records."""
    payload = json.loads(body_text)
    raw_bars = payload.get("bars", {})
    if not isinstance(raw_bars, dict):
        raise TypeError("unexpected Alpaca payload: 'bars' must be an object")
    records: list[BarRecord] = []
    duration = timeframe_duration(timeframe)
    ingested = ingestion_timestamp or now_utc()
    for item in raw_bars.get(symbol.upper(), []):
        event_time = parse_dt(item["t"])
        records.append(
            BarRecord(
                symbol=symbol.upper(),
                venue=venue,
                event_time=event_time,
                available_at=event_time + duration,
                open=float(item["o"]),
                high=float(item["h"]),
                low=float(item["l"]),
                close=float(item["c"]),
                volume=float(item["v"]),
                adjustment_status=adjustment_status,
                provenance=Provenance(
                    source="alpaca",
                    source_id=f"{symbol.upper()}:{timeframe}",
                    source_hash=sha256_text(json.dumps(item, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    return records


def _is_rate_limited(response: httpx.Response) -> bool:
    """Alpaca signals limits with 429, and sometimes 403 plus a message."""
    if response.status_code == 429:
        return True
    return response.status_code == 403 and "rate limit" in response.text.lower()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse ``Retry-After`` as seconds or an HTTP date; ``None`` if absent."""
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class _RateLimited(Exception):
    def __init__(self, response: httpx.Response, retry_after_s: float | None) -> None:
        super().__init__(f"rate limited with HTTP {response.status_code}")
        self.response = response
        self.retry_after_s = retry_after_s


class AlpacaMarketData:
    """Read-only Alpaca market-data client. Holds no broker/trading credentials use.

    Pass ``api_key``/``secret_key`` for a single account, or ``pool`` to rotate
    across several. On a rate limit the pool quarantines that account and the
    request is retried with the next one; a mid-pagination switch restarts the
    page sequence, which is safe because these GETs are idempotent.
    """

    name = "alpaca"

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        client: httpx.Client | None = None,
        venue: str = "alpaca-iex",
        adjustment: str = "raw",
        *,
        pool: CredentialPool | None = None,
        max_key_switch_attempts: int = 3,
    ) -> None:
        if adjustment not in _ADJUSTMENT_STATUS:
            raise ValueError(
                f"invalid adjustment {adjustment!r}; expected one of {sorted(_ADJUSTMENT_STATUS)}"
            )
        if pool is not None and (api_key or secret_key):
            raise ValueError("pass either api_key/secret_key or pool, not both")
        if pool is None:
            if not api_key or not secret_key:
                raise ValueError("Alpaca market-data credentials are required")
            pool = pool_from_pairs([(api_key, secret_key)])
        if max_key_switch_attempts < 1:
            raise ValueError("max_key_switch_attempts must be at least 1")
        self.pool = pool
        self._client = client or httpx.Client(timeout=20)
        self.venue = venue
        self.adjustment = adjustment
        self._max_key_switch_attempts = max_key_switch_attempts

    def _headers_for(self, credential: Credential) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": credential.key,
            "APCA-API-SECRET-KEY": credential.secret,
        }

    def _fetch_once(
        self,
        credential: Credential,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str,
    ) -> tuple[list[BarRecord], list[dict[str, object]], int, httpx.Response]:
        headers = self._headers_for(credential)
        records: list[BarRecord] = []
        raw_items: list[dict[str, object]] = []
        page_token: str | None = None
        page_count = 0
        response: httpx.Response | None = None
        for _ in range(MAX_PAGES):
            page_count += 1
            params: dict[str, str | int] = {
                "symbols": symbol.upper(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "timeframe": timeframe,
                "feed": "iex",
                "limit": PAGE_LIMIT,
                "adjustment": self.adjustment,
            }
            if page_token:
                params["page_token"] = page_token
            response = self._client.get(
                f"{BASE_URL}/stocks/bars",
                headers=headers,
                params=params,
            )
            record_http_call("alpaca-data", "fetch_bars", response)
            if _is_rate_limited(response):
                raise _RateLimited(response, _retry_after_seconds(response))
            response.raise_for_status()
            records.extend(
                parse_bars(
                    response.text,
                    symbol=symbol,
                    venue=self.venue,
                    timeframe=timeframe,
                    adjustment_status=_ADJUSTMENT_STATUS[self.adjustment],
                )
            )
            payload = response.json()
            page_bars = payload.get("bars", {})
            if isinstance(page_bars, dict):
                raw_items.extend(page_bars.get(symbol.upper(), []))
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        else:
            raise RuntimeError(
                f"Alpaca pagination exceeded {MAX_PAGES} pages for {symbol} {timeframe}"
            )
        assert response is not None
        return records, raw_items, page_count, response

    def fetch_bars(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> ProviderResult:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be ordered timezone-aware datetimes")
        last_limited: _RateLimited | None = None
        for _ in range(self._max_key_switch_attempts):
            try:
                credential = self.pool.acquire()
            except AllCredentialsExhausted:
                if last_limited is not None:
                    last_limited.response.raise_for_status()
                raise
            try:
                records, raw_items, page_count, response = self._fetch_once(
                    credential, symbol, start, end, timeframe
                )
            except _RateLimited as exc:
                self.pool.report_rate_limited(credential, exc.retry_after_s)
                last_limited = exc
                continue
            self.pool.report_success(credential)
            body = json.dumps(
                {"bars": {symbol.upper(): raw_items}},
                separators=(",", ":"),
                sort_keys=True,
            )
            raw = RawPayload(
                source="alpaca",
                request=str(response.request.url),
                fetched_at=now_utc(),
                body=body,
                context=(
                    ("symbol", symbol.upper()),
                    ("venue", self.venue),
                    ("timeframe", timeframe),
                    ("pages", str(page_count)),
                    ("credentials", str(self.pool.size)),
                ),
            )
            return ProviderResult(raw=raw, records=tuple(records))

        if last_limited is not None:
            last_limited.response.raise_for_status()
        raise RuntimeError("Alpaca request failed without a usable credential")

    def fetch(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> ProviderResult:
        return self.fetch_bars(symbol, start, end, timeframe)

    def bars(self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day") -> list[Bar]:
        """Legacy thin wrapper returning the pre-Phase-2 ``Bar`` model."""
        result = self.fetch_bars(symbol, start, end, timeframe)
        return [
            Bar(
                symbol=record.symbol,
                timestamp=record.event_time,
                open=record.open,
                high=record.high,
                low=record.low,
                close=record.close,
                volume=record.volume,
                timeframe=timeframe,
            )
            for record in result.records
        ]


def _raw_from_response(
    response: httpx.Response, *, symbol: str, venue: str, timeframe: str
) -> RawPayload:
    return RawPayload(
        source="alpaca",
        request=str(response.request.url),
        fetched_at=now_utc(),
        body=response.text,
        context=(
            ("symbol", symbol.upper()),
            ("venue", venue),
            ("timeframe", timeframe),
        ),
    )
