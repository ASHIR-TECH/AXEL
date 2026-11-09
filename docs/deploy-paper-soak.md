# Deploy AXEL for Phase 5 paper testing

This deploys a **paper-only** worker, TimescaleDB, Redis, and the read-only dashboard. It is suitable for a small Linux VPS or another Docker host. It does not create a live-trading deployment.

## Before deployment

1. Install Docker Engine and the Docker Compose plugin on the host.
2. Copy the repository to the host without copying any local database files.
3. Create `.env` from `.env.example`; do not commit it.
4. Set secure database and operator secrets, then confirm the mandatory paper locks:

   ```dotenv
   POSTGRES_PASSWORD=use-a-long-unique-password
   ENVIRONMENT=paper
   ALPACA_PAPER=true
   LIVE_TRADING_CONFIRMED=false
   ```

5. Add only paper Alpaca credentials. If using an agent, add either Qwen or Groq configuration, never both as the selected provider.
6. Set the soak cadence. The default 5-second interval writes roughly a million
   rows per day; 60 seconds is enough for a paper soak and cuts market-data
   writes by 12×:

   ```dotenv
   PAPER_RUNTIME_INTERVAL_SECONDS=60
   ```

## Launch

```bash
docker compose --profile paper up -d --build
docker compose --profile paper ps
docker compose logs -f api ingest-worker agent-worker core-worker
```

The terminal is served by the read-only API at `http://YOUR_SERVER_IP:8000/`
and, in a normal deployment, through Caddy at `https://YOUR_DOMAIN/`. Caddy is
the only public entry point; keep the API bound to localhost or the Docker
network. The dashboard has no user authentication by default, so keep it behind
a VPN, reverse proxy, or firewall, or set `DASHBOARD_API_TOKEN`.

## Verify

```bash
curl -fsS http://127.0.0.1:8000/api/v1/healthz
curl -fsS http://127.0.0.1:8000/api/v1/dashboard/snapshot | jq '.counters, .equity'
docker compose --profile paper exec paper-soak python scripts/run_paper_soak.py --once
docker compose --profile paper logs --tail=100 paper-soak api ingest-worker agent-worker core-worker
docker compose --profile paper ps
```

The workers refuse to start if any runtime setting is not paper-only. Every
runtime worker records a heartbeat each cycle, so continuity is provable rather
than assumed. Docker restarts a failed worker automatically. The dashboard
header must show `LIVE` when the API feed is connected.

```bash
# Coverage against the 56-day gate (least-continuous worker wins)
docker compose exec core-worker python scripts/soak_status.py --minimum-days 56
```

## Operations

- Check dashboard and worker logs daily.
- Run the local quality gate before each deployment: `make check`.
- Run the kill-switch drill at least once every 30 days.
- Backup the TimescaleDB volume before upgrades.
- Never expose the database port (`5432`) or Redis port (`6379`) publicly. In production, remove their host `ports:` mappings or firewall them to your private network.
- Run retention nightly: `docker compose exec core-worker python scripts/prune_operational_data.py`.
- Check soak coverage weekly: `scripts/soak_status.py --minimum-days 56`.
- Generate the initial Alembic revision before starting the soak. Until then,
  the `migrate` service uses `init_db()` and later index changes will not reach
  an existing database.

## Current scope

This starts the persistent paper-soak process, the deterministic ingest, agent
and core workers, the read-only API, and the live terminal. The workers write
durable signals, proposals, risk decisions, orders, fills, positions and equity
snapshots, so the dashboard counters and charts are backed by real records.
They are paper-only and require no LLM or broker credentials. Do not treat a
healthy worker or a positive paper P&L as evidence to enable live trading.
