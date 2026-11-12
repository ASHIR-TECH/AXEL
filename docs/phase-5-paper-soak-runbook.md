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

## Real-data, real-fill paper mode

Two flags select the official 56-day configuration. Both default to the
offline behaviour, so nothing changes until they are set explicitly:

```dotenv
PAPER_DATA_SOURCE=alpaca   # real IEX 1-min bars, STOCKS section only
PAPER_EXECUTION=alpaca     # approved orders go to the Alpaca PAPER endpoint
```

- `PAPER_DATA_SOURCE=synthetic` (default) keeps the deterministic offline feed
  for all five sections. `alpaca` narrows the runtime to the `STOCKS` section
  (`axel.runtime.alpaca_feed`, `axel.runtime.sections`) so no proposal can form
  on stale synthetic bars.
- `PAPER_EXECUTION=internal` (default) keeps the modeled fill (reference price
  plus a 2 bps assumption). `alpaca` submits the already-approved order to
  `https://paper-api.alpaca.markets` and records the broker's real fill,
  broker order id and realized slippage (`axel.runtime.execution`).
- The Alpaca backend refuses to start unless `ALPACA_PAPER=true` and the
  adapter's base URL contains `paper-api`. It also refuses to trade outside the
  US regular session, expressed in `America/New_York` so the window follows
  daylight saving; an order that does not fill is cancelled and the proposal is
  re-queued as `pending` instead of inventing a fill.
- `scripts/paper_live.py` reads the paper orders and reports how many filled and
  how many are still working.
- On the cloud box the worker units need `HTTPS_PROXY`/`HTTP_PROXY` pointing at
  the local egress proxy (`http://127.0.0.1:3128`) or every Alpaca call fails
  with a DNS error; keep it in a systemd drop-in rather than in `.env`.

Start the clock on a clean book so synthetic history cannot be mistaken for the
real run: create a fresh database, point `DATABASE_URL` at it, run
`init_db()`, and record the `paper_soak.started` audit marker with
`feed="alpaca-iex"`, `execution="alpaca-paper"` and the 56-day `window_end`.

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
