# Monitoring, alerting and paging

Status: **implemented** — Prometheus exporter, Prometheus, Alertmanager, Grafana,
and a PagerDuty/Telegram/webhook relay. Everything runs in Docker Compose and is
read-only with respect to the trading core.

This stack observes AXEL; it does **not** grant live-trading permission, submit
orders, re-arm the kill switch, or replace the independent Watchdog.

---

## 1. What runs

```text
┌────────────────────┐        scrape /metrics        ┌──────────────┐
│  axel-exporter     │◄──────────────────────────────│  Prometheus  │
│  (read-only)       │                               │  :9090       │
│  DB + state files  │                               └──────┬───────┘
└─────────┬──────────┘                                      │ alerts
          │ reads                                           ▼
┌─────────▼──────────┐                               ┌──────────────┐
│ TimescaleDB        │                               │ Alertmanager │
│ killswitch/halt    │                               │  :9093       │
│ state files        │                               └──────┬───────┘
└────────────────────┘                                      │ webhook
                                                            ▼
                                                   ┌──────────────────┐
                                                   │ axel-alert-relay │
                                                   │  :9110           │
                                                   └───┬───────┬──────┘
                                                       │       │
                                              PagerDuty     Telegram /
                                              Events v2     generic webhook

┌──────────────┐
│   Grafana    │──── queries ────► Prometheus
│   :3000      │
└──────────────┘
```

| Component | Image / code | Purpose |
|---|---|---|
| `axel-exporter` | `scripts/run_metrics_exporter.py`, `axel/observability/exporter.py` | Snapshots durable AXEL state and serves Prometheus text format on `:9108`. |
| `prometheus` | `prom/prometheus:v2.55.1` | Scrapes the exporter every 15s and evaluates alert rules. |
| `alertmanager` | `prom/alertmanager:v0.27.0` | Deduplicates/groups alerts and sends them to the relay. |
| `axel-alert-relay` | `scripts/run_alert_relay.py`, `axel/observability/alert_relay.py` | Delivers alerts to PagerDuty, Telegram and/or a generic JSON webhook. |
| `grafana` | `grafana/grafana:11.3.1` | Provisioned Prometheus datasource and the **AXEL — Trading Safety & Operations** dashboard. |

All host ports are bound to `127.0.0.1` by default.

---

## 2. Quick start

1. Add the monitoring values to the repository root `.env` (Compose reads it).
   Start from [`monitoring/.env.example`](../monitoring/.env.example):

   ```dotenv
   PAGERDUTY_INTEGRATION_KEY=...
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_OPERATOR_CHAT_ID=...
   GRAFANA_ADMIN_PASSWORD=change-me
   ```

   Leave `PAGERDUTY_INTEGRATION_KEY` empty to disable paging. Set
   `ALERT_RELAY_DRY_RUN=true` for a first run that logs instead of sending.

2. Start the stack:

   ```bash
   # Database + monitoring only
   docker compose up -d

   # Database + monitoring + paper soak + static dashboard
   docker compose --profile paper up -d
   ```

3. Open:

   | Service | URL |
   |---|---|
   | Grafana | http://localhost:3000 |
   | Prometheus | http://localhost:9090 |
   | Alertmanager | http://localhost:9093 |
   | Exporter metrics | http://localhost:9108/metrics |
   | Exporter readiness | http://localhost:9108/readyz |

4. Verify:

   ```bash
   curl -fsS http://localhost:9108/healthz
   curl -fsS http://localhost:9108/metrics | head
   curl -fsS http://localhost:9090/-/ready
   curl -fsS http://localhost:9093/-/ready
   ```

---

## 3. Exported metrics

The exporter is deliberately small. It exposes state that already exists in the
database or in the kill-switch/halt state files.

| Metric | Meaning |
|---|---|
| `axel_equity_usd`, `axel_cash_usd` | Broker account equity and cash. |
| `axel_high_water_mark_usd`, `axel_drawdown_pct` | Drawdown against the persisted high-water mark. |
| `axel_gross_exposure_usd`, `axel_net_exposure_usd` | Position exposure. |
| `axel_open_positions_total` | Open positions. |
| `axel_kill_switch_active` | `1` when the global kill switch is tripped. |
| `axel_halt_active`, `axel_halt_scope` | Scoped halt state and scope label. |
| `axel_pending_hitl_tickets` | Undecided durable approval records. |
| `axel_proposals_total{window="1h\|24h"}` | Proposal counts by window. |
| `axel_proposals_approved_total`, `axel_proposals_rejected_total` | Decision counts over 24h. |
| `axel_live_enabled` | `1` only when all live locks are satisfied. |
| `axel_open_incidents_total`, `axel_critical_incidents_total` | Open incidents from the `incidents` table. |
| `axel_unreconciled_orders_total` | Orders not in a terminal/reconciled state. |
| `axel_last_reconciliation_age_seconds` | Age of the latest completed reconciliation run. |
| `axel_reconciliation_mismatches_total` | Mismatches from the latest reconciliation run. |
| `axel_api_provider_healthy{provider}` | `1` when the provider has no consecutive failures and no quota exhaustion. |
| `axel_api_quota_exhausted{provider}` | `1` when the provider reported exhausted quota/credit. |
| `axel_api_consecutive_failures{provider}` | Consecutive failed calls for the provider. |
| `axel_api_last_success_age_seconds{provider}` | Seconds since the provider last answered successfully. |
| `axel_api_rate_limit_remaining{provider}` | Provider-reported remaining request budget. |
| `axel_api_retry_after_seconds{provider}` | Provider-requested backoff after a rate limit. |
| `axel_api_last_status_code{provider}` | HTTP status of the provider's most recent call. |
| `axel_api_failures_total{provider,outcome,window}` | Failed API calls by provider and outcome. |
| `axel_order_failures_total{reason,window}` | Failed order transitions by reason. |
| `axel_operational_failures_total{category,event,outcome,window}` | All persisted operational failures. |
| `axel_llm_tokens_total{provider,kind}` | LLM tokens consumed (prompt/completion). |
| `axel_llm_cost_usd_total{provider}` | Estimated LLM spend in USD. |
| `axel_exporter_up` | `1` while the exporter process is serving. |
| `axel_exporter_last_scrape_success` | `0` when the last state collection failed. |
| `axel_exporter_scrape_duration_seconds` | Collection latency. |
| `axel_environment_info{environment="paper\|live"}` | Active environment. |

### Failure semantics

- If the database is unreachable, `/metrics` still returns HTTP 200 with
  `axel_exporter_last_scrape_success 0` and `axel_exporter_error 1`. The alert
  `AxelScrapeFailed` fires; the exporter does not silently disappear.
- If the exporter process is down, Prometheus sets `up{job="axel"} = 0` and
  `AxelExporterDown` fires.
- `/readyz` returns `503` when the database cannot answer `SELECT 1`.

---

## 4. Alert rules

Rules live in [`monitoring/prometheus/alerts.yml`](../monitoring/prometheus/alerts.yml).

| Alert | Severity | Trigger |
|---|---|---|
| `AxelExporterDown` | critical | Prometheus cannot scrape the exporter for 2m. |
| `AxelScrapeFailed` | critical | Exporter cannot read durable state for 2m. |
| `AxelKillSwitchActive` | critical | Global kill switch is tripped. |
| `AxelHaltActive` | critical | Any scoped halt is active. |
| `AxelDrawdownWarning` | warning | Drawdown ≥ 8% for 5m. |
| `AxelDrawdownCritical` | critical | Drawdown ≥ 10%. |
| `AxelCriticalIncidentOpen` | critical | Any open critical incident. |
| `AxelIncidentOpen` | warning | Any incident open for 15m. |
| `AxelReconciliationMismatch` | critical | Latest reconciliation has mismatches. |
| `AxelUnreconciledOrders` | warning | Open orders not reconciled for 10m. |
| `AxelReconciliationStale` | warning | No reconciliation completed for 15m. |
| `AxelPendingApproval` | warning | An approval has waited 15m. |
| `AxelEquityUnavailable` | warning | Equity ≤ 0 for 5m. |
| `AxelLiveTradingEnabled` | warning | Live trading is enabled. |
| `AxelApiQuotaExhausted` | critical | Any provider reports exhausted quota/credit. |
| `AxelApiAuthFailure` | critical | Any `auth_failed` call in 10m. |
| `AxelApiRateLimited` | warning | More than 5 rate-limited calls in 10m. |
| `AxelApiProviderStale` | warning | No successful call to a provider for over an hour. |
| `AxelApiProviderUnhealthy` | warning | 3+ consecutive provider failures. |
| `AxelLlmBudgetExceeded` | critical | The local LLM spend guard refused a completion. |
| `AxelOrderRejected` | critical | The broker rejected an order. |
| `AxelOrderSubmissionFailed` | critical | Transport error, ambiguous timeout or failed cancellation. |
| `AxelOperationalEventBurst` | warning | More than 10 operational failures in 10m. |

Critical and warning alerts are grouped by `alertname`, `severity` and
`component`. A critical alert inhibits the matching warning with the same name.

---

## 5. Pager and notification setup

Alertmanager sends every alert to the relay at
`http://axel-alert-relay:9110/alert`. The relay then fans out to whichever
channels are configured.

### PagerDuty

1. Create a PagerDuty service using the **Events API v2** integration.
2. Copy its integration key into `PAGERDUTY_INTEGRATION_KEY` in `.env`.
3. Restart the relay: `docker compose up -d axel-alert-relay`.

The relay sends `trigger` for firing alerts and `resolve` for resolved alerts,
using the Alertmanager fingerprint as the PagerDuty `dedup_key`.

### Telegram

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OPERATOR_CHAT_ID`. Critical alerts are
sent with notifications enabled; warnings are silent.

### Generic webhook

Set `ALERT_WEBHOOK_URL` to receive the original Alertmanager JSON payload. This
is the integration point for Slack relays, Opsgenie, incident.io, or an internal
incident service.

### Test delivery

```bash
curl -fsS -X POST http://localhost:9110/alert \
  -H 'Content-Type: application/json' \
  -d '{
    "version": "4",
    "status": "firing",
    "alerts": [{
      "status": "firing",
      "labels": {"alertname": "AxelTestPage", "severity": "critical"},
      "annotations": {"summary": "Synthetic AXEL pager test"},
      "fingerprint": "test-page-1"
    }]
  }'
```

Then resolve it with the same payload and `"status": "resolved"`.

Use `ALERT_RELAY_DRY_RUN=true` to verify routing without contacting PagerDuty or
Telegram.

---

## 6. Security and operations

- **Localhost only:** Compose publishes Prometheus, Alertmanager, Grafana and the
  exporter on `127.0.0.1`. Do not expose them directly to the internet.
- **Grafana:** anonymous access is disabled and sign-up is disabled. Change
  `GRAFANA_ADMIN_PASSWORD` before exposing the port.
- **Secrets:** PagerDuty/Telegram credentials live in `.env`, which is ignored by
  git. They are not passed to the agent service.
- **Read-only exporter:** the exporter opens a session per scrape and only runs
  `SELECT`s. It never calls the risk engine, broker submit path, or approval
  service.
- **State sharing:** the exporter reads the kill-switch and halt files from the
  `axel_state` volume. Any process that writes those files must mount the same
  volume and use `KILLSWITCH_STATE_FILE` / `HALT_STATE_FILE`.
- **No automatic recovery:** an alert never clears a halt or re-arms the kill
  switch. Recovery remains an explicit operator action.

### Limitations

- The exporter is a snapshot process. It cannot see in-memory `ApprovalService`
  or `IncidentStore` objects owned by another process; it reads the durable
  `approvals` and `incidents` tables instead. If a producer has not persisted an
  event, the metric will not see it.
- Reconciliation age and mismatch count come from the latest
  `reconciliation_runs` row. A missing row is reported as absent, not as zero.
- Prometheus and Alertmanager have no authentication in this Compose setup. Keep
  them on localhost or put them behind an authenticated reverse proxy.
- Grafana dashboards are provisioned read-only. Edit the JSON in
  `monitoring/grafana/dashboards/` and restart Grafana to change them.
- API quota/auth/rate-limit and failed-transaction events are described in
  [`logging-and-api-health.md`](logging-and-api-health.md). They come from the
  `api_provider_state` and `operational_events` tables, which are written
  best-effort by the trading core.

---

## 7. Files

```text
axel/core/logging.py          # JSON formatter, redaction, rotating file sink
axel/observability/
├── exporter.py          # read-only metrics server
├── api_health.py        # outcome classification + durable provider/event state
└── alert_relay.py       # Alertmanager -> PagerDuty/Telegram/webhook
scripts/
├── run_metrics_exporter.py
└── run_alert_relay.py
monitoring/
├── .env.example
├── prometheus/
│   ├── prometheus.yml
│   └── alerts.yml
├── alertmanager/
│   └── alertmanager.yml
└── grafana/
    ├── provisioning/
    │   ├── datasources/prometheus.yml
    │   └── dashboards/axel.yml
    └── dashboards/axel-overview.json
```
