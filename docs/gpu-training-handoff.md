# GPU training handoff brief

**Status date:** 2026-11-08  
**Audience:** the person receiving AXEL to train or accelerate on a GPU  
**Repo:** `ASHIR-TECH/AXEL`, branch to be created for this work

This is the brief to hand over. Read §1 before starting: AXEL does not currently
contain a training pipeline, so the scope has to be chosen explicitly.

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

There is **no model training code in AXEL today**. Nothing in the repository
imports `torch`, `sklearn`, `lightgbm` or `xgboost`. What exists is:

| Path | What it actually is |
|---|---|
| `axel/data/backtester.py` | Deterministic, cost-aware trend baseline. CPU only. |
| `axel/data/validation.py` | Admission thresholds for a backtest result. |
| `axel/data/ml/` | Costs, slippage, calendars, corporate actions, splits, risk model, execution model. **Not** training. |
| `axel/validation/` | Walk-forward folds, PBO, deflated Sharpe, multiple-testing, robustness. |
| `axel/research/` | Dataset registry, experiment tracking, governance, model registry, drift. Metadata only. |
| `axel/data/features/` | Deterministic feature functions (price, momentum, volatility, macro, sentiment, microstructure). |
| `axel/agents/` | Proposal-only analyst layer and an OpenAI-compatible gateway. |
| `axel/runtime/` | Deterministic paper runtime. No learning. |

So "train AXEL on a GPU" is not a task that exists yet. Pick one scope from §2
and deliver the artifact described in §4.

---

## 2. Pick exactly one scope

### Scope A — Kronos fine-tune + offline inference adapter (recommended)

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
AAPL,2024-01-02T14:30:00+00:00,185.64,186.10,184.90,185.90,51230000,1h
```

Rules:

- `timestamp` must be timezone-aware (UTC).
- Prices positive, `low <= high`, `volume >= 0`.
- One row per `(symbol, timestamp, timeframe)`.
- Sorted by `(symbol, timestamp)`.
- **No look-ahead.** A feature at decision time `t` may only use data with
  `available_at <= t`.

### How to source it

Option 1, via AXEL's read-only Alpaca market-data client (needs a free paper
account's market-data keys):

```python
from datetime import UTC, datetime
from axel.data.providers.alpaca import AlpacaMarketData

client = AlpacaMarketData(api_key="...", secret_key="...")
result = client.fetch_bars(
    "AAPL",
    datetime(2023, 1, 1, tzinfo=UTC),
    datetime(2025, 1, 1, tzinfo=UTC),
    timeframe="1Hour",
)
bars = result.records
```

Option 2, provide a CSV export from any vendor that matches the schema above.

### Split policy

Use the existing code, do not invent a split:

```python
from axel.validation.walk_forward import walk_forward_folds
folds = walk_forward_folds(n_periods, train_size=..., test_size=..., step=...)
```

Report metrics only on folds the model never saw. If the model was tuned on the
test window, the result is invalid.

---

## 4. Deliverable contract

### 4.1 Artifact

```text
artifacts/<model_id>/<version>/
  model.pt              # or model.safetensors
  manifest.json
  README.md             # exact reproduce command
```

`manifest.json`:

```json
{
  "model_id": "kronos-mini",
  "version": "0.1.0",
  "task": "forecast",
  "timeframe": "1h",
  "horizon_bars": 5,
  "universe": ["AAPL", "MSFT", "NVDA"],
  "train_window": {"start": "2023-01-01T00:00:00Z", "end": "2024-06-30T00:00:00Z"},
  "validation_window": {"start": "2024-07-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
  "features": ["open", "high", "low", "close", "volume"],
  "seed": 7,
  "content_hash": "sha256:...",
  "code_commit": "<git sha of the training code>",
  "metrics": {
    "brier": 0.0,
    "mae_return": 0.0,
    "sharpe_after_costs": 0.0,
    "max_drawdown": 0.0
  }
}
```

### 4.2 Inference adapter

Lives in `axel/agents/`. It must:

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
   - `pip install torch` (or the Kronos repo's own requirements)
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

# Verify the baseline before touching anything
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .

# Baseline backtest (the number to beat)
.venv/bin/python - <<'PY'
from axel.data.backtester import trend_returns
from axel.data.ingest.ohlcv import parse_ohlcv_csv
bars = parse_ohlcv_csv(open("data/AAPL.csv").read(), source="alpaca")
print(trend_returns(bars))
PY
```

---

## 11. The one-paragraph version to send

> AXEL has no training code yet, so please work on branch
> `research/kronos-forecast`. Scope: fine-tune Kronos on the attached bars
> dataset (10 symbols, 1-hour, 2023-01 to 2025-01) and deliver a checkpoint, a
> `manifest.json`, and a read-only adapter in `axel/agents/` that emits
> `SignalModel` rows. Report metrics only on the held-out window, compare
> against `axel/data/backtester.py` after costs, and report PBO and deflated
> Sharpe. Do not touch `axel/risk`, `axel/execution`, `axel/portfolio`, and do
> not add broker keys. Open a PR when `ruff check .` and the test suite pass.
