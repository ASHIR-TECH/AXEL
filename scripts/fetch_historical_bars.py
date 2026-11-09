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
from axel.data.providers.equibles import EquiblesMarketData
from axel.data.providers.keypool import pool_from_pairs


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
    parser.add_argument(
        "--provider",
        choices=("alpaca", "equibles"),
        default="alpaca",
        help="market-data source (default: alpaca)",
    )
    parser.add_argument(
        "--adjustment",
        choices=("raw", "split", "all"),
        default="split",
        help="price adjustment for Alpaca (default: split; use all to include dividends)",
    )
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        raise SystemExit("--symbols must contain at least one symbol")

    if args.provider == "equibles":
        equibles_key = (
            settings.equibles_api_key.get_secret_value() if settings.equibles_api_key else ""
        )
        if not equibles_key:
            raise SystemExit("EQUIBLES_API_KEY is required for the equibles provider")
        if args.timeframe != "1Day":
            raise SystemExit("Equibles supports daily bars only; use --timeframe 1Day")
        provider = EquiblesMarketData(
            api_key=equibles_key, base_url=settings.equibles_base_url
        )
    else:
        pairs = settings.alpaca_credentials()
        if not pairs:
            raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for market data")
        provider = AlpacaMarketData(
            pool=pool_from_pairs(
                pairs,
                strategy=settings.alpaca_key_strategy,
                cooldown_s=settings.alpaca_key_cooldown_seconds,
            ),
            adjustment=args.adjustment,
        )
        if len(pairs) > 1:
            print(f"keys      {len(pairs)} ({settings.alpaca_key_strategy})")

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
