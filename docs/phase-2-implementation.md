# Phase 2 Implementation — Data, Backtesting & Validation Substrate

Status: **code complete, verified against live provider APIs** (339 tests, ruff clean, 17/17 live checks).
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
| `P2-QA` | `tests/data/` + the repo-wide suite (339 tests), plus `scripts/verify_phase2_pipeline.py` for live read-only provider verification |

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
  (`available_at = filingDate`), so fundamentals cannot leak into earlier
  decisions. The economic event is clamped to `min(reportDate, filingDate)`,
  because a proxy statement reports a meeting it has not held yet.
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
  provenance flags) — a split can never be applied twice. Adjustment status is
  one of `ADJUSTMENT_STATUSES`, and split/dividend adjustments compose rather
  than overwrite each other.
- Cash dividends credit cash; a delisting force-closes at its terminal price
  with a labelled exit reason. Neither path silently drops a symbol.
- Session-clock execution: the delay between decision and fill is counted in
  **trading sessions** from the venue's calendar, not calendar days. `alpaca-iex`
  and `us-equity` resolve to a generated US-equity holiday set; an unlisted venue
  resolves to weekdays-only rather than inventing holidays it cannot justify.
  Inferred by default, disabled with `infer_calendar=False`.
- Metrics: `total_return`, `cagr`, `volatility`, `sharpe`, `sortino`,
  `max_drawdown`, `hit_rate`, `profit_factor`, `expectancy_r`.

## Risk models (`axel/data/ml/risk_model.py`, `axel/risk/tail.py`)

The research pack rejects a universal 1%/2% risk constant, so the risk unit is a
measured quantity:

- `VolatilityRiskModel` — risk = quantity x price x realised volatility, for
  when no stop exists.
- `StopDistanceRiskModel` — risk = quantity x stop distance, matching the live
  `KellySizer` convention.
- `TailRiskEngine` — historical VaR, **Expected Shortfall**, marginal VaR,
  stress scenarios and Jorion-style sizing. Positions are capped by volatility
  and ES together; stress scenarios that name an existing holding net it off
  the cap, and a trade that only *reduces* exposure is never blocked
  (`binding_constraint="RISK_REDUCTION"`), because a gate that can veto an exit
  traps a strategy in its riskiest position.
- Wired in as an optional `Backtester(risk_engine=...)`, so the strategy
  proposes and the risk budget disposes.

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
.venv/bin/python -m pytest -q             # 339 passed
.venv/bin/python scripts/check_no_llm_imports.py   # [PASS]
.venv/bin/python scripts/check_agent_config.py      # [PASS]
```

Recorded payloads prove the parsers are correct but not that today's upstream
APIs still return what we assume. `scripts/verify_phase2_pipeline.py` closes
that gap with one read-only fetch per source, pushed through the real ingestion
pipeline:

```bash
.venv/bin/python scripts/verify_phase2_pipeline.py              # all sources
.venv/bin/python scripts/verify_phase2_pipeline.py --source fred
```

It asserts, on live data: bars are ordered and only readable after their
session closes, every record satisfies `available_at >= event_time`, the venue
resolves to a session calendar, the risk denominator is a real number, and
re-ingesting a payload is a no-op. Last run: **17/17 passed** against Alpaca
IEX, FRED and SEC EDGAR (SPY / DGS10 / CIK 0000320193).

## Real defects found by verification

Three were caught by tests and one only by the live run, which is the argument
for having one:

1. Final liquidation credited cash with the wrong sign (closing a long reduced cash).
2. Partial position closes decremented the residual order in the wrong
   direction, turning a full close into a phantom reversed position.
3. `reportDate` on a proxy statement (`DEF 14A`) is the *meeting* date, which
   EDGAR files against **weeks before the meeting**. Using it verbatim as the
   economic event placed `event_time` after the filing's own availability and
   every live fetch aborted. Every recorded payload used a 10-Q, where
   `reportDate` precedes `filingDate`, so no fixture could have caught it. Now
   clamped to `min(reportDate, filingDate)`; the true `reportDate` is preserved
   in `fields`.

## Known gaps (next work)

- Canonical records persist to filesystem JSONL by default;
  `axel/data/ingest/db_store.py` adds a `TimescaleStore` that enforces
  idempotency and `available_at >= event_time` in the database, but it is not
  yet the default in deployment.
- Walk-forward folds report per-fold metrics but do not yet auto-aggregate
  pooled OOS statistics.
- No factor has been promoted past `RESEARCH_ONLY`; promotion needs
  out-of-sample evidence that does not exist yet.
- Live verification covers ingestion invariants, not strategy economics: a
  validated strategy still requires pooled OOS statistics on real data.
- No minute/intraday calendar or multi-venue session model.