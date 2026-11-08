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
  evaluate_forecasts.py     # predictions.csv -> report.json + gate
  build_model_artifact.py   # checkpoint -> manifest.json
  train_kronos.py           # validate folds, emit/run Kronos commands
axel/training/              # dataset, evaluation, artifact contracts
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
.venv/bin/pip install pyqlib          # required by the Kronos fine-tune path
```

Clone Kronos next to AXEL, not inside it:

```bash
git clone https://github.com/shiyu-coder/Kronos.git ../Kronos
```

Verify the base system before training:

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
```

---

## 2. Build the dataset

Preferred: read-only Alpaca market data.

```bash
.venv/bin/python scripts/fetch_historical_bars.py \
  --symbols AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,UNH \
  --start 2023-01-01T00:00:00+00:00 \
  --end   2025-01-01T00:00:00+00:00 \
  --timeframe 1Hour \
  --out data/bars.csv
```

If the bars are already in the AXEL database:

```bash
.venv/bin/python scripts/export_training_dataset.py \
  --out data/dataset --timeframe 1Hour
```

Both paths write the same schema and a content hash:

```csv
symbol,timestamp,open,high,low,close,volume,timeframe
AAPL,2024-01-02T14:30:00+00:00,185.64,186.10,184.90,185.90,51230000,1Hour
```

Rules: timezone-aware timestamps, one row per `(symbol, timestamp, timeframe)`,
sorted, no look-ahead. Do **not** train on paper-soak data.

---

## 3. Prepare folds

Splits are made on the shared timestamp axis, never per symbol and never
randomly.

```bash
.venv/bin/python scripts/prepare_training_folds.py \
  --bars data/bars.csv \
  --out  data/folds \
  --train-size 2000 \
  --test-size 500 \
  --holdout-fraction 0.2
```

Output:

```text
data/folds/
  fold_00/train.csv, test.csv
  fold_01/train.csv, test.csv
  ...
  holdout/holdout_train.csv, holdout_test.csv
  folds.json
```

The holdout is written once and must never be used for tuning. If you touch it
before the final report, the result is invalid.

---

## 4. Fine-tune Kronos

`scripts/train_kronos.py` validates the fold manifest and writes the exact
Kronos commands. It does not import `torch`, so it is safe to run anywhere.

```bash
.venv/bin/python scripts/train_kronos.py \
  --folds data/folds/folds.json \
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

Before running them, edit `../Kronos/finetune/config.py`:

| Setting | Value |
|---|---|
| `qlib_data_path` | your local Qlib data directory |
| `dataset_path` | `data/kronos_dataset` |
| `save_path` | `artifacts/kronos-mini/runs` |
| `pretrained_tokenizer_path` | pretrained Kronos tokenizer |
| `pretrained_predictor_path` | pretrained Kronos predictor |
| `instrument` | your universe |
| `train_time_range` | the train windows from `folds.json` |
| `epochs`, `batch_size` | start small; mini converges fast |
| `use_comet` | `False` unless you use Comet |

Then either run `./artifacts/kronos-mini/train.sh` or re-run the planner with
`--run`.

Model sizes: `mini` 4.1M (4–8 GB), `small` 24.7M (8–16 GB), `base` 102.3M
(16–24 GB). Start with `mini`; only scale up if the evaluation gate passes.

---

## 5. Evaluate before you ship

Produce `predictions.csv` on the **holdout only**:

```csv
symbol,as_of,expected_return,probability_up,realized_return
AAPL,2024-11-01T14:30:00+00:00,0.0041,0.62,0.0037
```

Then:

```bash
.venv/bin/python scripts/evaluate_forecasts.py \
  --predictions artifacts/kronos-mini/predictions.csv \
  --bars data/folds/holdout/holdout_test.csv \
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
  --timeframe 1Hour \
  --horizon-bars 5 \
  --universe AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,UNH \
  --train-start 2023-01-01T00:00:00+00:00 \
  --train-end   2024-06-30T00:00:00+00:00 \
  --validation-start 2024-07-01T00:00:00+00:00 \
  --validation-end   2025-01-01T00:00:00+00:00 \
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
