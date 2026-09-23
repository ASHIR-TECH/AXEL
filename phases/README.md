# AXEL Phases — Documentation Index

Welcome to the AXEL documentation. AXEL is an **Autonomous Multi-Agent Trading System** whose core design principle is a strict separation between *generative* (LLM) agents and a *deterministic* trading core that can never be influenced by model output.

This `phases/` folder contains living documentation that tracks the build-out of the system. It is written to answer three questions:

1. **What is the system?** — see [How the App Works](how-the-app-works.md)
2. **How do I run it?** — see [Runbook](run-and-debug.md#runbook)
3. **How do I debug it when something breaks?** — see [Debugging](run-and-debug.md#debugging)

---

## Documentation map

| Document | Purpose |
|---|---|
| [`how-the-app-works.md`](how-the-app-works.md) | Full architecture: modules, data contracts, the risk pipeline, execution flow, safety/guardrail design, and the immutable risk limits. |
| [`run-and-debug.md`](run-and-debug.md) | Everything needed to get AXEL running locally: environment setup, the run scripts, the test/lint suites, and a debugging cookbook. |

---

## Implementation status (current phase)

The repository currently implements **Phase 1: The Deterministic Core** — the part of the system that will never depend on an LLM.

### Implemented today

- **Deterministic Risk Engine** (`SectionRiskAgent`) — pure-function gatekeeper that every trade proposal must pass.
- **Position Sizer** — fractional-Kelly sizing with hard notional and risk-at-stop caps.
- **Global Kill Switch / Circuit Breaker** — 10% peak-to-trough drawdown halts all trading, survives restarts, manual operator re-arm required.
- **Watchdog Monitor** — independent health cycle that trips the kill switch and cancels open orders.
- **Execution layer** — Alpaca broker adapter (paper/live/mock), guarded order submission, Order FSM, HITL (Human-In-The-Loop) approval service, and DB↔broker reconciliation.
- **Storage layer** — SQLAlchemy models for the full domain (instruments, bars, signals, proposals, orders, fills, positions, equity snapshots, kill-switch events, audit logs) with Alembic migrations scaffolding.
- **Explicitly absent**: LLM agents, agent orchestration, live market data ingestion — these live in later phases.

### Planned phases (roadmap)

| Phase | Scope |
|---|---|
| Phase 1 (done) | Deterministic core, risk engine, kill switch, paper execution, DB schema, drill scripts |
| Phase 2 | Agent service (analysts, expert panel, overseer) using LLM providers, with the LLM→core boundary enforced by `Signal`/`Proposal` contracts |
| Phase 3 | Market data ingestion (OHLCV → TimescaleDB hypertables), scheduler, forecasting |
| Phase 4 | Live trading enablement behind the double safety lock, HITL approval gateway, reconciliation automation |

Details of implemented behaviour are in [How the App Works](how-the-app-works.md). To get a working checkout run the commands in the [Runbook](run-and-debug.md#runbook).

---

## Quick reference

```text
$ make?  (no Makefile yet — use scripts directly)
$ .venv/bin/python scripts/drive_proposals.py      # run the deterministic pipeline drill
$ .venv/bin/python scripts/killswitch_drill.py     # run the kill-switch drill
$ .venv/bin/python -m pytest -q                    # run the test suite (32 tests)
$ .venv/bin/python -m ruff check .                 # lint
$ .venv/bin/python scripts/check_no_llm_imports.py # architectural boundary lint
```