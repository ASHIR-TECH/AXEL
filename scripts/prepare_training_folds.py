"""Prepare walk-forward folds and a final holdout from a bars CSV."""

from __future__ import annotations

import argparse
from pathlib import Path

from axel.training.dataset import load_bars_csv, prepare_folds, write_folds, write_holdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare training folds")
    parser.add_argument("--bars", required=True, type=Path, help="bars.csv")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--train-size", type=int, required=True, help="timestamps per train fold")
    parser.add_argument("--test-size", type=int, required=True, help="timestamps per test fold")
    parser.add_argument("--step", type=int, default=None, help="fold stride (default: test-size)")
    parser.add_argument(
        "--holdout-fraction",
        type=float,
        default=0.2,
        help="final untouched holdout fraction (default: %(default)s)",
    )
    args = parser.parse_args()

    bars = load_bars_csv(args.bars)
    plans, folds = prepare_folds(
        bars, train_size=args.train_size, test_size=args.test_size, step=args.step
    )
    train_path, test_path = write_holdout(bars, args.out / "holdout", test_fraction=args.holdout_fraction)
    holdout_bars = load_bars_csv(test_path)
    holdout_stamps = sorted({bar.timestamp for bar in holdout_bars})
    holdout = {
        "start": holdout_stamps[0].isoformat(),
        "end": holdout_stamps[-1].isoformat(),
        "rows": len(holdout_bars),
    }
    path = write_folds(bars, plans, folds, args.out, holdout=holdout)

    print(f"symbols   {len({bar.symbol for bar in bars})}")
    print(f"bars      {len(bars)}")
    print(f"folds     {len(plans)}")
    for plan in plans:
        print(
            f"  fold_{plan.index:02d}  train={plan.train_rows:>7} "
            f"[{plan.train_start} -> {plan.train_end}]  "
            f"test={plan.test_rows:>7} [{plan.test_start} -> {plan.test_end}]"
        )
    print(f"manifest  {path}")
    print(f"holdout   {train_path} | {test_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
