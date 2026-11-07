# AXEL production hosting and live-dashboard runbook

This document describes how to run AXEL online with the existing terminal UI
fed by real, durable records instead of the browser simulation.

The UI in `axel/dashboard/ui/` is intentionally unchanged visually. It is
already event-bus driven (`sim/state.js`), so a live adapter can feed the same
events from an API without redesigning the terminal.

## 1. Target process layout

```text
                    ┌──────────────────────────────┐
  browser ──TLS──►  │ Caddy / nginx                │
                    │  /            static UI      │
                    │  /api/*       proxy → api    │
                    │  /stream      SSE            │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │ api (FastAPI + uvicorn)      │
                    │  read-only dashboard queries │
                    │  auth · rate limit · SSE     │
                    └──────┬───────────────┬───────┘
                           │               │
                    ┌──────▼─────┐   ┌─────▼──────┐
                    │ PostgreSQL │   │ Redis      │
                    │ Timescale  │   │ cache/queue│
                    └──────▲─────┘   └─────▲──────┘
                           │               │
        ┌──────────────────┴───────┬───────┴──────────────┐
        │                          │                      │
 ┌──────▼───────┐          ┌───────▼───────┐      ┌───────▼───────┐
 │ ingest-worker│          │ agent-worker  │      │ core          │
 │ market data  │          │ LLM proposals │      │ risk/execution│
 │ no LLM       │          │ no broker keys│      │ no LLM        │
 └──────────────┘          └───────────────┘      └───────┬───────┘
                                                          │
                                                  ┌───────▼───────┐
                                                  │ watchdog      │
                                                  │ independent   │
                                                  └───────────────┘
```

Rules:

- `core` and `watchdog` must never import LLM libraries. CI already enforces
  this.
- `agent-worker` writes only `signals`, `panel_decisions`, `trade_proposals`
  and `agent_runs`. It never holds broker credentials.
- `core` reads typed rows and is the only process allowed to submit orders.
- The dashboard API is read-only. Approval actions are a separate authenticated
  service.

## 2. What makes the counters and charts real

The browser simulation must be replaced by a live adapter with the same event
contract:

| UI event | Real source |
|---|---|
| `equity` | `equity_snapshots` (time series) |
| `sections` | section NAV / daily P&L projection from snapshots and positions |
| `portfolio` | `positions`, `fills`, `equity_snapshots` |
| `orders` | `orders` + `order_events` |
| `signals` | `signals` |
| `risk:start` / `risk:check` / `risk:done` | `trade_proposals` + `risk_decisions` |
| `alerts` | `operational_events`, `incidents`, kill-switch events |
| `log` | append-only `audit_log` / operational events |
| `ops` | `api_provider_state`, ingest freshness, LLM budget ledger |

The API should expose a snapshot plus a stream:

```text
GET  /api/v1/dashboard/snapshot
GET  /api/v1/dashboard/stream      (SSE; one event per change or 1s tick)
GET  /api/v1/healthz
```

SSE is sufficient for a read-only dashboard. Use WebSocket only for chat and
human approvals.

## 3. Hosting options

| Option | Best for | Notes |
|---|---|---|
| Single VPS + Docker Compose | first production, low cost | 4 vCPU / 8 GB is enough for the API and workers; simplest operations |
| VPS + managed Postgres | reliability without DBA work | keep Redis and workers on the VPS |
| Fly.io / Render / Railway | fast deploys | check persistent volumes, private networking and long-running workers |
| Kubernetes | many sections / HA | only after the single-VPS path is stable |

Recommended first deployment: **Hetzner CX22/CPX21 or DigitalOcean 4 vCPU/8 GB,
Ubuntu LTS, Docker Compose, Caddy for TLS**.

## 4. Keeping it running

Minimum production checklist:

- `restart: unless-stopped` on every service.
- Healthchecks for `api`, `core`, `agent-worker`, `ingest-worker`, Postgres and
  Redis.
- Caddy or nginx as the only public entry point; bind Postgres and Redis to
  localhost or the Docker network only.
- A systemd unit that runs `docker compose up -d` on boot.
- Docker log rotation (`max-size`, `max-file`) and JSON logs to stdout.
- Prometheus + Grafana + Alertmanager + the existing alert relay.
- External uptime probe on `/api/v1/healthz` and the kill-switch state.
- Nightly `pg_dump` to object storage, plus weekly restore verification.
- Alembic migrations run as a one-shot job before the API/worker starts.
- Deploy through CI: build image → push to GHCR → `docker compose pull` →
  `docker compose up -d`; keep the previous image tag for rollback.

## 5. Security

- TLS everywhere; HSTS; firewall only 22/80/443.
- Dashboard auth (JWT or OIDC) and per-route authorization.
- Rate limiting at the proxy and in the API.
- Secrets in `.env` with `600` permissions or Docker secrets; never in Git.
- Broker keys are trade-only with withdrawals disabled.
- Separate database roles: `axel_app` (read/write core), `axel_dashboard`
  (read-only), `axel_watchdog` (independent).
- The dashboard API must never expose broker credentials or mutation endpoints
  without an authenticated approval flow.

## 6. Frontend integration sequence

1. Persist real activity: `ingest-worker` + `agent-worker` + `core` write the
   rows above.
2. Freeze the snapshot schema and expose `/api/v1/dashboard/snapshot` and
   `/stream`.
3. Add `js/live/` adapter that maps snapshot/stream payloads onto the existing
   `emit()` events. Keep `js/sim/` as a demo fallback.
4. Add auth and approval actions only after the read-only dashboard is stable.

Until step 1 exists, the UI will look alive but it is showing simulated data.
That distinction must be visible in the UI (a `SIM` / `LIVE` badge) so a demo is
never mistaken for a real track record.
