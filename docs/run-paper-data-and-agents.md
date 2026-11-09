# Run AXEL: paper data and low-cost models

AXEL is **paper-only by default**. These steps do not enable live trading. Never paste API keys into chat or commit `.env`.

## 1. Install and verify

```bash
cd /home/contractor/Koding/AXEL
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
make check
```

Expected result: Ruff passes, the no-LLM-in-core check passes, and all tests pass.

## 2. Configure secrets locally

```bash
cp .env.example .env
```

Keep these safe defaults:

```dotenv
ENVIRONMENT=paper
LIVE_TRADING_CONFIRMED=false
ALPACA_PAPER=true
```

Then add only the credentials for services you choose:

```dotenv
# Read-only Alpaca market-data API credentials; use a paper account.
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...

# FRED key (free registration): macro data.
FRED_API_KEY=...

# Required by SEC EDGAR for respectful, identifiable access.
SEC_USER_AGENT="Your Name your-email@example.com"

# Optional LLM. Select exactly one provider and provide its matching key.
LLM_PROVIDER=qwen
QWEN_API_KEY=...
LLM_BASE_URL=https://your-qwen-workspace-or-region/compatible-mode/v1
LLM_MODEL=your-qwen-model-id

# Alternative: Groq's OpenAI-compatible endpoint.
# LLM_PROVIDER=groq
# GROQ_API_KEY=...
# LLM_BASE_URL=                         # blank uses Groq's official default
# LLM_MODEL=choose-a-current-supported-model-from-your-Groq-account
LLM_HOURLY_BUDGET_USD=0.25
LLM_DAILY_BUDGET_USD=1.00
```

Qwen's compatible-mode endpoint is region/workspace-specific. Copy the base URL from your Qwen/Model Studio account and set it as `LLM_BASE_URL`; do not use a Groq URL with a Qwen key. Do not select a model name from this document; availability and free tiers change frequently.

### Multiple Alpaca accounts and Equibles

Read-only ingestion can rotate across several Alpaca accounts when one hits its
rate limit. Execution is unaffected: it still uses the primary pair.

```dotenv
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
# Optional second account (or use ALPACA_API_KEYS=key:secret,key:secret)
ALPACA_API_KEY_2=...
ALPACA_SECRET_KEY_2=...
# failover (default) | round_robin | random
ALPACA_KEY_STRATEGY=failover
ALPACA_KEY_COOLDOWN_SECONDS=60
```

- `failover` keeps the current account until it returns `429`, then moves on.
- `round_robin` cycles on every request.
- `random` picks uniformly from the accounts not currently rate limited.

A rate-limited account is quarantined for `Retry-After` when the provider sends
it, otherwise for `ALPACA_KEY_COOLDOWN_SECONDS`. The pool is process-local and
thread-safe; secrets never appear in logs or snapshots.

Equibles (daily prices only) is an alternative data source and supports the same
rotation policy as Alpaca:

```dotenv
EQUIBLES_API_KEY=eq_...
# Optional second key (or use EQUIBLES_API_KEYS=key,key)
EQUIBLES_API_KEY_2=eq_...
EQUIBLES_KEY_STRATEGY=failover
EQUIBLES_KEY_COOLDOWN_SECONDS=60
EQUIBLES_BASE_URL=https://api.equibles.com/v1
```

Equibles keys have no separate secret; the provider repeats the key internally
so it can reuse the same credential-pool interface. A rate-limited key is
quarantined exactly like an Alpaca account.

```bash
.venv/bin/python scripts/fetch_historical_bars.py \
  --provider equibles --timeframe 1Day \
  --symbols AAPL,MSFT --start 2026-01-01T00:00:00+00:00 \
  --end 2026-11-09T00:00:00+00:00 --out data/equibles_bars.csv
```

The provider sends the key as `Authorization: Bearer <key>` and refuses
intraday timeframes rather than returning data that looks valid but is not.

Massive (formerly Polygon.io) provides aggregate OHLCV bars and authenticates
with the `apiKey` query parameter:

```dotenv
MASSIVE_API_KEY=...
MASSIVE_BASE_URL=https://api.massive.com
```

```bash
.venv/bin/python scripts/fetch_historical_bars.py \
  --provider massive --timeframe 1Day \
  --symbols AAPL,MSFT --start 2026-10-01T00:00:00+00:00 \
  --end 2026-10-10T00:00:00+00:00 --out data/massive_bars.csv
```

Massive bars are split-adjusted by default (`adjusted=true`), and the adapter
records `split_adjusted` on each record so a consumer never mistakes an
adjusted series for raw prices. Pagination follows `next_url` while keeping the
key on each request.

SEC EDGAR access is keyed by `SEC_USER_AGENT`, which must include a contact
email: `SEC_USER_AGENT="Your Name you@example.com"`.

Twelve Data provides daily and intraday OHLCV via `apikey`:

```dotenv
TWELVE_DATA_API_KEY=...
TWELVE_DATA_BASE_URL=https://api.twelvedata.com
```

```bash
.venv/bin/python scripts/fetch_historical_bars.py \
  --provider twelve_data --timeframe 1Day --adjustment split \
  --symbols AAPL,MSFT --start 2026-10-01T00:00:00+00:00 \
  --end 2026-10-10T00:00:00+00:00 --out data/twelve_data_bars.csv
```

`--adjustment raw|split|all` maps to Twelve Data's `none|splits|all`.
Intraday timestamps are localized with the response's `exchange_timezone`
before being converted to UTC.

Finnhub's free tier covers company news, quotes, search and metrics. Historical
candles are a paid endpoint; the adapter raises that error explicitly instead
of returning empty bars.

```dotenv
FINNHUB_API_KEY=...
FINNHUB_BASE_URL=https://finnhub.io/api/v1
```

```bash
.venv/bin/python scripts/fetch_company_news.py \
  --symbols AAPL,MSFT --start 2026-10-01T00:00:00+00:00 \
  --end 2026-10-10T00:00:00+00:00 --out data/finnhub_news.jsonl
```

Finnhub authenticates with the `X-Finnhub-Token` header, so the key does not
appear in request URLs. News is written as canonical JSONL records with
provenance and entity hints; the text is treated strictly as data.

Validate that AXEL has linked the selected provider and key correctly, without sending a prompt:

```bash
.venv/bin/python scripts/check_agent_config.py
```

Expected output includes your selected provider and model but never prints the API key.

## 3. Run the existing safety drills

```bash
.venv/bin/python scripts/drive_proposals.py
.venv/bin/python scripts/killswitch_drill.py
```

These run with the mock broker and never send a live order.

## 4. What the new components do

- `axel/data/providers.py` contains read-only clients for Alpaca historical bars, FRED observations, and SEC EDGAR submissions. They are library components at present, so they do not poll or download by themselves.
- `axel/data/backtester.py` implements a deterministic cost-aware trend baseline; `axel/data/validation.py` rejects insufficient samples, weak performance, excessive drawdown, and implausibly high expectancy.
- `axel/agents/openai_compatible.py` is an optional proposal-only gateway. It has no broker or execution import. The budget guard blocks calls above the configured hourly/daily cap.
- `axel/dashboard/decision_log.py` is read-only; it cannot submit, approve, or alter an order.

## 5. Optional local infrastructure

```bash
docker compose up -d
docker compose ps
```

This starts TimescaleDB and Redis. It downloads container images the first time. Keep the default SQLite setup for unit tests; use TimescaleDB when we add the scheduled ingestion and Grafana provisioning.

## Safety rules

1. Keep `ENVIRONMENT=paper`, `LIVE_TRADING_CONFIRMED=false`, and `ALPACA_PAPER=true`.
2. Use data-provider credentials only in `.env`; `.env` is ignored by Git.
3. Start with a low LLM budget. A low-cost Groq/Qwen endpoint is appropriate for analysis only, never for final trade approval.
4. The deterministic risk engine, approval gate, and kill switch remain the only path toward an order.

## Remaining build steps

Before paper trading can run end-to-end, AXEL still needs scheduled ingestion/storage, walk-forward baseline reports, persistence of agent signals/proposals, Grafana provisioning, alerts transport, and the required paper soak. Track the implementation status in `docs/phase-0-1-audit-and-phase-3-4-plan.md`.
