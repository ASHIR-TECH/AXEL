# What's next

**Status date:** 2026-11-08  
**Branch:** `improve/reference-repo-integration`  
**Verification:** `ruff check .` clean · **1307 tests passing**

This document is the working plan after the live-dashboard and paper-runtime
work. It records what is actually ready, what still blocks a real paper soak,
and what was changed in this pass.

---

## 0. TL;DR

| Question | Answer |
|---|---|
| Can the deterministic core run today? | Yes. `drive_proposals.py` and `killswitch_drill.py` pass. |
| Do API keys alone start trading? | **No.** The runtime is synthetic and paper-only; nothing calls Alpaca. |
| Is the dashboard live? | Yes, read-only API + SSE, backed by durable rows. |
| Can the 56-day soak start now? | **Not safely.** Real data wiring, a credentialed paper test, and DB/index work come first. |
| Is the database soak-ready? | Improved in this pass, but still missing Alembic and hypertables. |
| Kronos? | Later, proposal-only, in the agent layer. |
| BrowserSkill? | Read-only operator automation only. Never broker login or order entry. |

---

## 1. Where the system actually is

Implemented and tested:

- Deterministic risk engine, sizing, loss manager, allocator, kill switch,
  broker adapter, order FSM, approval service, reconciliation.
- Read-only dashboard API (`/api/v1/healthz`, `/snapshot`, `/stream`) and the
  live frontend adapter with a `SIM` / `LIVE` chip.
- Deterministic paper runtime: `ingest-worker`, `agent-worker`, `core-worker`.
- Compose stack, Caddy edge config, systemd unit, hosting runbook.

Not implemented:

- Real market data flowing into the runtime.
- Real Alpaca paper execution through the runtime.
- A credentialed paper round-trip test.
- A versioned migration history.
- TimescaleDB hypertables, compression or continuous aggregates.
- Training-data export.

---

## 2. The Phase 1 gap: keys alone do not start it

Phase 1 is the deterministic core. It is implemented, and its drills pass. But
its own audit note is still true: the driver runs `AlpacaAdapter(mock_mode=True)`,
and the new runtime never imports a broker at all.

| Key | What it unlocks today |
|---|---|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Makes a live-mode `AlpacaAdapter` *possible*. Nothing in `axel/runtime/` instantiates one, so no order is sent. |
| `FRED_API_KEY` | Only if code calls `axel/data/providers/fred.py`. `ingest-worker` writes synthetic macro rows. |
| `LLM_PROVIDER` + provider key | Only `axel/agents/openai_compatible.py`. `agent-worker` is deterministic and never imports it. |
| `TELEGRAM_BOT_TOKEN` | The separate signal-listener / alert-relay path, not the runtime. |

**Consequence:** with keys in `.env` you can validate config, run drills, and
run the synthetic paper runtime with a live dashboard. You cannot get a real
Alpaca paper fill.

### What must be built

1. **Real ingest path.** `ingest-worker` uses `AlpacaMarketData` and `FredData`
   when keys are present, synthetic data otherwise, and stamps provenance so
   the UI can distinguish real from simulated.
2. **Real paper execution.** `core-worker` submits to Alpaca paper when
   `ALPACA_PAPER=true` and keys exist, then reconciles fills. Default off.
3. **Credentialed test.** `@pytest.mark.skipif(not ALPACA_API_KEY)`: one paper
   order, one fill, one reconciliation. This closes the last Phase 1 exit
   criterion.

---

## 3. The 56-day soak

### Continuity

The gate is 56 days of paper operation and 200 paper trades
(`docs/phase-5-paper-soak-runbook.md`). It must run continuously on the VPS.

Process continuity is handled by `restart: unless-stopped` and
`deploy/axel.service`. **Evidence** continuity was the gap: `soak_days` was
computed as `now - started_at`, so downtime counted as soak time.

This pass adds worker heartbeats and a coverage calculation (see §4). Run:

```bash
python scripts/soak_status.py --minimum-days 56
```

It reports cycles, gaps, downtime, covered days, and coverage ratio per worker.
The gate uses the **shortest** coverage across `ingest-worker`, `agent-worker`
and `core-worker`.

### Volume

At `PAPER_RUNTIME_INTERVAL_SECONDS=5` the runtime writes roughly
**0.7–1.2M rows/day** (~40–65M over 56 days, ~15–35 GB with indexes):

| Table | Rows/day at 5s |
|---|---:|
| `ohlcv_bars` | 518,400 |
| `equity_snapshots` | 103,680 |
| `signals` (change-gated) | up to 518,400 |
| `audit_log` | 17,280+ |
| `trade_proposals` + `risk_decisions` | ≤14,400 each |

**Run the soak at `PAPER_RUNTIME_INTERVAL_SECONDS=60`.** Real paper trading does
not need 5-second synthetic bars, and it cuts market-data writes by 12×.

Use PostgreSQL, never the default SQLite URL, for the soak.

---

## 4. What changed in this pass

### Read/write path

- **Batched ingest reads.** `latest_prices()` fetches the previous close for
  every symbol in one window-function query instead of one per symbol.
- **Batched, idempotent writes.** `persist_bars()` uses
  `INSERT ... ON CONFLICT DO NOTHING` (PostgreSQL and SQLite) and sends all
  symbols in one `executemany`. The old path issued two `SELECT`s and one
  `INSERT` per bar.
- **Measured effect:** one 30-symbol ingest cycle went from ~95 SQL statements
  to **7 in steady state** (11 on the first cycle, when news rows are created).

### Indexes

Added in `axel/db/models.py`:

| Index | Purpose |
|---|---|
| `ix_ohlcv_bars_symbol_timestamp` | `WHERE symbol = ? ORDER BY timestamp DESC` |
| `ix_signals_section_ts` | section signal windows and TTL lookups |
| `ix_equity_snapshots_section_timestamp` | core's latest-snapshot reads |
| `ix_equity_snapshots_section_upper_timestamp` | dashboard's case-insensitive reads |
| `ix_positions_section_upper` | per-section position counts |
| `ix_audit_log_event_type_timestamp` | closed-trade and event projections |

### Dashboard query fix

`axel/dashboard/projector.py` filtered with
`upper(coalesce(section, ''))`. The columns are `NOT NULL`, so the `coalesce`
was pointless and prevented index use. It is now `upper(section)`, which the
expression index matches. This was the query that would have degraded first.

### Retention

New `axel/db/retention.py` plus `scripts/prune_operational_data.py`. Batched
deletes, conservative defaults:

| Table | Default |
|---|---:|
| `operational_events` | 30 days |
| `audit_log` | 90 days |
| `signals` | 90 days |
| `ohlcv_bars` | 365 days |
| `equity_snapshots` | 730 days |

The previously-unused `operational_event_retention_days` setting is now wired in.

### Continuity evidence

New `worker_heartbeats` table and `axel/runtime/heartbeat.py`. The shared runner
writes a heartbeat in the same transaction as each cycle, so a heartbeat only
exists if the work was durable. A resume after a longer-than-expected gap is
recorded as downtime and a restart.

### Hosting

`docker-compose.yml` now binds Postgres to `127.0.0.1` only and sets sensible
`shared_buffers`, `effective_cache_size`, `max_wal_size`, `wal_compression` and
`synchronous_commit=off` for a paper soak.

---

## 5. Remaining database work

Ordered by risk to the soak.

### 5.1 Initial Alembic revision (must do)

`migrations/versions/` is empty, so the `migrate` service calls `init_db()`.
That means indexes added later **will not** be applied to an existing database.
Generate the initial revision before starting the soak, then switch the
`migrate` command to `alembic upgrade head`.

### 5.2 Run retention on a schedule (must do)

Add a systemd timer or cron entry:

```bash
python scripts/prune_operational_data.py
```

Without it the tables grow without bound.

### 5.3 Hypertables (should do, after the soak starts)

TimescaleDB is installed but never used as TimescaleDB: `create_hypertable`
appears nowhere. Converting gives chunk pruning, compression and cheaper
retention.

**Gotcha:** TimescaleDB requires unique indexes to include the partitioning
column. The current primary keys are `id` alone, so `create_hypertable` fails
until they become `(id, timestamp)`. That change also affects
`session.get(Model, id)` call sites.

Sketch:

```sql
ALTER TABLE ohlcv_bars DROP CONSTRAINT ohlcv_bars_pkey;
ALTER TABLE ohlcv_bars ADD PRIMARY KEY (id, "timestamp");
SELECT create_hypertable('ohlcv_bars', 'timestamp', chunk_time_interval => INTERVAL '1 day');
ALTER TABLE ohlcv_bars SET (timescaledb.compress);
SELECT add_compression_policy('ohlcv_bars', INTERVAL '7 days');
SELECT add_retention_policy('ohlcv_bars', INTERVAL '365 days');
```

Repeat for `signals`, `equity_snapshots` and `audit_log`.

### 5.4 Remaining write-path work

- `_ensure_news` still issues one `SELECT` per source per cycle (4 statements).
- `core_once` writes equity snapshots row by row; batch them.
- `signal` inserts are one statement per changed analyst.

These are small compared with ingest, but they matter at 60-second cadence over
eight weeks.

### 5.5 Analytics and training

The OLTP schema is not a training store. Do not point a training job at the
live database. Use one of:

1. **Continuous aggregates** (native Timescale) for 1m/5m/1h/1d rollups.
2. **Nightly Parquet export** to object storage, then train with DuckDB/Polars.
   This is the recommended default.
3. **ClickHouse** if ad-hoc research over hundreds of millions of rows becomes
   necessary.

`signals.features_ref` is free text, not structured features. A real training
pipeline needs a feature table or the Parquet export.

---

## 6. Kronos

**Decision:** integrate later as an optional, proposal-only forecast analyst.

- Lives in `axel/agents/`, writes `Signal` rows with model id, version, context
  length, horizon and quantiles.
- Never imported by `axel/risk`, `axel/execution` or `axel/portfolio`. Extend
  `scripts/check_no_llm_imports.py` to forbid `torch` there.
- Must pass `axel/data/validation.py` walk-forward validation and beat the
  deterministic trend baseline out-of-sample after costs.
- Pin seed, temperature, top-p and model revision or the soak is not
  reproducible.
- Ship as an optional extra, not a core dependency.

Kronos improves forecast quality. It does not make the system more autonomous.

---

## 7. BrowserSkill

**Decision:** read-only operator and research automation only.

Acceptable:

- Pulling statements from portals that have no API.
- Reading filings, fund pages and regulator sites.
- Human-in-the-loop "open this and show me".

Not acceptable:

- Autonomous broker login or order placement. That is an execution authority
  outside the risk engine, order FSM, reconciliation and audit log, and it
  violates broker terms.
- Credential-bearing browser profiles driven by an LLM. Any prompt injection
  becomes a trade.

If adopted: separate container, separate browser profile, no broker
credentials, allowlisted domains, human approval for writes, untrusted-input
handling, full action log.

"Log in and make money as the system" is a request to remove the safety system.
Autonomy in AXEL means running unattended **within pre-authorized risk caps**,
not unbounded authority to act.

---

## 8. Ordered plan

| # | Work | Acceptance |
|---|---|---|
| 1 | Initial Alembic revision + `alembic upgrade head` in `migrate` | Fresh DB builds from migrations; indexes exist |
| 2 | Retention timer | `prune_operational_data.py` runs nightly; row counts stay bounded |
| 3 | Real ingest adapter | Bars/macro carry provenance; UI shows real vs synthetic |
| 4 | Real Alpaca paper execution | `core-worker` submits and reconciles paper fills |
| 5 | Credentialed paper test | Skipped without keys; one round trip when present |
| 6 | Start the 56-day soak at 60s cadence | `soak_status.py` coverage climbs; no unrecorded gaps |
| 7 | Hypertables + compression | Chunks created; compression active after 7 days |
| 8 | Parquet export | Training data available without touching OLTP |
| 9 | Kronos analyst | Proposal-only, validation-gated |
| 10 | BrowserSkill | Read-only, isolated, audited |

Do not skip from 5 to 6 without 1–2. Do not skip from 6 to live.

---

## 9. Commands

```bash
# Verification
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q

# Phase 1 drills (mock broker)
.venv/bin/python scripts/drive_proposals.py
.venv/bin/python scripts/killswitch_drill.py

# Paper runtime + dashboard
python scripts/run_dashboard_api.py --init-db --port 8000
python scripts/run_paper_runtime.py --cycles 0 --interval 60

# Continuity evidence
python scripts/soak_status.py --minimum-days 56

# Retention
python scripts/prune_operational_data.py
```
