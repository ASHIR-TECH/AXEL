"""Convert a bars CSV into Qlib data for the Kronos fine-tune."""

from __future__ import annotations

import argparse
from pathlib import Path

from axel.training.dataset import load_bars_csv
from axel.training.qlib import dump_qlib_bin, write_qlib_csvs


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert bars.csv into Qlib format")
    parser.add_argument("--bars", required=True, type=Path, help="bars.csv")
    parser.add_argument("--out", required=True, type=Path, help="directory for per-symbol CSVs")
    parser.add_argument(
        "--qlib-dir",
        type=Path,
        default=None,
        help="destination for Qlib binary data (requires --qlib-script)",
    )
    parser.add_argument(
        "--qlib-script",
        type=Path,
        default=None,
        help="path to Qlib's scripts/dump_bin.py",
    )
    parser.add_argument("--freq", default="day", choices=("day", "1h"), help="Qlib frequency")
    args = parser.parse_args()

    if (args.qlib_dir is None) != (args.qlib_script is None):
        raise SystemExit("--qlib-dir and --qlib-script must be provided together")

    bars = load_bars_csv(args.bars)
    counts = write_qlib_csvs(bars, args.out, freq=args.freq)
    print(f"symbols   {len(counts)}")
    print(f"bars      {sum(counts.values())}")
    for symbol, count in counts.items():
        print(f"  {symbol:<8} {count:>7}")
    print(f"csv       {args.out}")

    if args.qlib_dir is not None and args.qlib_script is not None:
        dump_qlib_bin(args.out, args.qlib_dir, args.qlib_script, freq=args.freq)
        print(f"qlib      {args.qlib_dir}")
    else:
        print("next      run Qlib's dump_bin.py or pass --qlib-dir/--qlib-script")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
