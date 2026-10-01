"""FRED macro provider with vintage/revision awareness (point-in-time)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from axel.data.providers.base import ProviderResult, now_utc
from axel.data.schemas import MacroObservation, Provenance, RawPayload, sha256_text

ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"


def _midnight(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def parse_observations(
    body_text: str, *, series_id: str, ingestion_timestamp: datetime | None = None
) -> list[MacroObservation]:
    """Convert a FRED observations payload into vintage-aware macro records."""
    payload = json.loads(body_text)
    observations = payload.get("observations", [])
    if not isinstance(observations, list):
        raise TypeError("unexpected FRED payload: 'observations' must be a list")
    ingested = ingestion_timestamp or now_utc()
    records: list[MacroObservation] = []
    for item in observations:
        value = item.get("value")
        if value in (None, ".", ""):
            continue
        event_time = _midnight(item["date"])
        vintage = item.get("realtime_end") or item.get("realtime_start") or item["date"]
        available_at = _midnight(item.get("realtime_start") or item["date"])
        records.append(
            MacroObservation(
                series_id=series_id.upper(),
                event_time=event_time,
                available_at=available_at,
                value=float(value),
                vintage=vintage,
                provenance=Provenance(
                    source="fred",
                    source_id=series_id.upper(),
                    source_hash=sha256_text(json.dumps(item, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    return records


class FredData:
    """Read-only FRED client; ``realtime_as_of`` pins the vintage used."""

    name = "fred"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("FRED_API_KEY is required")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=20)

    def fetch_observations(self, series_id: str, *, realtime_as_of: str) -> ProviderResult:
        response = self._client.get(
            ENDPOINT,
            params={
                "series_id": series_id,
                "api_key": self._api_key,
                "file_type": "json",
                "realtime_start": realtime_as_of,
                "realtime_end": realtime_as_of,
            },
        )
        response.raise_for_status()
        records = parse_observations(response.text, series_id=series_id)
        raw = RawPayload(
            source="fred",
            request=str(response.request.url),
            fetched_at=now_utc(),
            body=response.text,
            context=(("series_id", series_id.upper()), ("realtime_as_of", realtime_as_of)),
        )
        return ProviderResult(raw=raw, records=tuple(records))

    def fetch(self, series_id: str, *, realtime_as_of: str) -> ProviderResult:
        return self.fetch_observations(series_id, realtime_as_of=realtime_as_of)

    def observations(self, series_id: str, *, realtime_as_of: str) -> list[dict[str, str]]:
        """Legacy thin wrapper returning raw observation dicts with values present."""
        response = self._client.get(
            ENDPOINT,
            params={
                "series_id": series_id,
                "api_key": self._api_key,
                "file_type": "json",
                "realtime_start": realtime_as_of,
                "realtime_end": realtime_as_of,
            },
        )
        response.raise_for_status()
        return [item for item in response.json().get("observations", []) if item.get("value") != "."]
