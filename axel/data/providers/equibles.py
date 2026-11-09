"""Equibles read-only market-data provider.

Equibles exposes a REST API at ``https://api.equibles.com/v1`` authenticated
with a Bearer key (``eq_...``). This adapter covers the daily-prices endpoint:

    GET /stocks/{symbol}/prices?limit=&offset=&startDate=&endDate=

The response is paginated newest-first::

    {"data": [{"date": "2026-07-10", "open": ..., "close": ..., "volume": ...}],
     "meta": {"limit": 5, "offset": 0, "count": 5, "hasMore": true}}

Only daily bars are supported; asking for another timeframe fails loudly rather
than returning data that looks valid but is not. Several keys can be supplied
through a credential pool, matching the Alpaca data rotation.
"""

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
from axel.data.schemas import BarRecord, Provenance, RawPayload, sha256_text
from axel.observability.api_health import record_http_call

BASE_URL = "https://api.equibles.com/v1"
PAGE_LIMIT = 500
MAX_PAGES = 100
SUPPORTED_TIMEFRAMES = frozenset({"1Day"})
_AVAILABILITY = timedelta(days=1)


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    return response.status_code == 403 and "rate limit" in response.text.lower()


def _retry_after_seconds(response: httpx.Response) -> float | None:
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


def _event_time(value: str) -> datetime:
    """Equibles returns date-only strings; treat them as UTC midnight."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_prices(
    body_text: str,
    *,
    symbol: str,
    venue: str = "equibles",
    adjustment_status: str = "raw",
    ingestion_timestamp: datetime | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[BarRecord]:
    """Convert a prices page (or merged pages) into canonical bar records."""
    payload = json.loads(body_text)
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        raise TypeError("unexpected Equibles payload: 'data' must be a list")
    ingested = ingestion_timestamp or now_utc()
    records: list[BarRecord] = []
    for item in rows:
        event_time = _event_time(str(item["date"]))
        if start is not None and event_time < start:
            continue
        if end is not None and event_time > end:
            continue
        records.append(
            BarRecord(
                symbol=symbol.upper(),
                venue=venue,
                event_time=event_time,
                available_at=event_time + _AVAILABILITY,
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item["volume"]),
                adjustment_status=adjustment_status,
                provenance=Provenance(
                    source="equibles",
                    source_id=f"{symbol.upper()}:1Day:{event_time.date().isoformat()}",
                    source_hash=sha256_text(json.dumps(item, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    records.sort(key=lambda record: record.event_time)
    return records


class EquiblesMarketData:
    """Read-only Equibles market-data client for daily stock prices."""

    name = "equibles"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        venue: str = "equibles",
        page_limit: int = PAGE_LIMIT,
        max_pages: int = MAX_PAGES,
        pool: CredentialPool | None = None,
        max_key_switch_attempts: int = 3,
    ) -> None:
        if pool is not None and api_key:
            raise ValueError("pass either api_key or pool, not both")
        if pool is None:
            if not api_key or not api_key.strip():
                raise ValueError("Equibles API key is required")
            # Equibles has no separate secret; the key is its own token.
            pool = pool_from_pairs([(api_key.strip(), api_key.strip())])
        if page_limit < 1:
            raise ValueError("page_limit must be at least 1")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if max_key_switch_attempts < 1:
            raise ValueError("max_key_switch_attempts must be at least 1")
        self.pool = pool
        self._client = client or httpx.Client(timeout=20)
        self.base_url = base_url.rstrip("/")
        self.venue = venue
        self.page_limit = page_limit
        self.max_pages = max_pages
        self._max_key_switch_attempts = max_key_switch_attempts

    def _headers_for(self, credential: Credential) -> dict[str, str]:
        return {"Authorization": f"Bearer {credential.secret or credential.key}"}

    def _fetch_once(
        self,
        credential: Credential,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[dict[str, object]], int, httpx.Response]:
        headers = self._headers_for(credential)
        rows: list[dict[str, object]] = []
        offset = 0
        page_count = 0
        response: httpx.Response | None = None
        for _ in range(self.max_pages):
            page_count += 1
            response = self._client.get(
                f"{self.base_url}/stocks/{symbol.upper()}/prices",
                headers=headers,
                params={
                    "limit": self.page_limit,
                    "offset": offset,
                    "startDate": start.date().isoformat(),
                    "endDate": end.date().isoformat(),
                },
            )
            record_http_call("equibles", "fetch_bars", response)
            if _is_rate_limited(response):
                raise _RateLimited(response, _retry_after_seconds(response))
            response.raise_for_status()
            payload = response.json()
            page = payload.get("data") or []
            if not isinstance(page, list):
                raise TypeError("unexpected Equibles payload: 'data' must be a list")
            rows.extend(page)
            meta = payload.get("meta") or {}
            if not page or not meta.get("hasMore"):
                break
            offset += len(page)
        else:
            raise RuntimeError(
                f"Equibles pagination exceeded {self.max_pages} pages for {symbol}"
            )
        assert response is not None
        return rows, page_count, response

    def fetch_bars(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> ProviderResult:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be ordered timezone-aware datetimes")
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"Equibles supports {sorted(SUPPORTED_TIMEFRAMES)} only, not {timeframe!r}"
            )

        last_limited: _RateLimited | None = None
        for _ in range(self._max_key_switch_attempts):
            try:
                credential = self.pool.acquire()
            except AllCredentialsExhausted:
                if last_limited is not None:
                    last_limited.response.raise_for_status()
                raise
            try:
                rows, page_count, response = self._fetch_once(
                    credential, symbol, start, end
                )
            except _RateLimited as exc:
                self.pool.report_rate_limited(credential, exc.retry_after_s)
                last_limited = exc
                continue
            self.pool.report_success(credential)
            body = json.dumps(
                {"data": rows, "meta": {"pages": page_count}},
                separators=(",", ":"),
                sort_keys=True,
            )
            records = parse_prices(
                body,
                symbol=symbol,
                venue=self.venue,
                start=start,
                end=end,
            )
            raw = RawPayload(
                source="equibles",
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
        raise RuntimeError("Equibles request failed without a usable credential")

    def fetch(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> ProviderResult:
        return self.fetch_bars(symbol, start, end, timeframe)

    def bars(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> list[Bar]:
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


__all__ = [
    "BASE_URL",
    "MAX_PAGES",
    "PAGE_LIMIT",
    "SUPPORTED_TIMEFRAMES",
    "EquiblesMarketData",
    "parse_prices",
]
