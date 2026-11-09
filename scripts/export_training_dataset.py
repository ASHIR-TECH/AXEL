"""Export durable OHLCV rows into a reproducible training dataset."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from axel.data.export import export_dataset
from axel.db.session import SessionLocal, init_db


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise SystemExit("--start/--end must include a timezone, e.g. 2024-01-01T00:00:00+00:00")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Export OHLCV bars as a training dataset")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--symbols", default="", help="comma-separated symbol filter")
    parser.add_argument("--start", default=None, help="ISO timestamp with timezone")
    parser.add_argument("--end", default=None, help="ISO timestamp with timezone")
    parser.add_argument("--timeframe", default=None, help="bar timeframe filter")
    parser.add_argument("--init-db", action="store_true", help="create tables first")
    args = parser.parse_args()

    if args.init_db:
        init_db()

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    with SessionLocal() as session:
        manifest = export_dataset(
            session,
            args.out,
            symbols=symbols or None,
            start=_timestamp(args.start),
            end=_timestamp(args.end),
            timeframe=args.timeframe,
        )

    print(f"rows      {manifest.row_count}")
    print(f"symbols   {', '.join(manifest.symbols) or '-'}")
    print(f"range     {manifest.start} -> {manifest.end}")
    print(f"sha256    {manifest.content_sha256}")
    print(f"written   {args.out / 'bars.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
