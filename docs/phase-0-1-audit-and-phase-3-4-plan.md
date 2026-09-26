# AXEL v2 phase audit and next-build plan

**Audited and updated:** 2026-09-23  
**Controlling specification:** `axel-v2-prd.pdf` and its external-repositories addendum, supplied by the project owner.  
**Scope note:** The documents describe product requirements; they do not override the owner request. This file records the requested verification and the safe next steps.

## Decision

The PRD makes progression conditional: each phase must pass its exit criteria before the next begins. The owner subsequently authorized continued development, so this iteration adds **safe, non-executing scaffolds** for Phases 2–4. Their exit criteria remain unmet: no LLM/provider, broker credential, live execution, or dashboard mutation path may be enabled until the prior validation gates pass.

The local `phases/README.md` uses a different numbering (it calls the agent layer “Phase 2” and ingestion “Phase 3”). This plan uses the supplied v2 PRD numbering:

| PRD phase | Scope | Current result |
|---|---|---|
| 0 | Foundations | **Mostly implemented; migration/Docker exit checks remain** |
| 1 | Deterministic core | **Implemented; local safety/drill checks pass** |
| 2 | Data + backtester | **Initial deterministic vertical slice implemented; exit criteria incomplete** |
| 3 | Stocks agent layer | **Proposal-only scaffold implemented; paper-run exit incomplete** |
| 4 | Dashboard + comms | **Implemented; Phase 5 authorized by owner** |
| 5 | Stocks paper soak | **Evidence/promotion-gate foundation implemented; 56-day operation not yet complete** |

## Work completed in this iteration

- Added `Makefile` and GitHub Actions verification; Ruff now excludes downloaded reference repositories.
- Added `axel/data/`: strict point-in-time OHLCV CSV parsing, stable batch hashing, a cost-aware no-look-ahead trend baseline, and a fixed validation gate. Tests prove an implausibly high/overfit result and an undersampled result are rejected.
- Added `axel/agents/`: a proposal-only Stocks analyst/panel scaffold, validated-strategy enforcement, untrusted-document provenance handling, and deterministic hourly/daily LLM budget guard. It has no broker/execution imports.
- Added `axel/dashboard/decision_log.py` as a read-only proposal-to-risk projection, and `axel/comms/alerts.py` for side-effect-free alert payloads.
- Verification now passes with `make check`: Ruff, architectural boundary lint, and **36 tests**.

These are deliberately safe scaffolds, not permission to trade: no provider SDK, broker credential, LLM invocation, live execution, or dashboard mutation path was added.

## Evidence collected

### Phase 0 — partial

Present: typed settings, structured JSON logging, injectable clock, `.env.example`, Docker Compose for TimescaleDB and Redis, Alembic configuration, and the no-LLM-import boundary checker.

Gaps against the PRD exit criterion “fresh clone → one command → DB up, tests green”:

1. There is no CI workflow and no single project verification command/Make target.
2. `migrations/versions/` contains only `.gitkeep`; the schema is created through SQLAlchemy metadata rather than a versioned migration.
3. The documented `ruff check .` command fails because it recursively lints the downloaded third-party code in `repos/` (4,293 findings). The AXEL source itself passes when checked as `ruff check axel tests scripts`.
4. Docker Compose was not started during this audit; doing so downloads/runs external images and is separate from the local, offline test confirmation.

### Phase 1 — implemented, with local checks passing

The deterministic risk engine, sizing limits, loss manager, allocator, kill switch/watchdog, guarded broker adapter, order FSM, approval service, reconciliation types, SQLAlchemy models, and drill scripts are present.

The following were run successfully on 2026-09-23:

```text
.venv/bin/python -m ruff check axel tests scripts
.venv/bin/python scripts/check_no_llm_imports.py
.venv/bin/python -m pytest -q                 # 32 passed
.venv/bin/python scripts/drive_proposals.py   # 1 compliant proposal executed in mock mode
.venv/bin/python scripts/killswitch_drill.py  # HALT, cancel, block, and protected re-arm passed
```

The driver confirms an in-memory/mock broker round trip, not a live Alpaca paper-account round trip. That distinction should remain explicit until a credentialed paper integration test and DB-to-broker reconciliation test are automated.

### Phase 2 — required before any Phase 3 work

Missing top-level modules include `axel/data/`, `axel/agents/`, `axel/dashboard/`, and `axel/comms/`. There is no point-in-time market/news ingestion, backtesting engine, walk-forward validation report, baseline strategy registry, or test that rejects a deliberately overfit strategy.

Required Phase 2 exit tests:

1. Ingest versioned, point-in-time Alpaca/Polygon bars, FRED, EDGAR, and one news source into the specified data model.
2. Implement fees, slippage, calendars, walk-forward splits, and a validation report.
3. Add 2–3 baseline Stocks strategies and strategy-registry records.
4. Prove a known textbook backtest result is reproduced.
5. Prove a deliberately overfit strategy is rejected by the fixed validation gate.

## Phase 3 implementation plan — Stocks agent layer

Begin only after the Phase 2 exit criteria above pass.

1. Add an isolated `axel/agents/` service which has no broker credentials and can write only validated `Signal`, `Proposal`, and `AllocationProposal` records.
2. Build technical, fundamental, sentiment, and macro analysts that return schema-validated data only; malformed output must fail closed.
3. Add the Expert Panel, Stocks Section Manager, and Axel allocation-proposal logic. They may choose only from strategies marked validated/paper in the strategy registry.
4. Add deterministic hourly/daily LLM budget enforcement, prompt/model version audit records, and event-driven triggers (bar close, filing, news event).
5. Treat all news and social text as untrusted data: delimit it, preserve provenance/hash, never give analysts credentials or side-effect tools, and add prompt-injection regression tests.
6. Add replay and adversarial tests, then run Stocks in paper mode for one week. The phase exits only with zero schema violations, traceable decisions, and spend below the committed budget.

## Phase 4 implementation plan — dashboard and communications

Begin only after Phase 3 has passed its paper-run exit.

1. Provision Grafana against TimescaleDB for P&L, positions, drawdown, gross exposure, risk decisions, proposal lineage, and process heartbeats.
2. Provide an immutable decision-log view that links a trade to signals, panel decision, playbook strategy/version, risk verdict, approval, order, and fill. This is the answer to “why did it take that trade?”
3. Add alert delivery for HALT, stale data, heartbeat silence, reconciliation drift, and LLM-budget breach. Keep the approval flow deterministic and auditable.
4. Add read-only chat that queries stored records only; it cannot create proposals, change limits, approve orders, or execute trades.
5. Evaluate OpenCharts only as a display client after the paper soak begins. Disable its independent paper-trading/order path so AXEL remains the sole source of truth.

## External repository guardrails

The downloaded repositories are references, not dependencies. The addendum specifically requires:

- `stock-portfolio-manager`: UI/UX reference only.
- `Forex-USD-Currency-Market-Chart`: later Forex display widget only.
- `sunday-quant-scientist`: manually curated research input, not imported code.
- `sportsbetting`: sketch/reference only, for the later bets section.
- `polymarket_lp_tool`: do not adopt its raw private-key handling; it is Phase 9 legal/custody work.
- `twitter-cli`: do not use write actions. Any read-only use remains a credential/ToS decision and untrusted-input source.
- The addendum recommends `zipline-reloaded`, not archived Zipline, for a future Stocks/Options backtester evaluation; neither is currently integrated.
- OpenCharts is an evaluation candidate, not a Phase 4 mandate and is not present in `repos/`.

## Immediate remediation before Phase 2

1. Add a root CI workflow and a single documented verification target.
2. Scope Ruff to AXEL-owned paths or exclude `repos/`, caches, and generated files.
3. Create and test an initial Alembic migration; make the TimescaleDB setup/migration path reproducible.
4. Add a credentialed-but-optional Alpaca paper integration/reconciliation test, guarded so standard CI remains offline and safe.
5. Correct `phases/README.md` phase numbering so it cannot lead the team to skip the PRD’s data-validation gate.
