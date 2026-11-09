"""Massive (formerly Polygon.io) read-only market-data provider.

Massive exposes aggregated OHLCV bars at ``https://api.massive.com``::

    GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}

Authentication uses the ``apiKey`` query parameter. Results are split-adjusted
by default (``adjusted=true``); the adapter records that explicitly rather than
letting a consumer mistake the series for raw prices.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from axel.data.models import Bar
from axel.data.providers.base import ProviderResult, now_utc
from axel.data.schemas import BarRecord, Provenance, RawPayload, sha256_text
from axel.observability.api_health import record_http_call

BASE_URL = "https://api.massive.com"
PAGE_LIMIT = 50_000
MAX_PAGES = 100

# timeframe -> (multiplier, timespan)
_TIMESPANS: dict[str, tuple[int, str]] = {
    "1Min": (1, "minute"),
    "5Min": (5, "minute"),
    "15Min": (15, "minute"),
    "30Min": (30, "minute"),
    "1Hour": (1, "hour"),
    "1Day": (1, "day"),
    "1Week": (1, "week"),
}

_AVAILABILITY: dict[str, timedelta] = {
    "1Min": timedelta(minutes=1),
    "5Min": timedelta(minutes=5),
    "15Min": timedelta(minutes=15),
    "30Min": timedelta(minutes=30),
    "1Hour": timedelta(hours=1),
    "1Day": timedelta(days=1),
    "1Week": timedelta(weeks=1),
}


def parse_aggregates(
    body_text: str,
    *,
    symbol: str,
    venue: str = "massive",
    timeframe: str = "1Day",
    adjustment_status: str = "split_adjusted",
    ingestion_timestamp: datetime | None = None,
) -> list[BarRecord]:
    """Convert Massive aggregate results into canonical bar records."""
    payload = json.loads(body_text)
    results = payload.get("results") or []
    if not isinstance(results, list):
        raise TypeError("unexpected Massive payload: 'results' must be a list")
    ingested = ingestion_timestamp or now_utc()
    duration = _AVAILABILITY.get(timeframe, timedelta(days=1))
    records: list[BarRecord] = []
    for item in results:
        event_time = datetime.fromtimestamp(float(item["t"]) / 1000.0, tz=UTC)
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
                    source="massive",
                    source_id=f"{symbol.upper()}:{timeframe}",
                    source_hash=sha256_text(json.dumps(item, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    records.sort(key=lambda record: record.event_time)
    return records


class MassiveMarketData:
    """Read-only Massive market-data client for aggregate bars."""

    name = "massive"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        venue: str = "massive",
        adjusted: bool = True,
        page_limit: int = PAGE_LIMIT,
        max_pages: int = MAX_PAGES,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Massive API key is required")
        if page_limit < 1:
            raise ValueError("page_limit must be at least 1")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self._api_key = api_key.strip()
        self._client = client or httpx.Client(timeout=20)
        self.base_url = base_url.rstrip("/")
        self.venue = venue
        self.adjusted = adjusted
        self.page_limit = page_limit
        self.max_pages = max_pages

    def _with_key(self, url: str) -> str:
        separator = "&" if "?" in url else "?"
        if "apiKey=" in url:
            return url
        return f"{url}{separator}apiKey={self._api_key}"

    def fetch_bars(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> ProviderResult:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be ordered timezone-aware datetimes")
        if timeframe not in _TIMESPANS:
            raise ValueError(
                f"Massive supports {sorted(_TIMESPANS)} only, not {timeframe!r}"
            )
        multiplier, timespan = _TIMESPANS[timeframe]
        url = (
            f"{self.base_url}/v2/aggs/ticker/{symbol.upper()}"
            f"/range/{multiplier}/{timespan}/{start.date().isoformat()}/{end.date().isoformat()}"
        )
        params = {
            "adjusted": "true" if self.adjusted else "false",
            "sort": "asc",
            "limit": self.page_limit,
            "apiKey": self._api_key,
        }

        results: list[dict[str, object]] = []
        page_count = 0
        response: httpx.Response | None = None
        next_url: str | None = url
        while next_url is not None and page_count < self.max_pages:
            page_count += 1
            if page_count == 1:
                response = self._client.get(next_url, params=params)
            else:
                response = self._client.get(self._with_key(next_url))
            record_http_call("massive", "fetch_bars", response)
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status")
            if status not in (None, "OK"):
                raise RuntimeError(f"Massive returned status {status!r}: {payload.get('error')}")
            page = payload.get("results") or []
            if not isinstance(page, list):
                raise TypeError("unexpected Massive payload: 'results' must be a list")
            results.extend(page)
            next_url = payload.get("next_url")
        if next_url is not None:
            raise RuntimeError(
                f"Massive pagination exceeded {self.max_pages} pages for {symbol}"
            )
        assert response is not None

        body = json.dumps({"results": results}, separators=(",", ":"), sort_keys=True)
        records = parse_aggregates(
            body,
            symbol=symbol,
            venue=self.venue,
            timeframe=timeframe,
            adjustment_status="split_adjusted" if self.adjusted else "raw",
        )
        raw = RawPayload(
            source="massive",
            request=str(response.request.url),
            fetched_at=now_utc(),
            body=body,
            context=(
                ("symbol", symbol.upper()),
                ("venue", self.venue),
                ("timeframe", timeframe),
                ("pages", str(page_count)),
            ),
        )
        return ProviderResult(raw=raw, records=tuple(records))

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
    "MassiveMarketData",
    "parse_aggregates",
]
