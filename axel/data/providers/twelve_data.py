"""Twelve Data read-only OHLCV provider.

Twelve Data serves time series at ``https://api.twelvedata.com/time_series``
and authenticates with the ``apikey`` query parameter::

    GET /time_series?symbol=AAPL&interval=1day&start_date=...&apikey=...

The response is a JSON object with ``meta``, ``values`` and ``status``. Values
are newest-first unless ``order=ASC`` is requested, so the adapter sorts them
anyway. ``adjust`` controls corporate-action handling:

* ``none``      -> ``raw``
* ``splits``    -> ``split_adjusted``
* ``dividends`` -> ``dividend_adjusted``
* ``all``       -> ``split_and_dividend_adjusted``

Only read-only market data is exposed here; no account or order endpoints.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from axel.data.models import Bar
from axel.data.providers.base import ProviderDataError, ProviderResult, now_utc
from axel.data.schemas import BarRecord, Provenance, RawPayload, sha256_text
from axel.observability.api_health import record_http_call

BASE_URL = "https://api.twelvedata.com"
PAGE_LIMIT = 5_000

# Canonical timeframe -> Twelve Data interval.
_INTERVALS: dict[str, str] = {
    "1Min": "1min",
    "5Min": "5min",
    "15Min": "15min",
    "30Min": "30min",
    "1Hour": "1h",
    "1Day": "1day",
    "1Week": "1week",
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

_ADJUSTMENT_STATUS = {
    "none": "raw",
    "splits": "split_adjusted",
    "dividends": "dividend_adjusted",
    "all": "split_and_dividend_adjusted",
}


def _event_time(raw: str, exchange_timezone: str | None) -> datetime:
    """Parse a Twelve Data datetime, respecting the exchange timezone.

    Date-only daily bars become UTC midnight (matching the other providers).
    Intraday timestamps are exchange-local, so they are localized before being
    converted to UTC.
    """
    text = raw.strip().replace(" ", "T", 1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    if len(raw.strip()) <= 10:
        return parsed.replace(tzinfo=UTC)
    zone = UTC
    if exchange_timezone:
        try:
            zone = ZoneInfo(exchange_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            zone = UTC
    return parsed.replace(tzinfo=zone).astimezone(UTC)


def parse_time_series(
    body_text: str,
    *,
    symbol: str,
    timeframe: str = "1Day",
    venue: str = "twelve-data",
    adjustment_status: str = "raw",
    ingestion_timestamp: datetime | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[BarRecord]:
    """Convert a Twelve Data time-series payload into canonical bar records."""
    payload = json.loads(body_text)
    if not isinstance(payload, dict):
        raise ProviderDataError("unexpected Twelve Data payload: expected an object")
    status = payload.get("status")
    if status not in (None, "ok"):
        message = payload.get("message") or payload.get("code") or "unknown error"
        raise ProviderDataError(f"Twelve Data returned status {status!r}: {message}")
    values = payload.get("values") or []
    if not isinstance(values, list):
        raise ProviderDataError("unexpected Twelve Data payload: 'values' must be a list")

    meta = payload.get("meta") or {}
    exchange_timezone = meta.get("exchange_timezone")
    ingested = ingestion_timestamp or now_utc()
    duration = _AVAILABILITY.get(timeframe, timedelta(days=1))
    records: list[BarRecord] = []
    for item in values:
        event_time = _event_time(str(item["datetime"]), exchange_timezone)
        if start is not None and event_time < start:
            continue
        if end is not None and event_time > end:
            continue
        try:
            record = BarRecord(
                symbol=symbol.upper(),
                venue=venue,
                event_time=event_time,
                available_at=event_time + duration,
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item.get("volume") or 0.0),
                adjustment_status=adjustment_status,
                provenance=Provenance(
                    source="twelve-data",
                    source_id=f"{symbol.upper()}:{timeframe}:{event_time.isoformat()}",
                    source_hash=sha256_text(json.dumps(item, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderDataError(
                f"malformed Twelve Data bar for {symbol}: {exc}"
            ) from exc
        records.append(record)
    records.sort(key=lambda record: record.event_time)
    return records


class TwelveDataMarketData:
    """Read-only Twelve Data client for OHLCV time series."""

    name = "twelve-data"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        venue: str = "twelve-data",
        adjustment: str = "splits",
        output_size: int = PAGE_LIMIT,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Twelve Data API key is required")
        if adjustment not in _ADJUSTMENT_STATUS:
            raise ValueError(
                f"invalid adjustment {adjustment!r}; "
                f"expected one of {sorted(_ADJUSTMENT_STATUS)}"
            )
        if output_size < 1:
            raise ValueError("output_size must be at least 1")
        self._api_key = api_key.strip()
        self._client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")
        self.venue = venue
        self.adjustment = adjustment
        self.output_size = output_size

    def fetch_bars(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day"
    ) -> ProviderResult:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be ordered timezone-aware datetimes")
        if timeframe not in _INTERVALS:
            raise ValueError(
                f"Twelve Data supports {sorted(_INTERVALS)} only, not {timeframe!r}"
            )

        response = self._client.get(
            f"{self.base_url}/time_series",
            params={
                "symbol": symbol.upper(),
                "interval": _INTERVALS[timeframe],
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
                "order": "ASC",
                "outputsize": self.output_size,
                "adjust": self.adjustment,
                "apikey": self._api_key,
            },
        )
        record_http_call("twelve-data", "fetch_bars", response)
        response.raise_for_status()
        records = parse_time_series(
            response.text,
            symbol=symbol,
            timeframe=timeframe,
            venue=self.venue,
            adjustment_status=_ADJUSTMENT_STATUS[self.adjustment],
            start=start,
            end=end,
        )
        body = json.dumps(
            {"values": json.loads(response.text).get("values", [])},
            separators=(",", ":"),
            sort_keys=True,
        )
        raw = RawPayload(
            source="twelve-data",
            request=str(response.request.url),
            fetched_at=now_utc(),
            body=body,
            context=(
                ("symbol", symbol.upper()),
                ("venue", self.venue),
                ("timeframe", timeframe),
                ("adjustment", self.adjustment),
                ("output_size", str(self.output_size)),
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
    "PAGE_LIMIT",
    "TwelveDataMarketData",
    "parse_time_series",
]
