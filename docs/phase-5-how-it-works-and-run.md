# Phase 5: how it works and how to run it

Phase 5 is AXEL’s **Stocks paper soak**. It observes paper trades and system health for at least eight weeks; it does not permit live trading.

## How it works

```text
Paper proposal → deterministic risk gate → paper broker → fills / reconciliation
       │                    │                         │
       └──────────────► Phase 5 evidence ◄────────────┘
                              │
             calibration + slippage + incidents + drills
                              │
                    promotion-gate report (read-only)
```

The Phase 5 module is `axel/operations/paper_soak.py`. It collects no credentials and cannot place an order or change the trading mode.

For every completed paper trade, record:

- `predicted_probability`: the confidence used for the decision, between 0 and 1.
- `outcome`: whether the trade outcome was successful.
- `expected_slippage_bps`: slippage assumed before the trade.
- `realized_slippage_bps`: actual paper fill slippage.

The promotion report calculates:

- **Brier score**: average squared error between forecast probability and outcome. Lower is better; Phase 5 requires `≤ 0.25`.
- **Mean slippage delta**: average absolute difference between expected and realized slippage. Phase 5 requires `≤ 3 bps`.
- **Operational evidence**: soak duration, trade count, unresolved reconciliation breaks, kill-switch drill recency, and recovery-drill status.

Missing evidence fails closed. A passing report is evidence for a human review; it never switches AXEL to live trading.

## 1. Configure paper mode

Create/update `.env` from `.env.example`:

```dotenv
ENVIRONMENT=paper
LIVE_TRADING_CONFIRMED=false
ALPACA_PAPER=true

ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret

# Select only one optional LLM provider.
LLM_PROVIDER=qwen
QWEN_API_KEY=your_qwen_key
LLM_BASE_URL=https://your-qwen-region-or-workspace/compatible-mode/v1
LLM_MODEL=your-qwen-model
LLM_HOURLY_BUDGET_USD=0.25
LLM_DAILY_BUDGET_USD=1.00
```

Do not put keys in source files or commit `.env`.

## 2. Verify before starting

From the repository root:

```bash
make check
.venv/bin/python scripts/check_agent_config.py
.venv/bin/python scripts/killswitch_drill.py
```

Expected result: tests, lint, and the no-LLM-in-core rule pass; the agent-config script reports `provider=qwen` (or `provider=groq`) and your model without displaying your key; the kill-switch drill passes.

If you are using the containerized database:

```bash
docker compose up -d
docker compose ps
```

Only do this after setting `DATABASE_URL` to the TimescaleDB connection value in `.env`. The local test suite uses SQLite and does not require Docker.

## 3. Record the soak evidence

The production paper runner/scheduler must write these observations as trades complete. Until that runner is attached, use the Phase 5 module only in a controlled development test. For example:

```python
from datetime import UTC, datetime, timedelta
from axel.operations.paper_soak import PaperTradeObservation, evaluate_promotion_gate

now = datetime.now(UTC)
observations = [
    PaperTradeObservation(
        predicted_probability=0.60,
        outcome=True,
        expected_slippage_bps=1.0,
        realized_slippage_bps=1.5,
    )
]

report = evaluate_promotion_gate(
    started_at=now - timedelta(days=56),
    observations=observations,
    unresolved_reconciliation_breaks=0,
    killswitch_drill_passed_at=now,
    recovery_drill_passed=True,
)
print(report)
```

One sample trade will correctly fail the 200-trade requirement. Do not lower production thresholds merely to make a report pass.

Use `record_incident()` to append a paper-soak incident to `audit_log` for a stale feed, reconciliation break, process restart, unexpected alert, or rejected order. Resolve incidents through an additional audit record—do not overwrite history.

## 4. Weekly operating routine

1. Check dashboard metrics, rejected proposals, fills, and reconciliation status.
2. Review LLM cost against the hourly/daily cap.
3. Record every incident and its resolution rationale.
4. Compare paper slippage with the strategy assumption.
5. Check confidence calibration (Brier score) after enough outcomes exist.
6. Run the kill-switch drill at least every 30 days.
7. Run one recovery drill during the soak: stop the process mid-order, restart, then confirm state reconciliation.

## Phase 5 exit criteria

Phase 6 cannot be considered until all are true:

1. 56 or more days of continuous paper operation.
2. 200 or more paper trades (or an agreed equivalent for low-frequency strategies).
3. Brier score `≤ 0.25`.
4. Mean realized-vs-expected slippage delta `≤ 3 bps`.
5. No unresolved reconciliation breaks or risk-rule bypasses.
6. Kill-switch drill passed within the last 30 days.
7. Recovery drill passed.
8. Written human sign-off.

Even then, do not change `ENVIRONMENT=live` without an explicit Phase 6 decision and manual approval policy.
