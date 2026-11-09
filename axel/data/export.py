"""Export durable OHLCV rows to a training dataset with a verifiable manifest.

The export is intentionally boring: one CSV, sorted deterministically, with a
content hash. That makes a training run reproducible and lets the model artifact
record exactly which data produced it.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from axel.data.models import Bar
from axel.db.models import OhlcvBar

DATASET_SCHEMA_VERSION = "ohlcv-csv-1"
BAR_COLUMNS = ("symbol", "timestamp", "open", "high", "low", "close", "volume", "timeframe")


@dataclass(frozen=True)
class DatasetManifest:
    """Everything needed to identify and reproduce a dataset export."""

    schema_version: str
    created_at: str
    row_count: int
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    start: str | None
    end: str | None
    content_sha256: str
    source: str = "axel.db.ohlcv_bars"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def write_bars_csv(bars: Iterable[Bar], destination: Path) -> str:
    """Write bars in the canonical training schema; return the content hash."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(bars, key=lambda bar: (bar.symbol, bar.timestamp))
    digest = hashlib.sha256()
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(BAR_COLUMNS)
        digest.update((",".join(BAR_COLUMNS) + "\n").encode())
        for bar in ordered:
            row = (
                bar.symbol,
                _iso(bar.timestamp),
                f"{bar.open:.8g}",
                f"{bar.high:.8g}",
                f"{bar.low:.8g}",
                f"{bar.close:.8g}",
                f"{bar.volume:.8g}",
                bar.timeframe,
            )
            line = ",".join(str(value) for value in row) + "\n"
            handle.write(line)
            digest.update(line.encode())
    return digest.hexdigest()


def export_bars_csv(
    session: Session,
    destination: Path,
    *,
    symbols: Iterable[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    timeframe: str | None = None,
) -> DatasetManifest:
    """Project ``ohlcv_bars`` into a CSV and return its manifest."""
    stmt = select(OhlcvBar).order_by(OhlcvBar.symbol.asc(), OhlcvBar.timestamp.asc())
    if symbols:
        stmt = stmt.where(OhlcvBar.symbol.in_(tuple(symbols)))
    if start is not None:
        stmt = stmt.where(OhlcvBar.timestamp >= start)
    if end is not None:
        stmt = stmt.where(OhlcvBar.timestamp <= end)
    if timeframe:
        stmt = stmt.where(OhlcvBar.timeframe == timeframe)

    rows = list(session.execute(stmt).scalars())
    bars = [
        Bar(
            symbol=row.symbol,
            timestamp=_as_utc(row.timestamp),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            timeframe=row.timeframe,
        )
        for row in rows
    ]
    content_hash = write_bars_csv(bars, destination)
    stamps = [bar.timestamp for bar in bars]
    return DatasetManifest(
        schema_version=DATASET_SCHEMA_VERSION,
        created_at=datetime.now(UTC).isoformat(),
        row_count=len(bars),
        symbols=tuple(sorted({bar.symbol for bar in bars})),
        timeframes=tuple(sorted({bar.timeframe for bar in bars})),
        start=_iso(min(stamps)) if stamps else None,
        end=_iso(max(stamps)) if stamps else None,
        content_sha256=content_hash,
    )


def export_dataset(
    session: Session,
    out_dir: Path,
    *,
    symbols: Iterable[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    timeframe: str | None = None,
) -> DatasetManifest:
    """Write ``bars.csv`` and ``manifest.json`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = export_bars_csv(
        session,
        out_dir / "bars.csv",
        symbols=symbols,
        start=start,
        end=end,
        timeframe=timeframe,
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "BAR_COLUMNS",
    "DATASET_SCHEMA_VERSION",
    "DatasetManifest",
    "export_bars_csv",
    "export_dataset",
    "write_bars_csv",
]
