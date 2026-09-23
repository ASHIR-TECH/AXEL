# How the App Works

## 1. System overview

AXEL is a multi-agent trading system designed around one non-negotiable rule:

> **Anything the LLM produces is a *suggestion*. Anything the deterministic core produces is a *decision*.**

The codebase is deliberately split into two worlds:

- **Generative / agent world** — analysis agents (Fundamental, Technical, Sentiment, Macro analysts), an Expert Panel, and an overseer. These do *not exist yet* in this repository phase; their inputs are modeled as immutable contracts so the rest of the system can be built and tested without any LLM dependency.
- **Deterministic core** — risk engine, sizing, kill-switch, watchdog, execution, reconciliation, and storage. This code is pure, testable, and hard-coded: no prompt can change a limit, and no model output can bypass a guard.

The boundary between the two worlds is enforced two ways:

1. **Contract types only** — data crosses the boundary only as validated Pydantic models: `Signal` (analysis, not an order), `Proposal` (a concrete trade idea), `RiskDecision` (the deterministic verdict), `Order` (executable).
2. **Import linting** — `scripts/check_no_llm_imports.py` fails CI if any LLM SDK is imported into `axel/risk/` or `axel/execution/`.

## 2. Repository layout

```text
axel/
├── core/          # Contracts, domain types, config, clock, ids, logging
├── risk/          # Deterministic risk engine, sizing, limits, kill switch, allocator, loss manager
├── execution/     # Broker adapter, order FSM, HITL approvals, reconciliation
├── watchdog/      # Independent health monitor + circuit breaker driver
└── db/            # SQLAlchemy models, engine, session factory
migrations/        # Alembic scaffolding (env.py reads DATABASE_URL from settings)
scripts/           # Standalone runnable drills and boundary lint
tests/             # unit/, integration/, property/ pytest suites
```

## 3. Core building blocks (`axel/core`)

| Module | Responsibility |
|---|---|
| `types.py` | Enums: `Section` (stocks/crypto/options/predictions), `Direction`, `OrderSide`, `OrderType`, `TimeInForce`, `OrderState`, `ProposalStatus`, `RiskVerdict`, `TradingMode`, `StrategyStatus`. |
| `contracts.py` | Frozen, strict Pydantic models that are the **only** structures allowed to cross the LLM/deterministic boundary: `Signal`, `Proposal`, `RiskDecision`, `Order`, `AllocationProposal`. |
| `config.py` | Pydantic-settings based configuration, loaded from `.env`. Enforces the double safety lock (see [§7](#7-safety-and-guardrails)). |
| `clock.py` | Injectable `Clock` abstraction (`RealClock` vs `SimulatedClock`) so the whole system can be run deterministically in tests and drills. |
| `ids.py` | ID generation; `generate_client_order_id()` produces a deterministic, length-safe, idempotency key for broker submissions. |
| `logging.py` | Structured **JSON** logging to stdout (one JSON object per line) for auditability. |

### The contracts (the trust boundary)

- **`Signal`** — emitted by an analyst; describes *direction, strength, horizon*. It is explicitly *not* an instruction to trade.
- **`Proposal`** — emitted by the Expert Panel/Section Manager; a concrete *idea* (symbol, side, entry, stop, target, confidence, win rate, loss ratio). Validation guarantees stop is on the correct side of entry (BUY ⇒ stop < entry; SELL ⇒ stop > entry).
- **`RiskDecision`** — produced *only* by the risk engine; records approve/reject/halt, approved quantity and notional, the binding limit, and the full list of checks passed/failed. This is the object that unlocks execution.
- **`Order`** — an executable order carrying a `client_order_id`; managed by the Order FSM.

## 4. The risk pipeline (`axel/risk`)

This is the heart of the system. Every trade idea must pass through `SectionRiskAgent.evaluate()` — a **pure function** with zero I/O and zero side effects.

### 4.1 Risk limits — immutable and hard-coded

`RiskLimits` (in `limits.py`) are frozen dataclass constants. They **cannot** be changed by configuration or model output:

| Limit | Value | Meaning |
|---|---|---|
| `GLOBAL_MAX_DRAWDOWN` | 10% | Peak-to-trough account drawdown that trips the global kill switch |
| `SECTION_DAILY_STOP` | 3% | Per-section daily stop; blocks new entries when breached |
| `MAX_POSITION_PCT` | 5% | Max notional exposure per trade (of section NAV) |
| `KELLY_CAP` | 25% | Fractional Kelly scaling factor |
| `MAX_PER_TRADE_RISK_PCT` | 1% | Max capital at risk at the stop distance |
| `MIN_RR_RATIO` | 1.5 | Minimum reward:risk ratio |
| `MAX_EXPECTED_R` | 4.0 | Targets above 4R are flagged as likely overfitting/leakage |
| `MIN_PANEL_CONFIDENCE` | 0.30 | Proposals below this confidence are auto-rejected |
| `MAX_OPEN_POSITIONS_PER_SECTION` | 10 | Concurrency cap |
| `CORRELATION_BLOCK_THRESHOLD` | 0.85 | Reject if a proposal correlates with an existing position beyond this |
| `MAX_REALLOCATION_PCT_PER_CYCLE` | 5% | Max capital shifted between sections per rebalance cycle |

### 4.2 The evaluation order in `SectionRiskAgent.evaluate()`

Checks run in strict priority order; the first fatal one short-circuits:

1. **Global kill switch / drawdown** — if the switch is active *or* drawdown ≥ 10% ⇒ verdict `HALTED`, immediate return. Nothing else is checked.
2. **Section daily stop-loss** — `LossManager.evaluate_daily_stop()`; breached if combined realized+unrealized PnL ≤ −3% of start-of-day equity ⇒ block new entries.
3. **Minimum panel confidence** — below 0.30 ⇒ reject.
4. **Concurrent positions cap** — ≥ 10 active positions in the section ⇒ reject.
5. **Risk/reward ratio** — computed from entry/stop/target per side; < 1.5 ⇒ reject.
6. **Overfitting sanity gate** — RR > 4.0 ⇒ flag as likely overfit/leakage.
7. **Correlation concentration** — any existing position with correlation ≥ 0.85 to the proposal ⇒ reject.
8. **Kelly sizing** — `KellySizer.compute_size()`; if zero quantity ⇒ reject.

If any check failed ⇒ `REJECTED` with the full list of `checks_failed` and reasons. If all pass ⇒ `APPROVED` with `approved_qty` and `approved_notional_usd` set from the sizer.

### 4.3 The position sizer (`KellySizer`)

Fractional-Kelly with layered caps:

```text
f*  = (b·p − q) / b        # full Kelly, where p=win rate, q=1−p, b=avg win/loss
f   = min(f*, 1) · 0.25    # fractional scaling
```

Three notional candidates are computed and the **smallest** is binding:

1. Kelly notional: `nav · f`
2. Notional cap: `nav · 5%`
3. Risk-at-stop cap: `(nav · 1%) / stop_distance_pct`

A 5% haircut is applied to historical win rate before computing. Negative/zero edge ⇒ zero allocation (`ZERO_OR_NEGATIVE_EDGE`). Equities round down to whole shares; crypto keeps fractional precision. The result records which cap was binding (`KELLY_FRACTION`, `NOTIONAL_CAP_5PCT`, `RISK_AT_STOP_CAP`).

### 4.4 Loss manager (`LossManager`)

Evaluates a `DailyPnLState` (section, start-of-day equity, realized + unrealized PnL) and returns `ALLOW` / `BLOCK_NEW_ENTRIES` based on the 3% daily stop.

### 4.5 Capital allocator (`CapitalAllocator`)

Validates `AllocationProposal`s (capital shifts between sections produced by the overseer). Rejects shifts > 5% of total portfolio NAV per cycle, identical source/destination, or moves that would leave the source section below a 10% reserve.

### 4.6 Kill switch (`KillSwitch`)

The global circuit breaker:

- Persists state to a JSON file (`.axel_killswitch.json` by default) so a HALT **survives process restarts**.
- `check_equity()` computes drawdown against the tracked **high-water mark**. New highs update the mark; a drawdown ≥ 10% calls `trip()`.
- When tripped: sets `HALTED`, writes the reason + timestamp to disk and JSON logs a `CRITICAL` event.
- `re_arm()` requires a valid operator token (compared via SHA-256 digests against `OPERATOR_REARM_SECRET`) **and** a written rationale ≥ 10 characters. Failed attempts log a warning and raise `PermissionError` / `ValueError`.

## 5. Execution (`axel/execution`)

### 5.1 Broker abstraction (`broker_base.py`)

`BrokerAdapter` is the abstract contract for any broker. Critically, `submit_order()` is guarded **in the base class**:

```text
if risk_decision is not APPROVED  -> raise PermissionError (Execution blocked)
if qty <= 0                       -> raise ValueError
```

So it is structurally impossible to execute an order that was never approved by the risk engine.

### 5.2 Alpaca adapter (`alpaca.py`)

- `mock_mode` is automatic when real credentials are missing or `mock_mode=True` is passed. Mock mode keeps in-memory balance/positions/orders and is fully offline.
- Real mode talks to the Alpaca REST API (`/v2/account`, `/v2/positions`, `/v2/orders`) with `client_order_id` for idempotency.
- `cancel_all_orders()` is the emergency flattening call used by the watchdog on HALT.
- Default base URL is the **paper** endpoint (`https://paper-api.alpaca.markets`).

### 5.3 Order FSM (`order_fsm.py`)

`OrderStateMachine` enforces legal transitions between order states and rejects anything else (`InvalidOrderTransitionError`). Terminal states (`FILLED`, `CANCELED`, `EXPIRED`, `REJECTED`) have no outward transitions. Self-transitions are idempotent no-ops.

### 5.4 Human-in-the-loop approvals (`approval.py`)

`ApprovalService` holds pending operator approvals with per-section TTLs (stocks 5 min, crypto 2 min, predictions 30 min, options 10 min). Tickets expire automatically (`sweep_expired()`); expired tickets cannot be approved retroactively. Only *risk-approved* decisions may enter the queue.

### 5.5 Reconciliation (`reconcile.py`)

`ReconciliationEngine` compares internal DB state against broker reality:

- `reconcile_positions()` — flags quantity drift per symbol.
- `reconcile_orders()` — flags **ghost orders** (on broker but not in DB) and **unmatched DB orders** (tracked but missing from broker) and produces a health report.

## 6. Watchdog (`axel/watchdog`)

`WatchdogMonitor` is meant to run as an independent process:

- `record_heartbeat()` — primary services signal liveness.
- `check_liveness()` — flags if the core has been silent too long.
- `run_health_cycle()` — pulls account equity from the broker, runs it through `KillSwitch.check_equity()`, and on breach **cancels all working orders** and fires the `CRITICAL` intervention alert.

It is an independent safety net: even if the execution bot crashed, the watchdog can still halt the system.

## 7. Safety and guardrails

### The double safety lock (`core/config.py`)

Live trading requires **both** `ENVIRONMENT=live` **and** `LIVE_TRADING_CONFIRMED=true` in the environment; otherwise the config invalid. `is_live_permitted()` additionally requires `ALPACA_PAPER=false`. Paper mode always forces the Alpaca paper flag on.

Other guardrails:

- Order submission requires an explicit `APPROVED` risk decision.
- Kill switch HALT blocks all new proposals at the top of the risk evaluation.
- The execution layer always uses the **paper** endpoint by default and mock mode when unauthenticated.
- LLM SDKs are lint-banned from `risk/` and `execution/`.
- Risk limits are frozen dataclasses (immutable at runtime).

## 8. Storage and migrations (`axel/db`, `migrations`)

- `db/models.py` — 16+ SQLAlchemy models covering: instruments, market calendars, OHLCV bars, news, signals, panel decisions, trade proposals, risk decisions, orders, fills, positions, equity snapshots, kill-switch events, strategy registry, agent runs (LLM observability), audit log. Models intended for TimescaleDB hypertables (bars, signals) note that in comments.
- `db/session.py` — engine/session factories; SQLite gets `check_same_thread=False`; `init_db()` creates all tables (used by tests and the driver script). `DATABASE_URL` is configurable (default `sqlite:///./axel.db`).
- `migrations/env.py` — Alembic wired to `settings.database_url`; `versions/` currently contains only `.gitkeep` (schema is created via `init_db()` at this phase).
- `docker-compose.yml` — optional infra: **TimescaleDB** (PostgreSQL 16) on 5432 and **Redis 7** on 6379, both with healthchecks.

## 9. The healthy-data lifecycle (end to end)

```text
Analyst agents ──► Signal (analysis only)
                         │
Expert Panel / Section Manager ──► Proposal (idea: symbol, entry, stop, target, confidence)
                         │
              ┌──────────▼───────────┐
              │ SectionRiskAgent      │  <-- pure, deterministic, no I/O
              │  evaluate()           │
              └──────────┬───────────┘
                         │ RiskDecision (APPROVED / REJECTED / HALTED)
                  (live or soak only)
                         ▼
              ApprovalService (HITL ticket, TTL)
                         │ operator approves
                         ▼
              Order + RiskDecision ──► BrokerAdapter.submit_order (guarded)
                         │
                         ▼
                   Broker (paper/mock)
                         │
              Order FSM tracks state ──► ReconciliationEngine
                         │
                   DB (orders, fills, positions, equity snapshots)
                         │
              WatchdogMonitor ──► KillSwitch ──► HALT on ≥10% drawdown
```

## 10. What is deliberately NOT in this phase

Trading discipline means honoring boundaries:

- No LLM/agent code, no `openai`/`anthropic` imports anywhere.
- No live market-data ingestion yet (OHLCV schema exists, feed does not).
- No live trading: everything defaults to paper/mock; live requires flipping all three safety locks.
- No automated operand of the HITL gateway in this phase — approvals are manual by design.