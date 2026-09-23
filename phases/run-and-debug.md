# Runbook & Debugging Guide

This document tells you how to get AXEL running locally, how to exercise the system safely (paper/mock only), and how to debug it when things misbehave.

---

## Part A — Runbook

### A.0 Prerequisites

- **Python ≥ 3.11** (repo target is 3.12, developed against 3.13).
- `pip`, `venv`.
- *(Optional)* Docker for Postgres/TimescaleDB + Redis — not required for the default SQLite/mock setup.
- *(Optional)* An Alpaca paper account + keys **only if** you want real paper execution from the adapter.

### A.1 One-time setup

```bash
# 1. Clone / enter repo
cd axel

# 2. Create virtual environment and install the package with dev extras
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 3. (Optional) configure environment
cp .env.example .env
#  .env is git-ignored — never commit real keys
```

`.env` values: see `axel/core/config.py` for the authoritative list. Defaults are safe for local dev:

- `ENVIRONMENT=paper`, `LIVE_TRADING_CONFIRMED=false`
- `DATABASE_URL=sqlite:///./axel.db` (SQLite unless you started the Timescale container — then use `postgresql+psycopg2://axel:axelpass@localhost:5432/axel`, and add `psycopg2` to the environment)
- Alpaca keys optional — adapter falls back to **mock mode** if they are missing

> **Never** run with `ENVIRONMENT=live` + `LIVE_TRADING_CONFIRMED=true` + real Alpaca keys unless you truly intend to trade real money. The config will let you, and the system will execute.

### A.2 Sanity checks — is the checkout healthy?

```bash
# Lint (ruff)
.venv/bin/python -m ruff check .

# Architectural boundary lint (no LLM imports in risk/ or execution/)
.venv/bin/python scripts/check_no_llm_imports.py

# Full test suite (unit + integration + property)
.venv/bin/python -m pytest -q
```

Expected on a clean checkout:

```text
ruff       -> All checks passed!
check_no_llm_imports -> [PASS] No forbidden LLM imports detected...
pytest     -> 32 passed
```

### A.3 Running the system

There is **no long-running daemon yet** — Phase 1 is exercised through standalone drill scripts that wire real components together in-memory.

#### Driver: deterministic core pipeline (no LLM)

```bash
.venv/bin/python scripts/drive_proposals.py
```

What it does:

1. `init_db()` — creates the SQLite schema (`axel.db`) in the repo root.
2. Constructs `SectionRiskAgent` (real, deterministic) + `AlpacaAdapter(mock_mode=True)`.
3. Feeds **4 hand-crafted proposals** through risk evaluation:
   - `AAPL` — compliant 2.5:1 RR ⇒ expected **APPROVED** and submitted via the broker.
   - `MSFT` — 1.2:1 RR < 1.5 ⇒ expected **REJECTED** (RR_TOO_LOW / confidence gate).
   - `TSLA` — 6:1 RR > 4.0R cap ⇒ expected **REJECTED** (overfitting flag).
   - `NVDA` — 0.25 confidence < 0.30 ⇒ expected **REJECTED** (confidence gate).
4. Prints verdicts, approved quantities, binding limits, and broker ack state.

Exit code `0` and `Summary: 1 of 4 proposals approved and executed.` = success.

#### Kill-switch drill

```bash
.venv/bin/python scripts/killswitch_drill.py
```

Walks through the full circuit-breaker story:

1. Baseline high-water mark at $100,000.
2. Portfolio drops to $89,000 (11% drawdown) → watchdog health cycle detects it → **HALT**, working orders cancelled, JSON `CRITICAL` log fired.
3. New proposal during HALT ⇒ verdict **HALTED** (blocked).
4. Unauthorized re-arm (wrong token) ⇒ `PermissionError` (blocked).
5. Re-arm with short rationale ⇒ `ValueError` (blocked).
6. Authorized re-arm (valid token + written rationale) ⇒ system re-armed at new baseline.

Exit code `0` and `Kill-Switch Drill PASSED all criteria!` = success. State file `.drill_killswitch.json` is created and cleaned up by the script.

### A.4 Run a single test / subset

```bash
# one file
.venv/bin/python -m pytest tests/unit/test_risk_engine.py -q

# one test by name
.venv/bin/python -m pytest tests/unit/test_risk_engine.py::test_xxx -q

# only integration tests
.venv/bin/python -m pytest tests/integration -q
```

### A.5 Optional infrastructure (Docker)

```bash
docker compose up -d          # starts TimescaleDB (5432) + Redis (6379)
docker compose ps             # both should show "healthy"
```

Afterwards point `DATABASE_URL` at Postgres and re-run `init_db()` (e.g. via the driver script) to create the schema there. To wipe containers/data afterwards:

```bash
docker compose down -v
```

### A.6 Working with the database

- **Schema creation** is handled by `axel.db.session:init_db()` (`Base.metadata.create_all`). It runs automatically in the driver script and in tests.
- Alembic scaffolding exists (`migrations/env.py` reads `DATABASE_URL` from settings), but `versions/` is empty — no migrations to run yet.
- Default SQLite file is `./axel.db` (git-ignored). Delete it to reset local state:

```bash
rm -f axel.db
```

### A.7 Interpreting logs

Logging is **JSON on stdout**, one object per line:

```json
{"timestamp": "...", "level": "CRITICAL", "logger": "axel",
 "message": "KILL SWITCH TRIPPED: All automated executions HALTED.",
 "extra": {"reason": "GLOBAL DRAWDOWN BREACH: ...", "section": null, "timestamp": "..."}}
```

Levels are set by `LOG_LEVEL` (default `INFO`). `DEBUG` also enables SQLAlchemy `echo` (engine prints SQL). To pretty-print in a terminal:

```bash
.venv/bin/python scripts/killswitch_drill.py 2>&1 | jq -c .      # needs jq
```

---

## Part B — Debugging

### B.0 Golden rules

1. **Reproduce with the smallest failing unit first.** The risk engine is a pure function — most bugs here are data bugs, not I/O bugs.
2. **Use the simulated clock.** `SimulatedClock` (`axel/core/clock.py`) makes time-dependent logic (TTLs, expirations) fully deterministic. Mirror the `sim_clock` fixture in `tests/conftest.py`.
3. **Check the boundary lint first** if you see imports problems:

```bash
.venv/bin/python scripts/check_no_llm_imports.py
```

4. **Never debug with live keys.** `mock_mode=True` keeps every run offline and idempotent.

### B.1 Common failure signatures and root causes

#### "EXECUTION BLOCKED: Cannot submit order ... without an APPROVED RiskDecision"

Cause: you (or your code) called `BrokerAdapter.submit_order()` with a non-`APPROVED` decision. This is by design.

Fix: the order must originate from `SectionRiskAgent.evaluate()` returning `verdict == APPROVED` with `approved=True`. Do not bypass by constructing a fake `RiskDecision(applied=True)` in production paths — the guard exists specifically to prevent that. For tests, the existing fixtures already set up approved flow correctly.

#### Kill switch trips but you expected it not to

Cause: a stale state file. The kill switch persists to `.axel_killswitch.json` (or the path given) and loads it on startup.

Diagnose:

```bash
cat .axel_killswitch.json            # shows halted, reason, halted_at, high_water_mark
```

Fix (dev only!): delete the state file to clear a HALT, then re-verify via the drill.

#### Proposal rejected for "SIZING_ZERO_QTY"

Cause: the sizer allocated zero. Inspect `RiskDecision.reasons` for the binding limit:

- `ZERO_OR_NEGATIVE_EDGE` — notional expected-value ≤ 0 after the win-rate haircut. Raise `win_rate`/`avg_win_loss_ratio` in the proposal.
- `STOP_TOO_CLOSE` — stop within 0.01% of entry.
- `INVALID_INPUTS` — nav/entry/stop ≤ 0.

#### Verdict shows REJECTED with multiple `checks_failed`

The engine accumulates all non-fatal failures (only step 1 short-circuits). Read the `reasons` array in the `RiskDecision` — it names exactly which limit and numbers were involved. Mirror the values in `RiskLimits` (`axel/risk/limits.py`).

#### HITL ticket "has already expired" or "cannot be approved"

Cause: TTL elapsed (see per-section TTLs in `ApprovalService.DEFAULT_TTLS`) or ticket already decided.

Debug with the simulated clock: create ticket, `clock.advance(timedelta(seconds=ttl + 1))`, then `approve()` → expect expiry path. See `tests/unit/test_approval.py`.

#### Reconciliation reports "ghost orders" / "unmatched orders"

Cause: internal DB state and broker state diverged.

- Ghost: client_order_id present on broker but absent in DB → an order was sent without recording, or DB state was reset while broker orders remained.
- Unmatched: open DB order missing at broker → possibly cancelled/expired at broker but not synced.

In mock mode this is fully reproducible: insert into `broker._mock_orders` or the DB independently, then run `ReconciliationEngine` and inspect the `ReconciliationReport`.

#### SQLite database lock / "database is locked" errors

Cause: multiple writers. SQLite is for dev/tests only.

Fix: prefer in-memory DB in tests (`conftest.in_memory_db`), single process for driver scripts, or move to Postgres/TimescaleDB via docker compose for multi-process work.

### B.2 Debugging techniques in practice

#### 1. Insert print/inspect points (quick)

The engine is pure — add temporary prints in `SectionRiskAgent.evaluate()` or, better, use a small REPL session:

```python
from axel.core.contracts import Proposal
from axel.core.types import OrderSide, Section, TradingMode
from axel.risk.engine import AccountState, SectionRiskAgent, SectionRiskState

risk_agent = SectionRiskAgent()
prop = Proposal(section=Section.STOCKS, symbol="SPY", side=OrderSide.BUY,
                entry=500.0, stop=495.0, target=515.0, strategy_id="dbg",
                panel_confidence=0.9, mode=TradingMode.PAPER)
account = AccountState(total_equity=100_000.0, cash=50_000.0, global_drawdown_pct=0.0)
section = SectionRiskState(section=Section.STOCKS, section_nav=100_000.0, start_of_day_equity=100_000.0)
dec = risk_agent.evaluate(prop, account, section)
print(dec.verdict, dec.checks_failed, dec.reasons, dec.binding_limit)
```

#### 2. Use `pytest -q -x` and the existing tests as a spec

The tests encode the expected behaviour. Find the matching test file and run just it. If your behaviour differs from a test, either the code or the test is wrong — decide which by reading `RiskLimits`.

#### 3. Watch JSON logs for structured signals

Kill-switch trips, re-arms, HITL lifecycle, and reconciliation breaks all emit structured `extra` fields. Pipe through `jq` or grep by `level`:

```bash
.venv/bin/python scripts/killswitch_drill.py 2>&1 | grep '"level": "CRITICAL"'
```

#### 4. Debug the broker without a network

Everything broker-related is mockable:

```python
from axel.execution.alpaca import AlpacaAdapter
broker = AlpacaAdapter(mock_mode=True)
broker._mock_balance["total_equity"] = 89_000.0   # simulate the drawdown
broker.get_account_summary()                        # -> {"total_equity": 89000.0, ...}
```

#### 5. SQL echo for query debugging

Set `LOG_LEVEL=DEBUG` in `.env` — `create_db_engine` enables `echo=True` when level is DEBUG, printing every SQL statement to stderr.

### B.3 Debugging the kill switch across restarts

The kill switch persists state — so you can prove the HALT survives a process restart:

```bash
.venv/bin/python scripts/killswitch_drill.py        # tripped during run, state file deleted at end
# To observe persistence, trip it manually:
.venv/bin/python -c "
from axel.risk.killswitch import KillSwitch
k = KillSwitch()
k.set_high_water_mark(100000.0)
print('halted:', k.check_equity(89_000.0))          # trips and saves .axel_killswitch.json
"
.venv/bin/python -c "
from axel.risk.killswitch import KillSwitch
k = KillSwitch()                                     # reloads state from disk
print('still halted after restart:', k.is_halted, k.halt_reason)
"
rm -f .axel_killswitch.json
```

Use `SimulatedClock` when you want the timestamps stored in state to be deterministic.

### B.4 Property / fuzz failures

`tests/property/test_risk_properties.py` (hypothesis) can surface edge cases: NaN/Inf defaults, negative NAVs, absurd RRs. If it fails:

- Note the counterexample hypothesis prints.
- Check the sizer guard clauses (`nav <= 0`, `stop_distance_pct < 1e-4`, edge ≤ 0).
- The engine must never raise from bad numeric input — it should return a `REJECTED`/zero-qty decision. Treat an exception as the bug.

---

## Part C — Quick command cheat sheet

```bash
# environment
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env

# verify
.venv/bin/python -m ruff check .
.venv/bin/python scripts/check_no_llm_imports.py
.venv/bin/python -m pytest -q

# run drills
.venv/bin/python scripts/drive_proposals.py
.venv/bin/python scripts/killswitch_drill.py

# tests, targeted
.venv/bin/python -m pytest tests/unit/test_risk_engine.py -q
.venv/bin/python -m pytest tests/integration -q

# infra (optional)
docker compose up -d
docker compose down -v

# reset local dev state
rm -f axel.db .axel_killswitch.json .drill_killswitch.json
```