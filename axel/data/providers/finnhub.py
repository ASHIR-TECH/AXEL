"""Finnhub read-only provider: company news, quotes and (paid) candles.

Finnhub authenticates with ``X-Finnhub-Token``. On the free tier the company
news, quote, search and metric endpoints work; the historical candle endpoint
is a paid resource. The adapter surfaces that restriction explicitly rather
than returning empty bars that look valid.

News is treated strictly as data, never as an instruction. Headline cashtags
are extracted only as entity hints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from axel.data.models import Bar
from axel.data.providers.base import ProviderDataError, ProviderResult, now_utc
from axel.data.providers.news import extract_entities
from axel.data.schemas import BarRecord, NewsRecord, Provenance, RawPayload, sha256_text
from axel.observability.api_health import record_http_call

BASE_URL = "https://finnhub.io/api/v1"

_RESOLUTIONS: dict[str, str] = {
    "1Min": "1",
    "5Min": "5",
    "15Min": "15",
    "30Min": "30",
    "1Hour": "60",
    "1Day": "D",
    "1Week": "W",
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


@dataclass(frozen=True)
class Quote:
    """A point-in-time Finnhub quote. ``price`` may be 0 outside market hours."""

    symbol: str
    price: float
    change: float
    percent_change: float
    high: float
    low: float
    open: float
    previous_close: float
    as_of: datetime


def parse_company_news(
    body_text: str,
    *,
    symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    ingestion_timestamp: datetime | None = None,
) -> list[NewsRecord]:
    """Convert a Finnhub company-news list into canonical news records."""
    payload = json.loads(body_text)
    if not isinstance(payload, list):
        raise ProviderDataError("unexpected Finnhub payload: company news must be a list")
    ingested = ingestion_timestamp or now_utc()
    records: list[NewsRecord] = []
    for item in payload:
        url = str(item.get("url") or "")
        title = str(item.get("headline") or "")
        if not url or not title:
            continue
        epoch = item.get("datetime")
        try:
            event_time = datetime.fromtimestamp(float(epoch), tz=UTC)
        except (TypeError, ValueError, OSError) as exc:
            raise ProviderDataError(
                f"malformed Finnhub news timestamp for {symbol}: {epoch!r}"
            ) from exc
        if start is not None and event_time < start:
            continue
        if end is not None and event_time > end:
            continue
        related = tuple(
            part.strip().upper()
            for part in str(item.get("related") or "").split(",")
            if part.strip()
        )
        entities = tuple(
            dict.fromkeys((*extract_entities(title), *related))
        )
        article_id = sha256_text(url)[:16]
        records.append(
            NewsRecord(
                article_id=article_id,
                publisher=str(item.get("source") or "finnhub"),
                event_time=event_time,
                available_at=event_time,
                title=title,
                body_ref=url,
                entities=entities,
                provenance=Provenance(
                    source="finnhub",
                    source_id=str(item.get("id") or article_id),
                    source_hash=sha256_text(json.dumps(item, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    records.sort(key=lambda record: record.event_time)
    return records


def parse_candles(
    body_text: str,
    *,
    symbol: str,
    timeframe: str = "1Day",
    venue: str = "finnhub",
    ingestion_timestamp: datetime | None = None,
) -> list[BarRecord]:
    """Convert a Finnhub ``/stock/candle`` payload into canonical bars."""
    payload = json.loads(body_text)
    if not isinstance(payload, dict):
        raise ProviderDataError("unexpected Finnhub candle payload: expected an object")
    status = payload.get("s")
    if status == "no_data":
        return []
    if status != "ok":
        message = payload.get("error") or status or "unknown error"
        raise ProviderDataError(f"Finnhub candles failed: {message}")
    try:
        times = payload["t"]
        opens = payload["o"]
        highs = payload["h"]
        lows = payload["l"]
        closes = payload["c"]
        volumes = payload["v"]
    except KeyError as exc:
        raise ProviderDataError(f"Finnhub candle payload missing {exc}") from exc
    if not (len(times) == len(opens) == len(highs) == len(lows) == len(closes) == len(volumes)):
        raise ProviderDataError("Finnhub candle arrays have mismatched lengths")
    ingested = ingestion_timestamp or now_utc()
    duration = _AVAILABILITY.get(timeframe, timedelta(days=1))
    records: list[BarRecord] = []
    for index, epoch in enumerate(times):
        event_time = datetime.fromtimestamp(float(epoch), tz=UTC)
        records.append(
            BarRecord(
                symbol=symbol.upper(),
                venue=venue,
                event_time=event_time,
                available_at=event_time + duration,
                open=float(opens[index]),
                high=float(highs[index]),
                low=float(lows[index]),
                close=float(closes[index]),
                volume=float(volumes[index]),
                adjustment_status="raw",
                provenance=Provenance(
                    source="finnhub",
                    source_id=f"{symbol.upper()}:{timeframe}:{event_time.isoformat()}",
                    source_hash=sha256_text(
                        json.dumps(
                            {
                                "t": epoch,
                                "o": opens[index],
                                "h": highs[index],
                                "l": lows[index],
                                "c": closes[index],
                                "v": volumes[index],
                            },
                            sort_keys=True,
                        )
                    ),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    records.sort(key=lambda record: record.event_time)
    return records


class FinnhubMarketData:
    """Read-only Finnhub client for company news, quotes and candles."""

    name = "finnhub"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        venue: str = "finnhub",
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Finnhub API key is required")
        self._headers = {"X-Finnhub-Token": api_key.strip()}
        self._client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")
        self.venue = venue

    def _get(self, path: str, params: dict[str, object]) -> httpx.Response:
        response = self._client.get(f"{self.base_url}{path}", headers=self._headers, params=params)
        record_http_call("finnhub", path.strip("/").replace("/", "_"), response)
        response.raise_for_status()
        return response

    def fetch_news(
        self, symbol: str, start: datetime, end: datetime
    ) -> ProviderResult:
        """Fetch company news for ``symbol`` between two timezone-aware dates."""
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be ordered timezone-aware datetimes")
        response = self._get(
            "/company-news",
            {
                "symbol": symbol.upper(),
                "from": start.date().isoformat(),
                "to": end.date().isoformat(),
            },
        )
        records = parse_company_news(
            response.text, symbol=symbol, start=start, end=end
        )
        raw = RawPayload(
            source="finnhub",
            request=str(response.request.url),
            fetched_at=now_utc(),
            body=response.text,
            context=(
                ("symbol", symbol.upper()),
                ("from", start.date().isoformat()),
                ("to", end.date().isoformat()),
            ),
        )
        return ProviderResult(raw=raw, records=tuple(records))

    def quote(self, symbol: str) -> Quote:
        """Fetch the latest quote for ``symbol``."""
        response = self._get("/quote", {"symbol": symbol.upper()})
        payload = response.json()
        try:
            return Quote(
                symbol=symbol.upper(),
                price=float(payload["c"]),
                change=float(payload["d"]),
                percent_change=float(payload["dp"]),
                high=float(payload["h"]),
                low=float(payload["l"]),
                open=float(payload["o"]),
                previous_close=float(payload["pc"]),
                as_of=datetime.fromtimestamp(float(payload["t"]), tz=UTC),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderDataError(f"malformed Finnhub quote for {symbol}: {exc}") from exc

    def fetch_bars(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> ProviderResult:
        """Fetch historical candles. This endpoint requires a paid Finnhub plan."""
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be ordered timezone-aware datetimes")
        if timeframe not in _RESOLUTIONS:
            raise ValueError(
                f"Finnhub supports {sorted(_RESOLUTIONS)} only, not {timeframe!r}"
            )
        response = self._get(
            "/stock/candle",
            {
                "symbol": symbol.upper(),
                "resolution": _RESOLUTIONS[timeframe],
                "from": int(start.timestamp()),
                "to": int(end.timestamp()),
            },
        )
        records = parse_candles(response.text, symbol=symbol, timeframe=timeframe, venue=self.venue)
        raw = RawPayload(
            source="finnhub",
            request=str(response.request.url),
            fetched_at=now_utc(),
            body=response.text,
            context=(
                ("symbol", symbol.upper()),
                ("venue", self.venue),
                ("timeframe", timeframe),
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
    "FinnhubMarketData",
    "Quote",
    "parse_candles",
    "parse_company_news",
]
