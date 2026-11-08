"""Fetch historical bars from Alpaca into the training CSV schema.

Read-only market data only. This script never places an order and does not use
the broker trading endpoint.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from axel.core.config import settings
from axel.data.historical import fetch_to_csv
from axel.data.providers.alpaca import AlpacaMarketData


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise SystemExit("timestamps must include a timezone, e.g. 2023-01-01T00:00:00+00:00")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch historical bars into a CSV")
    parser.add_argument("--out", required=True, type=Path, help="destination CSV path")
    parser.add_argument("--symbols", required=True, help="comma-separated symbols")
    parser.add_argument("--start", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--end", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--timeframe", default="1Hour", help="Alpaca timeframe (default: 1Hour)")
    args = parser.parse_args()

    api_key = settings.alpaca_api_key.get_secret_value() if settings.alpaca_api_key else ""
    secret_key = (
        settings.alpaca_secret_key.get_secret_value() if settings.alpaca_secret_key else ""
    )
    if not api_key or not secret_key:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for market data")

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    provider = AlpacaMarketData(api_key=api_key, secret_key=secret_key)
    rows, content_hash = fetch_to_csv(
        provider,
        symbols,
        _timestamp(args.start),
        _timestamp(args.end),
        args.out,
        timeframe=args.timeframe,
    )
    print(f"rows      {rows}")
    print(f"sha256    {content_hash}")
    print(f"written   {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
