"""SEC EDGAR filings provider. Filings become usable only at their public filing time."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from axel.data.providers.base import ProviderResult, now_utc
from axel.data.schemas import FilingRecord, Provenance, RawPayload, sha256_text

BASE_URL = "https://data.sec.gov/submissions"

_FILING_FIELDS = (
    "form",
    "filingDate",
    "reportDate",
    "accessionNumber",
    "primaryDocument",
    "primaryDocDescription",
)


def _filing_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def parse_filings(
    body_text: str, *, cik: str, ingestion_timestamp: datetime | None = None
) -> list[FilingRecord]:
    """Convert an EDGAR submissions payload into canonical filing records."""
    payload = json.loads(body_text)
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    if not isinstance(forms, list):
        raise TypeError("unexpected EDGAR payload: 'filings.recent.form' must be a list")
    ingested = ingestion_timestamp or now_utc()
    issuer = str(cik).zfill(10)
    records: list[FilingRecord] = []
    for index in range(len(forms)):
        row = {
            field: str(recent.get(field, [""] * len(forms))[index])
            for field in _FILING_FIELDS
        }
        accession = row["accessionNumber"]
        if not accession:
            continue
        filed = row["filingDate"] or row["reportDate"]
        # ``reportDate`` is the period a filing *covers*, which is not always a
        # moment that has happened yet: a proxy statement (DEF 14A) is filed
        # weeks before the meeting date it reports on. Using it verbatim as the
        # economic event would place the event after the filing's own
        # availability and read as look-ahead. The event cannot precede the
        # public filing, so clamp it; the true reportDate stays in ``fields``.
        period = min(row["reportDate"] or filed, filed)
        records.append(
            FilingRecord(
                issuer_id=issuer,
                accession=accession,
                form=row["form"],
                event_time=_filing_date(period),
                available_at=_filing_date(filed),
                fields=tuple(row.items()),
                provenance=Provenance(
                    source="sec",
                    source_id=f"{issuer}:{accession}",
                    source_hash=sha256_text(json.dumps(row, sort_keys=True)),
                    ingestion_timestamp=ingested,
                ),
            )
        )
    return records


class SecEdgar:
    """Read-only EDGAR client requiring a contact User-Agent per SEC policy."""

    name = "sec"

    def __init__(self, user_agent: str, client: httpx.Client | None = None) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must identify a contact email")
        self._headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        self._client = client or httpx.Client(timeout=20)

    def _fetch_submissions(self, cik: str) -> httpx.Response:
        normalized = str(cik).zfill(10)
        if not normalized.isdigit():
            raise ValueError("CIK must be numeric")
        response = self._client.get(f"{BASE_URL}/CIK{normalized}.json", headers=self._headers)
        response.raise_for_status()
        return response

    def fetch_filings(self, cik: str) -> ProviderResult:
        response = self._fetch_submissions(cik)
        records = parse_filings(response.text, cik=cik)
        raw = RawPayload(
            source="sec",
            request=str(response.request.url),
            fetched_at=now_utc(),
            body=response.text,
            context=(("cik", str(cik).zfill(10)),),
        )
        return ProviderResult(raw=raw, records=tuple(records))

    def fetch(self, cik: str) -> ProviderResult:
        return self.fetch_filings(cik)

    def submissions(self, cik: str) -> dict[str, object]:
        """Legacy thin wrapper returning the raw submissions document."""
        return self._fetch_submissions(cik).json()
