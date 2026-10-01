"""News provider (GDELT, keyless). External text is data, never an instruction."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx

from axel.data.providers.base import ProviderResult, now_utc
from axel.data.schemas import NewsRecord, Provenance, RawPayload, sha256_text

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# $TICKER / $TICKER1 styles found in headlines; used only as a hint, never as a command.
_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9.\-]{0,9})")


def _seen(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def extract_entities(title: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.upper() for match in _CASHTAG.findall(title)))


def parse_news(
    body_text: str, *, ingestion_timestamp: datetime | None = None
) -> list[NewsRecord]:
    """Convert a GDELT article list into canonical, provenance-carrying news records."""
    payload = json.loads(body_text)
    articles = payload.get("articles", [])
    if not isinstance(articles, list):
        raise TypeError("unexpected news payload: 'articles' must be a list")
    ingested = ingestion_timestamp or now_utc()
    records: list[NewsRecord] = []
    for item in articles:
        url = str(item.get("url", ""))
        title = str(item.get("title", ""))
        publisher = str(item.get("domain", "unknown"))
        if not url:
            continue
        published = item.get("seendate")
        event_time = _seen(published) if published else ingested
        records.append(
            NewsRecord(
                article_id=sha256_text(url)[:16],
                publisher=publisher,
                event_time=event_time,
                available_at=event_time,
                title=title,
                body_ref=url,
                entities=extract_entities(title),
                provenance=Provenance(
                    source="gdelt",
                    source_id=sha256_text(url)[:16],
                    source_hash=sha256_text(json.dumps(item, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    return records


class GdeltNews:
    """Keyless GDELT article search; injected client keeps tests deterministic."""

    name = "gdelt"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=20)

    def fetch_articles(self, query: str, *, max_records: int = 10) -> ProviderResult:
        if not query.strip():
            raise ValueError("query is required")
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        response = self._client.get(
            ENDPOINT,
            params={
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": max_records,
            },
        )
        response.raise_for_status()
        records = parse_news(response.text)
        raw = RawPayload(
            source="gdelt",
            request=str(response.request.url),
            fetched_at=now_utc(),
            body=response.text,
            context=(("query", query), ("max_records", str(max_records))),
        )
        return ProviderResult(raw=raw, records=tuple(records))

    def fetch(self, query: str, *, max_records: int = 10) -> ProviderResult:
        return self.fetch_articles(query, max_records=max_records)
