# Phase 2 Implementation — Data, Backtesting & Validation Substrate

Status: **code complete, end-to-end verified** (221 tests, ruff clean).
Spec: `docs/AXEL_Phase_2_PRD.pdf`.

## Scope delivered

Phase 2 is a *research/validation* substrate. It ingests provider data,
normalizes it into versioned records, computes deterministic features,
simulates execution honestly, and validates strategies against explicit gates.
It does not place live orders and cannot grant live authority.

## PRD work package → code

| Work package | Modules |
| --- | --- |
| `P2-DATA` | `axel/data/providers/` (`base`, `alpaca`, `fred`, `sec`, `news`), `axel/data/ingest/` (`raw_store`, `store`, `pipeline`, `ohlcv`), `axel/data/normalization/`, `axel/data/schemas.py` |
| `P2-PIT` | `axel/data/point_in_time/` (`joins.py`): `available_at`, `filter_available`, `as_of`, `join_as_of`, `assert_no_lookahead`, `PointInTimeIndex` |
| `P2-FEAT` | `axel/data/features/` (`price`, `volume`, `momentum`, `volatility`, `fundamentals`, `macro`, `sentiment`, `cross_sectional`, `base`) |
| `P2-BT` | `axel/data/ml/` (`backtester`, `costs`, `slippage`, `calendars`, `splits`, `metrics`) |
| `P2-VAL` | `axel/validation/` (`walk_forward`, `robustness`, `multiple_testing`, `deflated_sharpe`, `admission`) |
| `P2-STRAT` | `axel/strategies/` (`metadata`, `registry`, `baseline/trend`, `baseline/mean_reversion`, `validated/`) |
| `P2-REP` | `axel/validation/report.py` — reproducible, fingerprinted validation report |
| `P2-QA` | `tests/data/` (56 tests) + the repo-wide suite (221 tests) |

## Data contract (`axel/data/schemas.py`)

Every canonical record is frozen and carries:

- `provenance` — source, source id, content hash, ingestion timestamp,
  `schema_version` (`canonical-1`), `normalization_version` (`norm-1`), quality flags.
- `event_time` vs `available_at` — construction enforces
  `available_at >= event_time`, so a record can never be marked knowable before
  it happened.
- `record_type` + `key()` — the logical key used for deduplication and as-of joins.

Record types: `BarRecord`, `MacroObservation`, `FilingRecord`, `NewsRecord`.

## Point-in-time guarantees

- `available_at = max(source_available_at, dependency availability)`
  (`feature_available_at`).
- Bars become usable only once the bar has **closed**
  (`available_at = event_time + timeframe_duration`).
- FRED observations are **vintage aware**: `available_at` is the realtime
  vintage, never the observation date.
- Filings are usable only from their **public filing time**
  (`available_at = filingDate`), so fundamentals cannot leak into earlier decisions.
- `assert_no_lookahead` / `LookaheadError` turn leakage into a hard failure.
- The backtester decides using only bars with `available_at <= t` and executes
  at the **next bar's open**, never at `t`.

## Idempotency & replay

- `RawStore` writes each provider payload **once**, content-addressed by body
  hash, and never rewrites it.
- `CanonicalStore.put` deduplicates by logical key.
- `IngestionPipeline.ingest` is restartable: re-running an interval reports
  `stored=0, duplicates=N` instead of duplicating data.
- `IngestionPipeline.replay` re-derives canonical records from a stored raw
  payload, so normalization changes are auditable.

## Determinism & reproducibility

- Features are pure functions of normalized records plus explicit params; each
  `FeatureRecord` carries a 64-char `fingerprint()` of name/version/params.
- `ValidationReport` is a pure function of its inputs and carries **no
  timestamp**, so two identical runs produce an identical `fingerprint`
  (`tests/data/test_validation_report.py` asserts this).
- No LLM in the research path. News sentiment is a fixed keyword lexicon
  (`NEWS_LEXICON_VERSION`), never a model call.

## Honest execution simulation

- Costs and slippage are always applied: `CostModel` (bps + floor),
  `FixedBpsSlippage`, `VolumeImpactSlippage`.
- Round-trip `Trade` objects report gross pnl, costs, net pnl and
  `r_multiple`, so expectancy is expressed in R, not in raw return.
- Splits are adjusted idempotently (`apply_splits` records each applied split in
  provenance flags) — a split can never be applied twice.
- Metrics: `total_return`, `cagr`, `volatility`, `sharpe`, `sortino`,
  `max_drawdown`, `hit_rate`, `profit_factor`, `expectancy_r`.

## Admission gates (`axel/validation/admission.py`)

A strategy is admitted only when **all** of these hold:

| Gate | Default |
| --- | --- |
| Trade count | `>= 100` |
| Net expectancy | `0.1R – 0.5R` |
| Net OOS Sharpe | `> 0.7` |
| Max drawdown | `>= -0.20` |
| Regimes covered | `>= 2` |
| Deflated Sharpe | `>= 0.95` |

- Expectancy above `1.0R` raises `needs_leakage_audit` → **audit, never promote**.
- Selection bias is priced in via `expected_max_sharpe` and
  `deflated_sharpe_ratio`, plus `benjamini_hochberg` / `bonferroni_threshold`.
- Robustness gate: ±20% parameter perturbation must not flip expectancy sign
  (`robustness_report(...).stable`).

## Strategy lifecycle

`DRAFT → BACKTESTED → VALIDATED → PAPER_ELIGIBLE → ACTIVE`, with
`REJECTED` / `RETIRED`. `StrategyRegistry.transition` rejects illegal jumps
(e.g. `DRAFT → PAPER_ELIGIBLE`), and `can_place_live_orders()` is hard-coded
`False`: **Phase 2 cannot grant live execution authority.**
`axel/strategies/validated/` is intentionally empty — nothing skips the gates.

## Verification

```bash
.venv/bin/python -m ruff check .          # All checks passed
.venv/bin/python -m pytest -q             # 221 passed
.venv/bin/python scripts/check_no_llm_imports.py   # [PASS]
.venv/bin/python scripts/check_agent_config.py      # [PASS]
```

Two real defects were caught by these tests during P2-C and fixed:

1. Final liquidation credited cash with the wrong sign (closing a long reduced cash).
2. Partial position closes decremented the residual order in the wrong
   direction, turning a full close into a phantom reversed position.

## Known gaps (next work)

- Canonical records are persisted to filesystem JSONL; **not yet wired to
  TimescaleDB** (`ohlcv_bars` / `equity_snapshots`). The `CanonicalStore` seam
  is the intended replacement point.
- Corporate actions beyond splits (dividends, delistings) are not modeled.
- Validation has not yet been run against **real** provider data; all
  end-to-end tests use deterministic synthetic bars.
- Walk-forward folds report per-fold metrics but do not yet auto-aggregate
  pooled OOS statistics.
- No minute/intraday calendar or multi-venue session model.