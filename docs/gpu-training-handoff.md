# GPU training handoff brief

**Status date:** 2026-11-08  
**Audience:** the person receiving AXEL to train or accelerate on a GPU  
**Repo:** `ASHIR-TECH/AXEL`, branch to be created for this work

This is the brief to hand over. Read §1 before starting: the pipeline around the
model is implemented, and the scope is fixed as **Scope A**.

---

## 1. Read this first

### 1.1 What the GPU is for

The GPU is used for **Scope A: fine-tuning Kronos**. That is real training, and
it is the reason this machine is involved.

| Work | GPU | Notes |
|---|---|---|
| Fine-tune Kronos (Scope A) | **Required** | Decoder-only transformer over K-line tokens |
| GPU-accelerated backtesting (Scope C) | Optional | Vectorized over years of bars |
| The 56-day paper soak | **No** | Wall-clock observation of a real broker |
| The deterministic runtime | **No** | CPU-only by design, and must stay that way |
| The dashboard | **No** | Read-only SQL projections |

What is being trained is a **forecaster**, not the trading system. Kronos learns
to predict future bars. AXEL's risk engine, sizing, approval and execution stay
deterministic and never learn. The model's output enters the system only as a
`Signal` row.

Rough sizing (from the Kronos model family):

| Variant | Parameters | GPU memory | Expected fine-tune time |
|---|---:|---:|---|
| Kronos-mini | 4.1M | 4–8 GB | under an hour |
| Kronos-small | 24.7M | 8–16 GB | a few hours |
| Kronos-base | 102.3M | 16–24 GB | several hours to a day |

Context length is 512 bars for small/base. The Kronos repository documents a
Qlib-based fine-tuning path; follow its instructions rather than inventing a
training loop.

Fine-tuning is an experiment, not a guaranteed edge. The pretrained checkpoint
may already be good enough, and the fine-tuned model may fail the validation
gate in §5. Report either outcome honestly.

### 1.2 What already exists

The **data, fold, evaluation, artifact and adapter plumbing is implemented**.
What is still missing is the GPU run itself: no checkpoint has been trained, and
the repository still does not import `torch` at runtime. The implemented pieces
are:

| Path | What it actually is |
|---|---|
| `axel/data/export.py` | DB → `bars.csv` + `manifest.json` with a content hash. |
| `axel/data/historical.py` | Provider-agnostic historical fetch (read-only). |
| `axel/training/dataset.py` | Timestamp-axis walk-forward folds and an untouched holdout. |
| `axel/training/evaluation.py` | Brier, MAE, directional accuracy, cost-adjusted Sharpe, admission gate, trend baseline. |
| `axel/training/artifact.py` | `ModelArtifact` + `manifest.json` contract with model hash. |
| `axel/training/qlib.py` | Qlib CSV/binary export for the Kronos fine-tune. |
| `axel/agents/kronos.py` | Proposal-only `KronosForecastAnalyst` emitting `SignalModel` rows. |
| `scripts/fetch_historical_bars.py` | Alpaca market data → CSV. |
| `scripts/export_training_dataset.py` | AXEL DB → CSV + manifest. |
| `scripts/prepare_training_folds.py` | CSV → folds + holdout + `folds.json`. |
| `scripts/export_qlib_dataset.py` | CSV → Qlib CSV + binary data for Kronos. |
| `scripts/evaluate_forecasts.py` | `predictions.csv` → `report.json` + pass/fail. |
| `scripts/build_model_artifact.py` | Checkpoint → `manifest.json`. |
| `scripts/train_kronos.py` | Validates folds and emits/runs the Kronos commands. |
| `training/kronos/README.md` | The exact runbook. |
| `axel/data/backtester.py` | Deterministic, cost-aware trend baseline. CPU only. |
| `axel/data/validation.py` | Admission thresholds for a backtest result. |
| `axel/data/ml/` | Costs, slippage, calendars, corporate actions, splits, risk model, execution model. **Not** training. |
| `axel/validation/` | Walk-forward folds, PBO, deflated Sharpe, multiple-testing, robustness. |
| `axel/research/` | Dataset registry, experiment tracking, governance, model registry, drift. Metadata only. |
| `axel/data/features/` | Deterministic feature functions (price, momentum, volatility, macro, sentiment, microstructure). |
| `axel/agents/` | Proposal-only analyst layer and an OpenAI-compatible gateway. |
| `axel/runtime/` | Deterministic paper runtime. No learning. |

The contract is one directory: `artifacts/<model_id>/<version>/` containing the
checkpoint and `manifest.json`. See `training/kronos/README.md` for the exact
sequence.

---

## 2. Scope (fixed: A)

### Scope A — Kronos fine-tune + offline inference adapter (chosen)

- Fine-tune Kronos (`https://github.com/shiyu-coder/Kronos`) on AXEL's historical
  bars.
- Deliver a checkpoint plus a thin adapter that turns forecasts into AXEL
  `SignalModel` rows.
- This is the highest-value option if the goal is a learned price forecaster.

### Scope B — Supervised model on AXEL features

- Train a model over the existing deterministic features.
- Requires defining labels and a dataset builder first; more open-ended than A.
- Only take this if the target is explicitly specified.

### Scope C — GPU-accelerated backtesting

- No training. Vectorize the existing backtest contract over years of bars.
- Lowest risk, immediate value, no model artifact.
- Good as a parallel deliverable, not a substitute for A or B.

**Do not mix scopes.** A handoff that tries to do A and B and C at once produces
three unfinished things.

---

## 3. Dataset

AXEL does not ship a training dataset, and the current database contains
synthetic paper data only. The dataset must be sourced explicitly.

### Required schema (OHLCV)

Matches `axel/data/ingest/ohlcv.py`:

```csv
symbol,timestamp,open,high,low,close,volume,timeframe
AAPL,2026-11-06T04:00:00+00:00,269.10,270.40,267.85,269.90,41230000,1Day
```

Rules:

- `timestamp` must be timezone-aware (UTC).
- Prices positive, `low <= high`, `volume >= 0`.
- One row per `(symbol, timestamp, timeframe)`.
- Sorted by `(symbol, timestamp)`.
- **Split-adjusted prices.** The fetch script defaults to `--adjustment split`;
  raw prices across 2020–2026 contain 10:1 and 20:1 split jumps.
- **No look-ahead.** A feature at decision time `t` may only use data with
  `available_at <= t`.

### How to source it

Option 1, via AXEL's read-only Alpaca market-data client. The wrapper script
does the fetch and writes the canonical CSV. The dataset for this handoff is
already at `data/kronos/bars.csv` (21 symbols, 33,147 daily bars, 2020-07-27 →
2026-11-06):

```bash
.venv/bin/python scripts/fetch_historical_bars.py \
  --symbols AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,UNH,JNJ,V,WMT,PG,HD,BAC,DIS,NFLX,AMD,CRM,SPY \
  --start 2020-01-01T00:00:00+00:00 \
  --end   2026-11-09T00:00:00+00:00 \
  --timeframe 1Day \
  --out data/kronos/bars.csv
```

Option 2, export what is already in the AXEL database:

```bash
.venv/bin/python scripts/export_training_dataset.py \
  --out data/kronos/dataset --timeframe 1Day
```

Option 3, provide a CSV export from any vendor that matches the schema above.
Alpaca's free IEX feed starts at 2020-07-27 for daily bars and 2023 for
intraday bars; use daily, which is what Kronos's Qlib path expects.

### Split policy

Use the existing code, do not invent a split:

```bash
.venv/bin/python scripts/prepare_training_folds.py \
  --bars data/kronos/bars.csv \
  --out  data/kronos/folds \
  --train-size 750 \
  --test-size 150 \
  --holdout-fraction 0.2
```

Under the hood this calls
`axel.validation.walk_forward.walk_forward_folds` on the shared timestamp axis,
then writes `fold_NN/train.csv`, `fold_NN/test.csv`, an untouched holdout and
`folds.json`. The holdout is 6,636 rows from 2025-08-07 to 2026-11-06.

Report metrics only on folds the model never saw. If the model was tuned on the
test window, the result is invalid.

### Qlib conversion

Kronos fine-tunes through Qlib, so convert the CSV before training:

```bash
.venv/bin/python scripts/export_qlib_dataset.py \
  --bars data/kronos/bars.csv \
  --out data/kronos/qlib_csv \
  --qlib-dir data/kronos/qlib \
  --qlib-script ../qlib/scripts/dump_bin.py \
  --freq day
```

This writes `calendars/day.txt`, `instruments/all.txt` and
`features/<symbol>/*.day.bin`. `instruments/all.txt` is what `instrument =
"all"` refers to in the Kronos config. The `vwap` field is the OHLC typical
price, because Alpaca IEX bars do not carry a VWAP.

---

## 4. Deliverable contract

### 4.1 Artifact

```text
artifacts/<model_id>/<version>/
  model.pt              # or model.safetensors
  manifest.json
  README.md             # exact reproduce command
```

`manifest.json` (written by `scripts/build_model_artifact.py`):

```json
{
  "schema_version": "axel-model-artifact-1",
  "model_id": "kronos-mini",
  "version": "0.1.0",
  "task": "forecast",
  "section": "STOCKS",
  "timeframe": "1Day",
  "horizon_bars": 10,
  "universe": ["AAPL", "MSFT", "NVDA"],
  "train_window": ["2020-07-27T00:00:00+00:00", "2025-02-01T00:00:00+00:00"],
  "validation_window": ["2025-08-07T00:00:00+00:00", "2026-11-06T00:00:00+00:00"],
  "seed": 7,
  "content_hash": "<sha256 of the model file>",
  "code_commit": "<git sha of the training code>",
  "artifact_file": "model.pt",
  "created_at": "2026-11-08T00:00:00+00:00",
  "metrics": {
    "brier": 0.0,
    "mae": 0.0,
    "sharpe": 0.0,
    "max_drawdown": 0.0
  }
}
```

`axel/training/artifact.py` is the single source of truth for this schema; do not
hand-write a variant.

### 4.2 Inference adapter

Implemented in `axel/agents/kronos.py`. You only supply a `Predictor`. It:

- Load the artifact read-only.
- Emit `SignalModel` rows with:
  - `analyst = "KRONOS"` (or the chosen model id)
  - `direction` in `LONG`, `SHORT`, `NEUTRAL`
  - `strength` in `[0.0, 1.0]`
  - `horizon` matching `horizon_bars`
  - `features_ref` = JSON string with `model_id`, `version`, quantiles
- Have a deterministic mode (fixed seed) so paper runs are reproducible.
- Have unit tests with a tiny fixture checkpoint or a stubbed predictor.

### 4.3 Model registration

Register the artifact through `axel/research/model_registry.py` at
`DeploymentState.DEVELOPMENT`. Promotion beyond `VALIDATED` is not the GPU
person's decision.

---

## 5. Validation gate

A model is not accepted because it trains. It is accepted when it passes the
existing gates:

| Gate | Location | Requirement |
|---|---|---|
| Walk-forward | `axel/validation/walk_forward.py` | Out-of-sample folds only |
| Admission thresholds | `axel/data/validation.py` | ≥100 trades, expectancy 0.001–0.01R, Sharpe ≥0.7, max DD ≤20% |
| Overfitting | `axel/validation/pbo.py` | Probability of backtest overfitting reported |
| Multiple testing | `axel/validation/deflated_sharpe.py`, `multiple_testing.py` | Deflated Sharpe reported |
| Baseline | `axel/data/backtester.py` `trend_returns` | Must beat the deterministic trend baseline **after costs** |
| Calibration | `axel/operations/paper_soak.py` `brier_score` | Brier ≤0.25 for probabilistic output |

If the model cannot beat the deterministic baseline, say so. A negative result
reported honestly is more useful than an overfit artifact.

---

## 6. Hard boundaries

The GPU work must not cross these lines:

1. **No broker credentials.** Never put `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` or
   Telegram tokens in the training environment.
2. **No execution imports.** Do not import `axel.risk`, `axel.execution` or
   `axel.portfolio` from the model or adapter. `scripts/check_no_llm_imports.py`
   must keep passing.
3. **No trading authority.** The model proposes `Signal` rows. Only the
   deterministic core can size, approve or execute.
4. **No live mode.** `ENVIRONMENT=paper`, `LIVE_TRADING_CONFIRMED=false`.
5. **No direct merges to `main`.** Work on a branch; open a PR.
6. **No training on soak data.** Data collected during the paper soak cannot be
   used to fit the model that is being evaluated by that soak.

---

## 7. Definition of done

- [ ] Reproducible training command documented in the artifact README.
- [ ] Checkpoint + `manifest.json` with content hash and git commit.
- [ ] Adapter in `axel/agents/` emitting `SignalModel` rows.
- [ ] Unit tests for the adapter (fixture checkpoint, deterministic output).
- [ ] Metrics on a held-out window the model never saw.
- [ ] Baseline comparison after costs.
- [ ] PBO / deflated Sharpe reported.
- [ ] `ruff check .` and the full test suite pass.
- [ ] PR opened against a feature branch, not `main`.

---

## 8. What the AXEL owner must do before handoff

Complete these today; the GPU work is blocked without them.

1. **Choose the scope** from §2. Recommended: Scope A.
2. **Choose the universe, timeframe and horizon.** Example: 10 US large caps,
   1-hour bars, 5-bar horizon. Write it down; do not leave it to the trainer.
3. **Provide data access.** Either Alpaca market-data keys for a paper account,
   or a CSV export matching §3. Confirm the date range and that it covers at
   least two market regimes.
4. **Create the branch** and grant repo access:
   ```bash
   git checkout -b research/kronos-forecast
   git push -u origin research/kronos-forecast
   ```
5. **Set up the environment** they will use:
   - Python 3.11+
   - CUDA-compatible GPU and driver
   - `pip install -e '.[dev]'`
   - `pip install -e '.[ml]'` (torch, pandas, numpy, pyarrow)
   - `pip install pyqlib` (Kronos fine-tune path)
6. **Hand them this document.** Do not add verbal scope beyond it.
7. **Agree the acceptance test** in writing before they start: which fold, which
   metric, which threshold.
8. **Tell them explicitly what not to touch:** `axel/risk`, `axel/execution`,
   `axel/portfolio`, `docker-compose.yml`, `.env`.

### Do not hand over

- Broker or live-trading credentials.
- The production `.env`.
- Write access to `main`.
- Any expectation that this shortens the 56-day paper soak. It does not.

---

## 9. What is left in AXEL (not the GPU person's job)

Tracked in `docs/next-steps.md`:

1. Initial Alembic revision and `alembic upgrade head` in the `migrate` service.
2. Retention timer for `prune_operational_data.py`.
3. Real market-data ingest adapter (Alpaca bars + FRED) with provenance.
4. Real Alpaca paper execution in `core-worker`, feature-flagged.
5. Credentialed paper round-trip test (skipped without keys).
6. Start the 56-day soak at 60-second cadence.
7. Hypertables, compression and retention policies.
8. Parquet export for analytics.
9. Manual browser check that the deployed header shows `LIVE`.

None of these require a GPU. They are the critical path to the soak; the model
work runs in parallel.

---

## 10. Commands the GPU person needs

```bash
# Clone and set up
git clone <repo-url> axel && cd axel
git checkout research/kronos-forecast
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pip install -e '.[ml]'
.venv/bin/pip install pyqlib

# Clone Kronos and Qlib next to AXEL
git clone https://github.com/shiyu-coder/Kronos.git ../Kronos
git clone https://github.com/microsoft/qlib.git ../qlib

# Verify the baseline before touching anything
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .

# 1. Dataset (already generated at data/kronos/bars.csv)
.venv/bin/python scripts/fetch_historical_bars.py \
  --symbols AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,UNH,JNJ,V,WMT,PG,HD,BAC,DIS,NFLX,AMD,CRM,SPY \
  --start 2020-01-01T00:00:00+00:00 --end 2026-11-09T00:00:00+00:00 \
  --timeframe 1Day --out data/kronos/bars.csv

# 2. Folds + untouched holdout
.venv/bin/python scripts/prepare_training_folds.py \
  --bars data/kronos/bars.csv --out data/kronos/folds \
  --train-size 750 --test-size 150 --holdout-fraction 0.2

# 3. Convert to Qlib binary data
.venv/bin/python scripts/export_qlib_dataset.py \
  --bars data/kronos/bars.csv --out data/kronos/qlib_csv \
  --qlib-dir data/kronos/qlib --qlib-script ../qlib/scripts/dump_bin.py --freq day

# 4. Plan the Kronos run (validates folds, writes train.sh)
.venv/bin/python scripts/train_kronos.py \
  --folds data/kronos/folds/folds.json --kronos-repo ../Kronos \
  --out artifacts/kronos-mini --variant mini --gpus 1 --device cuda:0

# 5. Evaluate on the holdout only
.venv/bin/python scripts/evaluate_forecasts.py \
  --predictions artifacts/kronos-mini/predictions.csv \
  --bars data/kronos/folds/holdout/holdout_test.csv \
  --out artifacts/kronos-mini/report.json

# 6. Package the artifact
.venv/bin/python scripts/build_model_artifact.py \
  --dir artifacts/kronos-mini --model-id kronos-mini --version 0.1.0 \
  --model-file model.pt --timeframe 1Day --horizon-bars 10 \
  --universe AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM,XOM,UNH,JNJ,V,WMT,PG,HD,BAC,DIS,NFLX,AMD,CRM,SPY \
  --train-start 2020-07-27T00:00:00+00:00 --train-end 2025-02-01T00:00:00+00:00 \
  --validation-start 2025-08-07T00:00:00+00:00 \
  --validation-end 2026-11-06T00:00:00+00:00 \
  --metrics artifacts/kronos-mini/report.json
```

The full runbook, including `finetune/config.py` settings, is
`training/kronos/README.md`.

---

## 11. The one-paragraph version to send

> AXEL already has the dataset, fold, evaluation, artifact and adapter plumbing.
> Please work on branch `research/kronos-forecast` and run
> `training/kronos/README.md` end to end: fetch the bars, prepare the folds,
> fine-tune Kronos from the pretrained checkpoint, evaluate on the untouched
> holdout, and package the checkpoint with `scripts/build_model_artifact.py`.
> Report metrics only on the held-out window, compare against
> `axel/data/backtester.py` after costs, and report PBO and deflated Sharpe. Do
> not touch `axel/risk`, `axel/execution`, `axel/portfolio`, and do not add
> broker keys. Open a PR when `ruff check .` and the test suite pass.
