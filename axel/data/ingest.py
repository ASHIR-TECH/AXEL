"""Safe, point-in-time CSV ingestion. Network source adapters belong above this boundary."""

import csv
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO

from axel.data.models import Bar


def parse_ohlcv_csv(payload: str, *, source: str) -> list[Bar]:
    """Parse a provider export without trusting provider-specific code or timestamps."""
    if not source.strip():
        raise ValueError("source is required")
    reader = csv.DictReader(StringIO(payload))
    required = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("OHLCV CSV is missing required columns")
    bars = []
    for row in reader:
        timestamp = datetime.fromisoformat(row["timestamp"])
        if timestamp.tzinfo is None:
            raise ValueError("provider timestamp must include a timezone")
        bars.append(Bar(
            symbol=row["symbol"].upper(), timestamp=timestamp.astimezone(UTC),
            open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
            close=float(row["close"]), volume=float(row["volume"]),
            timeframe=row.get("timeframe") or "1d",
        ))
    return sorted(bars, key=lambda bar: (bar.symbol, bar.timestamp))


def content_hash(items: Iterable[Bar]) -> str:
    """Stable dedupe key for an ingestion batch."""
    serialized = "\n".join(repr(item) for item in items)
    return sha256(serialized.encode()).hexdigest()
