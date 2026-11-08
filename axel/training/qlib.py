"""Convert AXEL's canonical bars CSV into the Qlib layout Kronos expects.

Kronos fine-tunes through Qlib, so the handoff needs one more step between
``bars.csv`` and ``finetune/qlib_data_preprocess.py``. This module writes one
CSV per symbol and can invoke Qlib's own ``dump_bin.py`` to produce the binary
``calendars/``, ``instruments/`` and ``features/`` directories.

The ``vwap`` column is a documented typical-price proxy because Alpaca's IEX
bars do not carry a VWAP. Kronos only uses it as an input feature.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from axel.data.models import Bar

DAILY_FORMAT = "%Y-%m-%d"
INTRADAY_FORMAT = "%Y-%m-%d %H:%M:%S"
QLIB_COLUMNS = ("date", "open", "high", "low", "close", "volume", "factor", "vwap")


def _date(value: datetime, freq: str) -> str:
    return value.strftime(DAILY_FORMAT if freq == "day" else INTRADAY_FORMAT)


def typical_price(bar: Bar) -> float:
    """Alpaca IEX bars have no VWAP; use the standard typical price."""
    return (bar.open + bar.high + bar.low + bar.close) / 4.0


def write_qlib_csvs(bars: list[Bar], out_dir: Path, *, freq: str = "day") -> dict[str, int]:
    """Write ``<out_dir>/<SYMBOL>.csv`` files in Qlib's expected schema."""
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[bar.symbol.upper()].append(bar)

    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for symbol, series in sorted(grouped.items()):
        series.sort(key=lambda bar: bar.timestamp)
        path = out_dir / f"{symbol}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(QLIB_COLUMNS)
            for bar in series:
                writer.writerow(
                    (
                        _date(bar.timestamp, freq),
                        f"{bar.open:.8g}",
                        f"{bar.high:.8g}",
                        f"{bar.low:.8g}",
                        f"{bar.close:.8g}",
                        f"{bar.volume:.8g}",
                        "1.0",
                        f"{typical_price(bar):.8g}",
                    )
                )
        counts[symbol] = len(series)
    return counts


def dump_bin_command(
    csv_dir: Path, qlib_dir: Path, qlib_script: Path, *, freq: str = "day"
) -> list[str]:
    """The exact Qlib command that turns per-symbol CSVs into binary data."""
    return [
        sys.executable,
        str(qlib_script),
        "dump_all",
        "--data_path",
        str(csv_dir),
        "--qlib_dir",
        str(qlib_dir),
        "--freq",
        freq,
        "--date_field_name",
        "date",
    ]


def dump_qlib_bin(
    csv_dir: Path, qlib_dir: Path, qlib_script: Path, *, freq: str = "day"
) -> None:
    if not qlib_script.exists():
        raise FileNotFoundError(f"Qlib dump_bin.py not found: {qlib_script}")
    subprocess.run(
        dump_bin_command(csv_dir, qlib_dir, qlib_script, freq=freq),
        check=True,
    )


__all__ = [
    "DAILY_FORMAT",
    "INTRADAY_FORMAT",
    "QLIB_COLUMNS",
    "dump_bin_command",
    "dump_qlib_bin",
    "typical_price",
    "write_qlib_csvs",
]
