# AXEL production hosting and live-dashboard runbook

This document describes how to run AXEL online with the terminal UI fed by
real, durable records instead of the browser simulation.

The UI in `axel/dashboard/ui/` is visually unchanged. It is event-bus driven
(`sim/state.js`), and `js/live/adapter.js` now feeds that same bus from the
read-only API. The simulation remains available only as an explicit fallback.

## 1. Target process layout

```text
                    ┌──────────────────────────────┐
  browser ──TLS──►  │ Caddy                        │
                    │  /            → api:8000     │
                    │  /api/*       → api:8000     │
                    │  SSE flush    unbuffered     │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │ api (FastAPI + uvicorn)      │
                    │  static UI + read-only API   │
                    │  bearer token · SSE          │
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
 │ ingest-worker│          │ agent-worker  │      │ core-worker   │
 │ market data  │          │ signals +     │      │ risk gate +   │
 │ no LLM       │          │ proposals     │      │ paper fills   │
 └──────────────┘          └───────────────┘      └───────┬───────┘
                                                          │
                                                  ┌───────▼───────┐
                                                  │ watchdog      │
                                                  │ independent   │
                                                  └───────────────┘
```

Rules:

- `core` and `watchdog` must never import LLM libraries. CI enforces this.
- `agent-worker` writes only `signals`, `panel_decisions`, `trade_proposals`
  and `agent_runs`. It never holds broker credentials and cannot execute.
- `core-worker` is the only process allowed to evaluate risk, submit orders and
  write fills/positions/equity snapshots.
- The dashboard API is read-only. Approval and chat actions are a separate
  authenticated service.

## 2. What is live, and what is simulated

The header shows a `FEED` chip:

| Chip | Meaning |
|---|---|
| `LIVE` | Snapshot loaded from `/api/v1/dashboard/snapshot` and SSE is connected |
| `LIVE · RETRY` | A live snapshot loaded, but the stream disconnected; EventSource is reconnecting |
| `SIM` | The server feed was unreachable, or the page was opened with `?sim=1` |

When the API cannot be reached on first load, `main.js` falls back to
`sim/scheduler.js` and the chip stays `SIM`. The adapter never silently mixes
simulated events into a live session. Once a live snapshot has loaded, stream
errors show `LIVE · RETRY` instead of fabricating data.

The F1 kill drill and F4 resume are blocked while `window.AXEL_LIVE` is true:
a browser action must never fake a backend kill-switch event or resume trading.
Use the operator runbook and the authenticated service for those actions.

Live event mapping:

| UI event | Durable source |
|---|---|
| `equity` | `equity_snapshots` |
| `sections` | section NAV, daily P&L, positions and correlation |
| `portfolio` | `positions` + closed-trade audit records |
| `orders` | `orders` + `fills` |
| `signals` | `signals` |
| `risk:start` / `risk:check` / `risk:done` | `trade_proposals` + `risk_decisions` |
| `alerts` | `incidents`, kill-switch events, alert audit rows |
| `log` | append-only `audit_log` |
| `ops` | ingest freshness, `agent_runs` LLM spend, provider state |

API surface:

```text
GET /api/v1/healthz
GET /api/v1/dashboard/snapshot
GET /api/v1/dashboard/stream      (SSE; snapshot first, then deltas)
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

## 4. First boot on a single VPS

Prerequisites: Ubuntu LTS, Docker Engine with the Compose plugin, a DNS record
for the dashboard hostname, and ports 80/443 open.

```bash
# 1. Get the code
sudo mkdir -p /opt/axel && sudo chown "$USER" /opt/axel
git clone <repo-url> /opt/axel
cd /opt/axel

# 2. Configure secrets. Never commit .env.
cp .env.example .env
chmod 600 .env
$EDITOR .env

# 3. Start the stack. `migrate` creates the schema, then the API and
#    workers start; Caddy obtains a certificate automatically.
docker compose up -d --build

# 4. Verify
docker compose ps
curl -fsS http://127.0.0.1:8000/api/v1/healthz
docker compose logs -f api ingest-worker agent-worker core-worker
```

Open `https://<your-domain>` and confirm the header shows `LIVE`. The first
ingest cycle writes 30 bars plus periodic macro rows; proposals appear after
enough history exists (about 8 cycles), and the risk gate evaluates them on the
next core cycle.

Set these values in `.env` for the edge and API:

```dotenv
# Caddy. Use a real hostname for automatic HTTPS; :80 is HTTP-only.
AXEL_SITE_ADDRESS=dashboard.example.com

# Optional bearer token. EventSource cannot set headers, so the browser also
# accepts ?token=<value>; keep it out of access logs or use a same-site cookie
# proxy when you add real user auth.
DASHBOARD_API_TOKEN=
DASHBOARD_CORS_ORIGINS=*

# Paper runtime cadence and deterministic feed seed.
PAPER_RUNTIME_INTERVAL_SECONDS=5
PAPER_RUNTIME_SEED=7

# Database credentials used by Compose.
POSTGRES_USER=axel
POSTGRES_PASSWORD=change-me
POSTGRES_DB=axel
```

Schema note: `migrations/versions/` is currently empty, so the `migrate`
service calls `init_db()` to create tables from SQLAlchemy metadata. Generate
the initial Alembic revision before relying on versioned migrations, then change
the `migrate` command to `alembic upgrade head`. Do not run `init_db()` against a
database that already has a migration history.

## 5. Environment reference

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./axel.db` | SQLAlchemy URL; Compose injects PostgreSQL |
| `ENVIRONMENT` | `paper` | `live` also requires `LIVE_TRADING_CONFIRMED=true` |
| `LIVE_TRADING_CONFIRMED` | `false` | Double safety lock; keep false for paper |
| `ALPACA_PAPER` | `true` | Must stay true in paper mode |
| `DASHBOARD_API_TOKEN` | unset | Optional bearer token for all dashboard routes |
| `DASHBOARD_CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `DASHBOARD_POLL_SECONDS` | `1.0` | SSE database poll interval |
| `DASHBOARD_STREAM_HEARTBEAT_SECONDS` | `5.0` | SSE keepalive interval |
| `PAPER_RUNTIME_INTERVAL_SECONDS` | `5.0` | Worker cycle and synthetic bar interval |
| `PAPER_RUNTIME_SEED` | `7` | Deterministic feed seed |
| `KILLSWITCH_STATE_FILE` | `.axel_killswitch.json` | Shared kill-switch state file |
| `HALT_STATE_FILE` | `.axel_halt.json` | Halt state read by the watchdog |
| `LOG_FILE` | empty | Empty keeps stdout-only logs for Docker |

## 6. Keeping it running

Minimum production checklist:

- `restart: unless-stopped` on every long-running service (already set in
  `docker-compose.yml`).
- Healthchecks for `api`, Postgres, Redis and the monitoring services.
- Caddy or nginx as the only public entry point; bind Postgres, Redis and the
  API to localhost or the Docker network only.
- The systemd unit in `deploy/axel.service` brings the stack up on boot:

```bash
sudo cp deploy/axel.service /etc/systemd/system/axel.service
sudo systemctl daemon-reload
sudo systemctl enable --now axel.service
sudo systemctl status axel.service
```

- Docker log rotation (`max-size`, `max-file`) and JSON logs to stdout.
- Prometheus + Grafana + Alertmanager + the existing alert relay.
- External uptime probe on `/api/v1/healthz` and an alert on `halted=true` or a
  new kill-switch event.
- Nightly `pg_dump` to object storage, plus weekly restore verification.
- Deploy through CI: build image → push to GHCR → `docker compose pull` →
  `docker compose up -d`; keep the previous image tag for rollback.

Operational checks:

```bash
# Read-only snapshot (works with or without a token)
curl -fsS http://127.0.0.1:8000/api/v1/dashboard/snapshot | jq '.counters, .equity'
# SSE stream: first event must be `snapshot`
curl -N http://127.0.0.1:8000/api/v1/dashboard/stream | head -n 4
# Worker health and recent durable activity
docker compose logs --tail=100 ingest-worker agent-worker core-worker
```

Resource guidance: the API is I/O-light (one poll per connected client). The
paper workers are CPU-light. A 2 vCPU / 4 GB VPS is enough for a single
operator; use 4 vCPU / 8 GB when running the monitoring stack and Postgres on
the same host.

## 7. Security

- TLS everywhere; HSTS; firewall only 22/80/443.
- Dashboard auth (JWT or OIDC) and per-route authorization before exposing it
  beyond a trusted network.
- Rate limiting at the proxy and in the API.
- Secrets in `.env` with `600` permissions or Docker secrets; never in Git.
- Broker keys are trade-only with withdrawals disabled.
- Separate database roles: `axel_app` (read/write core), `axel_dashboard`
  (read-only), `axel_watchdog` (independent).
- The dashboard API must never expose broker credentials or mutation endpoints
  without an authenticated approval flow.
- The API is read-only by construction: it only runs `SELECT` projections and
  never imports the execution or agent modules.

## 8. Local development

Run the API and the paper runtime together; the API serves the UI at the same
origin, so no static server is required:

```bash
python scripts/run_dashboard_api.py --init-db --port 8000
python scripts/run_paper_runtime.py --cycles 0 --interval 2
```

Then open `http://127.0.0.1:8000/`. For UI-only work with the static server:

```bash
python -m http.server 8080 --directory axel/dashboard/ui
```

The adapter maps port `8080`, `3000` and `5173` to the API on `:8000`
automatically. Override with `?api=http://host:port/api/v1`, and force the
simulation with `?sim=1`.

Useful worker invocations:

```bash
python scripts/run_ingest_worker.py --init-db --once
python scripts/run_agent_worker.py --once
python scripts/run_core_worker.py --once
python scripts/run_paper_runtime.py --cycles 20 --interval 1
```

## 9. Scaling path

1. Single VPS + Compose + Caddy (this document).
2. Move Postgres to a managed service; keep workers and API on the VPS.
3. Split ingest, agent and core workers onto separate hosts. The database is
   the only shared state, so no cross-process event bus is required.
4. Add a read replica for the dashboard API and a queue for chat/approvals.
5. Only then consider Kubernetes.
