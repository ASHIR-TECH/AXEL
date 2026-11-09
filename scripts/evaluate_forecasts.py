"""Score forecast predictions and apply the admission gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from axel.training.dataset import load_bars_csv
from axel.training.evaluation import evaluate, load_predictions_csv, trend_baseline_sharpe


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate forecast predictions")
    parser.add_argument("--predictions", required=True, type=Path, help="predictions.csv")
    parser.add_argument("--bars", required=True, type=Path, help="bars.csv used for the baseline")
    parser.add_argument("--out", type=Path, default=None, help="write report JSON here")
    parser.add_argument("--fee-bps", type=float, default=1.0)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    args = parser.parse_args()

    predictions = load_predictions_csv(args.predictions)
    bars = load_bars_csv(args.bars)
    baseline = trend_baseline_sharpe(
        bars, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps
    )
    report = evaluate(
        predictions,
        baseline_sharpe=baseline,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )

    payload = report.to_dict()
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
