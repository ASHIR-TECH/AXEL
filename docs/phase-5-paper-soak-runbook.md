# Phase 5 — Stocks paper-soak runbook

Phase 5 begins the **paper-only observation period**. It is not live-trading approval and it must run for at least eight weeks before a promotion decision can be considered.

## Before the first run

1. Keep `.env` explicitly paper-only:

   ```dotenv
   ENVIRONMENT=paper
   LIVE_TRADING_CONFIRMED=false
   ALPACA_PAPER=true
   ```

2. Configure Alpaca **paper** credentials and, if using agents, one selected provider (`LLM_PROVIDER=qwen` or `LLM_PROVIDER=groq`). See `docs/run-paper-data-and-agents.md`.
3. Run baseline verification:

   ```bash
   make check
   .venv/bin/python scripts/killswitch_drill.py
   .venv/bin/python scripts/check_agent_config.py
   ```

4. Start TimescaleDB/Redis only when your database URL points to the Docker service:

   ```bash
   docker compose up -d
   docker compose ps
   ```

## What to record during the soak

- Every paper trade: forecast probability, final outcome, expected slippage, and realized slippage.
- Every reconciliation result and whether it was resolved.
- Every alert, stale-data event, rejected proposal, or process restart as an incident.
- A kill-switch drill at least every 30 days.
- One recovery drill: stop the process while an order is active, restart, and verify reconciliation.

`axel.operations.paper_soak` evaluates this evidence with fail-closed rules. It does **not** alter live-trading settings or send an order.

## Promotion gate

All of the following must be evidenced before Phase 6 is considered:

1. At least 56 days of paper operation and 200 paper trades.
2. Brier score at or below 0.25 (calibrated confidence).
3. Mean realized-vs-expected slippage delta at or below 3 bps.
4. Zero unresolved reconciliation breaks.
5. A kill-switch drill passed in the preceding 30 days.
6. A recovery drill passed.
7. Explicit human sign-off recorded separately.

These are evidence requirements, not an automatic permission to turn on live mode. Do not set `ENVIRONMENT=live` or `LIVE_TRADING_CONFIRMED=true` during Phase 5.
