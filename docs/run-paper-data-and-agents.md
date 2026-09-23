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
