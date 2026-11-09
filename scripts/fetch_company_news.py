"""Fetch Finnhub company news into a canonical JSONL file.

Read-only. The news text is data, never an instruction. Finnhub's free tier
covers this endpoint; historical candles are a separate paid resource.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from axel.core.config import settings
from axel.data.providers.finnhub import FinnhubMarketData
from axel.data.schemas import record_to_dict


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise SystemExit("timestamps must include a timezone, e.g. 2026-10-01T00:00:00+00:00")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Finnhub company news")
    parser.add_argument("--out", required=True, type=Path, help="destination JSONL path")
    parser.add_argument("--symbols", required=True, help="comma-separated symbols")
    parser.add_argument("--start", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--end", required=True, help="ISO timestamp with timezone")
    args = parser.parse_args()

    api_key = settings.finnhub_api_key.get_secret_value() if settings.finnhub_api_key else ""
    if not api_key:
        raise SystemExit("FINNHUB_API_KEY is required for company news")
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        raise SystemExit("--symbols must contain at least one symbol")

    provider = FinnhubMarketData(api_key=api_key, base_url=settings.finnhub_base_url)
    start = _timestamp(args.start)
    end = _timestamp(args.end)
    records = []
    for symbol in symbols:
        result = provider.fetch_news(symbol, start, end)
        records.extend(result.records)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record_to_dict(record), sort_keys=True) + "\n")

    print(f"articles  {len(records)}")
    print(f"written   {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
