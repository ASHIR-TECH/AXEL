# Logging, API health and failed transactions

AXEL has to answer two operational questions without reading source code or
attaching a debugger:

1. **Is an external API token/credential exhausted, revoked or rate limited?**
2. **Did a transaction fail to go through, and why?**

This document describes the structured log events, the durable health tables,
the Prometheus metrics and the alerts that answer those questions.

Everything here is **observability only**. Recording a failure never submits an
order, retries a transaction, clears a halt, or re-arms the kill switch.

---

## 1. Where logs go

Every AXEL process emits one JSON object per line to **stdout**. Containerised
deployments collect that automatically through Docker/`journald`.

Set `LOG_FILE` to also write a rotating JSON file:

```dotenv
LOG_FILE=/var/log/axel/axel.log
LOG_MAX_BYTES=10000000   # 10 MB per file
LOG_BACKUP_COUNT=5       # keep 5 rotated files
```

`LOG_FILE` empty (the default) keeps stdout-only output. Docker Compose mounts
the shared `axel_logs` volume at `/var/log/axel`, so the trading core, exporter
and alert relay each write their own file there.

A log line looks like:

```json
{
  "timestamp": "2026-11-01T10:12:33.481+00:00",
  "level": "CRITICAL",
  "logger": "axel",
  "message": "groq chat_completion failed: quota_exhausted",
  "event": "api_quota_exhausted",
  "extra": {
    "provider": "groq",
    "operation": "chat_completion",
    "outcome": "quota_exhausted",
    "status_code": 429,
    "retry_after_seconds": 60.0
  }
}
```

`event` is a stable, machine-searchable name. Use it instead of matching the
human-readable `message`:

```bash
# All quota/credit exhaustion across every provider
jq -c 'select(.event | startswith("api_quota"))' /var/log/axel/axel.log

# Every transaction that did not complete
jq -c 'select(.event | test("^order_(rejected|submit_failed|submission_ambiguous|cancel_failed)$"))' \
  /var/log/axel/axel.log
```

### Secret redaction

The JSON formatter redacts:

- fields named like credentials (`api_key`, `secret`, `authorization`,
  `bot_token`, `routing_key`, `webhook_url`, `password`, …), recursively;
- credential-shaped values (`Bearer …`, `sk-…`, `APCA-…`, `ghp_…`, …).

Token **counts** (`prompt_tokens`, `completion_tokens`) are operational data and
are never redacted. Redaction is applied to log lines *and* to persisted
operational events.

---

## 2. Event catalogue

### API calls

| Event | Level | Meaning |
|---|---|---|
| `api_call_succeeded` | info | The provider answered successfully. |
| `api_auth_failed` | critical | HTTP 401/403 without a quota signal: key revoked, expired or wrong project. |
| `api_quota_exhausted` | critical | HTTP 402, or 401/403/429 whose body reports quota/credit/usage exhaustion. |
| `api_rate_limited` | warning | HTTP 429 without a quota signal. |
| `api_rejected` | warning | HTTP 4xx that is neither auth, quota, nor rate limiting. |
| `api_server_error` | warning | HTTP 5xx. |
| `api_timeout` | warning | The request timed out; the outcome may be unknown. |
| `api_network_error` | warning | Connection reset/DNS/TLS failure before a response. |
| `llm_token_usage` | info | Token and cost accounting for one completion. |

### Orders and transactions

| Event | Level | Meaning |
|---|---|---|
| `order_created` | info | A durable order intent was recorded. |
| `order_approved` | info | The single-use approval was consumed. |
| `order_submitted` | info | `SUBMITTED` was written to the outbox before the broker call. |
| `order_acknowledged` | info | The broker acknowledged the order. |
| `order_filled` | info | A fill was applied. |
| `order_rejected` | critical | The broker rejected the order (4xx). **The transaction did not go through.** |
| `order_submit_failed` | critical | Transport error before an acknowledgement. |
| `order_submission_ambiguous` | critical | Timeout/ambiguous response. Never blind-retried; reconcile first. |
| `order_cancel_failed` | warning | The broker did not accept a cancellation. |
| `order_unresolved` | warning | An in-flight order is still unknown at the broker. |
| `order_reconciled` / `order_state_synced` | info | Broker state was applied during recovery. |
| `order_duplicate_in_flight` / `order_duplicate` | info/warning | A duplicate submit was refused. |

### Agents and notifications

| Event | Level | Meaning |
|---|---|---|
| `llm_budget_exceeded` | critical | The local hourly/daily spend guard refused a completion. |
| `api_*` for `telegram` / `pagerduty` / `webhook` | warning/critical | Alert delivery failed; the operator may not have been paged. |

---

## 3. Durable health tables

Two tables make health visible to a **separate process** (the read-only
exporter), which cannot read the trading process's memory.

### `api_provider_state` — one row per provider

| Column | Meaning |
|---|---|
| `provider` | `alpaca`, `alpaca-data`, `fred`, `sec`, `news`, `groq`, `telegram`, `pagerduty`, `webhook`, … |
| `last_operation`, `last_outcome`, `last_status_code` | Most recent call. |
| `last_success_at`, `last_failure_at` | Freshness of the last success/failure. |
| `consecutive_failures` | Reset to `0` by any success. |
| `quota_exhausted`, `quota_exhausted_at` | Set by a quota/credit failure; cleared by the next success. |
| `retry_after_seconds`, `rate_limit_remaining` | Provider guidance from response headers. |
| `prompt_tokens_total`, `completion_tokens_total`, `estimated_cost_usd_total` | LLM usage accounting. |

### `operational_events` — append-only failure log

Failures, quota exhaustion, rejected/ambiguous orders, LLM budget exhaustion and
failed alert deliveries are persisted here. Successes stay in the JSON log to
keep the table small. Rows carry `category`, `component`, `event`, `outcome`,
`severity`, `status_code`, redacted `detail` and a JSON `payload`.

Set `PERSIST_OPERATIONAL_EVENTS=false` to disable durable writes (JSON logging
continues). Set `OPERATIONAL_EVENT_RETENTION_DAYS` (default `30`) to control
retention.

Persistence is best-effort: a database error is logged at debug level and
swallowed so observability can never break the trading path.

---

## 4. Metrics and alerts

The exporter turns durable state into metrics:

| Metric | Meaning |
|---|---|
| `axel_api_provider_healthy{provider}` | `1` when no failures and no quota exhaustion. |
| `axel_api_quota_exhausted{provider}` | `1` when the provider reported exhausted quota/credit. |
| `axel_api_consecutive_failures{provider}` | Consecutive failed calls. |
| `axel_api_last_success_age_seconds{provider}` | Seconds since the last success. |
| `axel_api_rate_limit_remaining{provider}` | Provider-reported remaining budget. |
| `axel_api_retry_after_seconds{provider}` | Provider-requested backoff. |
| `axel_api_last_status_code{provider}` | Status of the most recent call. |
| `axel_api_failures_total{provider,outcome,window}` | Failures in the last 24h. |
| `axel_order_failures_total{reason,window}` | Failed order transitions in the last 24h. |
| `axel_operational_failures_total{category,event,outcome,window}` | All persisted operational failures. |
| `axel_llm_tokens_total{provider,kind}` | Prompt/completion tokens consumed. |
| `axel_llm_cost_usd_total{provider}` | Estimated LLM spend. |

Alert rules (`monitoring/prometheus/alerts.yml`):

| Alert | Severity | Trigger |
|---|---|---|
| `AxelApiQuotaExhausted` | critical | `axel_api_quota_exhausted > 0` — immediate page. |
| `AxelApiAuthFailure` | critical | Any `auth_failed` in 10m. |
| `AxelApiRateLimited` | warning | More than 5 rate-limited calls in 10m. |
| `AxelApiProviderStale` | warning | No successful call for over an hour. |
| `AxelApiProviderUnhealthy` | warning | 3+ consecutive failures. |
| `AxelLlmBudgetExceeded` | critical | Local LLM spend guard refused a completion. |
| `AxelOrderRejected` | critical | Broker rejected an order. |
| `AxelOrderSubmissionFailed` | critical | Transport error, ambiguous timeout or failed cancellation. |
| `AxelOperationalEventBurst` | warning | More than 10 operational failures in 10m. |

---

## 5. Troubleshooting

### "My API token is finished"

1. Check the alert: `AxelApiQuotaExhausted` or `AxelApiAuthFailure`.
2. Find the provider:
   ```bash
   curl -fsS http://localhost:9108/metrics | grep axel_api_quota_exhausted
   ```
3. Find the evidence:
   ```bash
   jq -c 'select(.event | test("^api_(quota|auth)"))' /var/log/axel/axel.log
   ```
4. Confirm against the durable state:
   ```sql
   SELECT provider, last_outcome, last_status_code, quota_exhausted,
          quota_exhausted_at, retry_after_seconds
   FROM api_provider_state
   WHERE quota_exhausted OR last_outcome IN ('auth_failed', 'rate_limited');
   ```
5. Rotate/top up the credential in `.env` and restart the affected service. The
   next successful call clears `quota_exhausted` and the failure streak.

For LLM spend, compare `axel_llm_cost_usd_total` with
`LLM_HOURLY_BUDGET_USD` / `LLM_DAILY_BUDGET_USD`. A local budget refusal emits
`llm_budget_exceeded` and raises `AxelLlmBudgetExceeded` even when the provider
itself still has credit.

### "A transaction didn't go through"

1. Check `AxelOrderRejected` / `AxelOrderSubmissionFailed`.
2. Read the event:
   ```bash
   jq -c 'select(.event | test("^order_(rejected|submit_failed|submission_ambiguous|cancel_failed)$"))' \
     /var/log/axel/axel.log
   ```
3. Query the durable record:
   ```sql
   SELECT occurred_at, event, outcome, severity, status_code, detail, payload
   FROM operational_events
   WHERE category = 'order'
   ORDER BY occurred_at DESC
   LIMIT 20;
   ```
4. `order_rejected` means the broker refused it — fix the order, do not retry
   blindly. `order_submission_ambiguous` means the outcome is **unknown**: run
   reconciliation and look the order up by `client_order_id` before any retry.

---

## 6. What is instrumented

| Path | Records |
|---|---|
| `axel/execution/alpaca.py` | Account, positions, orders, fills, cancel, health check, submit. |
| `axel/execution/order_service.py` | Full order lifecycle and every failure branch. |
| `axel/agents/openai_compatible.py` | LLM auth/quota/rate-limit/timeout, token usage, local budget. |
| `axel/comms/telegram.py` | Alert delivery. |
| `axel/observability/alert_relay.py` | PagerDuty, Telegram and webhook delivery. |
| `axel/data/providers/{alpaca,fred,sec,news}.py` | Synchronous market/macro/filing/news fetches. |

Async crypto providers are not yet instrumented; they should follow the same
`record_http_call` / `record_api_error` pattern when they are wired into the
trading core.
