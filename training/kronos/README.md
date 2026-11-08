# Kronos fine-tune runbook

This directory is the handoff package for **Scope A: fine-tune Kronos** and
turn the checkpoint into an AXEL model artifact. Read
`docs/gpu-training-handoff.md` first; this file is the exact sequence.

Nothing here is imported by `axel/risk`, `axel/execution` or `axel/portfolio`.
The model proposes signals. The deterministic core still authorizes every
order.

---

## 0. What you receive

```text
training/kronos/
  README.md                 # this file
scripts/
  fetch_historical_bars.py  # provider -> bars.csv
  export_training_dataset.py# DB -> bars.csv + manifest.json
  prepare_training_folds.py # bars.csv -> fold_NN/ + holdout/ + folds.json
  export_qlib_dataset.py    # bars.csv -> Qlib CSV + binary data
  evaluate_forecasts.py     # predictions.csv -> report.json + gate
  build_model_artifact.py   # checkpoint -> manifest.json
  train_kronos.py           # validate folds, emit/run Kronos commands
axel/training/              # dataset, evaluation, artifact, Qlib contracts
axel/agents/kronos.py       # proposal-only inference adapter
```

The interface between your GPU work and AXEL is one directory:

```text
artifacts/<model_id>/<version>/
  model.pt          # or model.safetensors
  manifest.json     # written by scripts/build_model_artifact.py
  predictions.csv   # held-out predictions used by the evaluation gate
  report.json       # written by scripts/evaluate_forecasts.py
```

---

## 1. Environment

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pip install -e '.[ml]'      # torch, pandas, numpy, pyarrow
.venv/bin/pip install pyqlib          # Qlib data loader + dump_bin.py
```

Clone Kronos and Qlib next to AXEL, not inside it:

```bash
git clone https://github.com/shiyu-coder/Kronos.git ../Kronos
git clone https://github.com/microsoft/qlib.git ../qlib
```

Verify the base system before training:

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
```

---

## 2. Build the dataset

The dataset for this handoff is already generated at `data/kronos/bars.csv`:

| Property | Value |
|---|---|
| Symbols | 21 (20 US large caps + `SPY` benchmark) |
| Rows | 33,147 daily bars |
| Range | 2020-07-27 → 2026-11-06 |
| Timestamps | 1,580 |
| Duplicates / zero volume | 0 / 0 |

Alpaca's free IEX feed has daily history from **2020-07-27** and intraday
history from **2023**. Use daily bars: Kronos's Qlib fine-tune path is built
around daily frequency.

Regenerate it with:

```bash
.venv/bin/python scripts/fetch_historical_bars.py \
  --symbols AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,UNH,JNJ,V,WMT,PG,HD,BAC,DIS,NFLX,AMD,CRM,SPY \
  --start 2020-01-01T00:00:00+00:00 \
  --end   2026-11-09T00:00:00+00:00 \
  --timeframe 1Day \
  --out data/kronos/bars.csv
```

If the bars are already in the AXEL database:

```bash
.venv/bin/python scripts/export_training_dataset.py \
  --out data/kronos/dataset --timeframe 1Day
```

Both paths write the same schema and a content hash:

```csv
symbol,timestamp,open,high,low,close,volume,timeframe
AAPL,2026-11-06T04:00:00+00:00,269.10,270.40,267.85,269.90,41230000,1Day
```

Rules: timezone-aware timestamps, one row per `(symbol, timestamp, timeframe)`,
sorted, no look-ahead. Do **not** train on paper-soak data.

---

## 3. Prepare folds

Splits are made on the shared timestamp axis, never per symbol and never
randomly.

```bash
.venv/bin/python scripts/prepare_training_folds.py \
  --bars data/kronos/bars.csv \
  --out  data/kronos/folds \
  --train-size 750 \
  --test-size 150 \
  --holdout-fraction 0.2
```

This produces five walk-forward folds plus a final holdout:

```text
data/kronos/folds/
  fold_00/train.csv, test.csv
  ...
  fold_04/train.csv, test.csv
  holdout/holdout_train.csv   26,511 rows, 2020-07-27 → 2025-08-06
  holdout/holdout_test.csv     6,636 rows, 2025-08-07 → 2026-11-06
  folds.json
```

The holdout is written once and must never be used for tuning. If you touch it
before the final report, the result is invalid.

---

## 3.5 Convert to Qlib

Kronos reads Qlib binary data, not the AXEL CSV. Convert it:

```bash
.venv/bin/python scripts/export_qlib_dataset.py \
  --bars data/kronos/bars.csv \
  --out data/kronos/qlib_csv \
  --qlib-dir data/kronos/qlib \
  --qlib-script ../qlib/scripts/dump_bin.py \
  --freq day
```

`--qlib-script` is the `scripts/dump_bin.py` shipped with Qlib
(`https://github.com/microsoft/qlib/blob/main/scripts/dump_bin.py`). If you omit
`--qlib-dir/--qlib-script`, the script writes the per-symbol CSVs and you run
Qlib's `dump_all` yourself.

Output:

```text
data/kronos/qlib/
  calendars/day.txt
  instruments/all.txt
  features/<symbol>/{open,high,low,close,volume,factor,vwap}.day.bin
```

`instruments/all.txt` is what `instrument = "all"` refers to in the Kronos
config. The `vwap` field is the OHLC typical price, because Alpaca IEX bars do
not carry a VWAP.

---

## 4. Fine-tune Kronos

`scripts/train_kronos.py` validates the fold manifest and writes the exact
Kronos commands. It does not import `torch`, so it is safe to run anywhere.

```bash
.venv/bin/python scripts/train_kronos.py \
  --folds data/kronos/folds/folds.json \
  --kronos-repo ../Kronos \
  --out artifacts/kronos-mini \
  --variant mini \
  --gpus 1 \
  --device cuda:0
```

This writes `train_plan.json` and `train.sh` and prints:

```text
python finetune/qlib_data_preprocess.py
torchrun --standalone --nproc_per_node=1 finetune/train_tokenizer.py
torchrun --standalone --nproc_per_node=1 finetune/train_predictor.py
python finetune/qlib_test.py --device cuda:0
```

Required edits to `../Kronos/finetune/config.py`:

| Setting | Value |
|---|---|
| `qlib_data_path` | `<abs>/data/kronos/qlib` |
| `instrument` | `"all"` |
| `dataset_begin_time` | `"2020-07-27"` |
| `dataset_end_time` | `"2026-11-06"` |
| `train_time_range` | `["2020-07-27", "2025-02-01"]` |
| `val_time_range` | `["2024-10-01", "2025-08-06"]` |
| `test_time_range` | `["2025-08-07", "2026-11-06"]` |
| `backtest_time_range` | `["2025-08-07", "2026-11-06"]` |
| `dataset_path` | `<abs>/data/kronos/processed` |
| `save_path` | `<abs>/artifacts/kronos-mini/runs` |
| `pretrained_tokenizer_path` | `NeoQuasar/Kronos-Tokenizer-2k` |
| `pretrained_predictor_path` | `NeoQuasar/Kronos-mini` |
| `backtest_n_symbol_hold` | `5` (must be ≤ universe size) |
| `backtest_n_symbol_drop` | `1` |
| `use_comet` | `False` |
| `epochs`, `batch_size` | start with `10` and `32` for mini |

The validation window deliberately starts four months before the training
window ends, so the first validation samples have the full 90-bar lookback. The
test window is disjoint from both.

Three source edits are required because the Kronos demo targets A-shares:

1. `finetune/qlib_data_preprocess.py` and `finetune/qlib_test.py`: replace
   `REG_CN` with `REG_US` in both the import and the `qlib.init` call.
2. `finetune/config.py`: `_set_benchmark("all")` raises. Replace
   `self.backtest_benchmark = self._set_benchmark(self.instrument)` with
   `self.backtest_benchmark = "SPY"`.
3. `finetune/qlib_test.py`: set `"limit_threshold": None`. The default `0.095`
   is an A-share ±10% limit that would wrongly skip US moves.

Then either run `./artifacts/kronos-mini/train.sh` or re-run the planner with
`--run`.

Model sizes: `mini` 4.1M (4–8 GB), `small` 24.7M (8–16 GB), `base` 102.3M
(16–24 GB). Start with `mini`; only scale up if the evaluation gate passes.

---

## 5. Evaluate before you ship

Produce `predictions.csv` on the **holdout only**:

```csv
symbol,as_of,expected_return,probability_up,realized_return
AAPL,2026-08-14T04:00:00+00:00,0.0041,0.62,0.0037
```

Then:

```bash
.venv/bin/python scripts/evaluate_forecasts.py \
  --predictions artifacts/kronos-mini/predictions.csv \
  --bars data/kronos/folds/holdout/holdout_test.csv \
  --out artifacts/kronos-mini/report.json
```

The script exits `1` when the model fails the gate. The gate is the existing
AXEL policy, not a new one:

| Gate | Requirement |
|---|---|
| Trades | ≥ 100 |
| Expectancy | 0.001–0.01 R |
| Sharpe after costs | ≥ 0.7 |
| Max drawdown | ≤ 20% |
| Baseline | Sharpe strictly greater than `trend_returns` |
| Calibration | Brier reported; ≤ 0.25 is the paper-soak bar |

A negative result is a valid deliverable. Report it.

---

## 6. Package the artifact

Copy the best checkpoint into the artifact directory, then:

```bash
.venv/bin/python scripts/build_model_artifact.py \
  --dir artifacts/kronos-mini \
  --model-id kronos-mini \
  --version 0.1.0 \
  --model-file model.pt \
  --timeframe 1Day \
  --horizon-bars 10 \
  --universe AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,UNH,JNJ,V,WMT,PG,HD,BAC,DIS,NFLX,AMD,CRM,SPY \
  --train-start 2020-07-27T00:00:00+00:00 \
  --train-end   2025-02-01T00:00:00+00:00 \
  --validation-start 2025-08-07T00:00:00+00:00 \
  --validation-end   2026-11-06T00:00:00+00:00 \
  --metrics artifacts/kronos-mini/report.json
```

`report.json` contains a `reasons` list, which is skipped; only numeric
metrics are written to the manifest.

---

## 7. Wire the adapter

The adapter is already written; you only supply a `Predictor`:

```python
from datetime import datetime
from pathlib import Path

from axel.agents.kronos import Forecast, KronosForecastAnalyst, load_analyst


class KronosPredictor:
    def __init__(self, checkpoint: Path) -> None:
        # load the fine-tuned model here; keep it read-only
        ...

    def predict(self, symbol: str, closes: list[float], as_of: datetime) -> Forecast:
        # run inference, map to expected_return / probability_up
        ...


analyst = load_analyst(Path("artifacts/kronos-mini"), KronosPredictor(Path("model.pt")))
forecast = analyst.forecast("AAPL", closes, as_of)
analyst.emit(session, forecast)   # writes one SignalModel row
```

Boundaries the adapter enforces:

- `analyst = "KRONOS"`
- `direction` in `LONG`, `SHORT`, `NEUTRAL`
- `strength` in `[0, 1]`, derived from `probability_up`
- `horizon` matches the manifest `horizon_bars`
- `features_ref` is a JSON string capped at 128 characters
- no import of `axel.risk`, `axel.execution`, `axel.portfolio`
- no broker credentials

---

## 8. Definition of done

- [ ] `bars.csv` + `manifest.json` with content hash
- [ ] `folds.json` + per-fold CSVs + untouched holdout
- [ ] fine-tuned checkpoint from a pretrained Kronos start
- [ ] `predictions.csv` on the holdout only
- [ ] `report.json` with Brier, MAE, directional accuracy, Sharpe, drawdown
- [ ] baseline comparison after costs
- [ ] `manifest.json` with model hash, data hash, git commit, metrics
- [ ] adapter test passes with a stub predictor
- [ ] `ruff check .` and `pytest -q` pass
- [ ] PR against a feature branch, never `main`

---

## 9. Hard boundaries

1. No broker or live-trading credentials in the training environment.
2. No imports from `axel.risk`, `axel.execution`, `axel.portfolio`.
3. No trading authority: the model only writes `SignalModel` rows.
4. No live mode: `ENVIRONMENT=paper`, `LIVE_TRADING_CONFIRMED=false`.
5. No direct merges to `main`.
6. No training on paper-soak data.
7. A GPU does not shorten the 56-day paper soak. It is wall-clock observation.
