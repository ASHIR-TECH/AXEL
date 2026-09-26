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

## Launch

```bash
docker compose --profile paper up -d --build
docker compose --profile paper ps
docker compose --profile paper logs -f paper-soak
```

The dashboard is served at `http://YOUR_SERVER_IP:8080` by default. Set `AXEL_DASHBOARD_PORT` in `.env` to change it. Keep it behind a VPN, reverse proxy, or firewall; the static dashboard has no authentication.

## Verify

```bash
docker compose --profile paper exec paper-soak python scripts/run_paper_soak.py --once
docker compose --profile paper logs --tail=100 paper-soak
docker compose --profile paper ps
```

The worker refuses to start if any runtime setting is not paper-only. It writes a heartbeat every minute; Docker restarts it after an unexpected failure.

## Operations

- Check dashboard and worker logs daily.
- Run the local quality gate before each deployment: `make check`.
- Run the kill-switch drill at least once every 30 days.
- Backup the TimescaleDB volume before upgrades.
- Never expose the database port (`5432`) or Redis port (`6379`) publicly. In production, remove their host `ports:` mappings or firewall them to your private network.

## Current scope

This starts the persistent paper-soak process and dashboard. The next worker integration attaches validated agent proposals, paper-order placement, fill collection, reconciliation, and Phase 5 observation recording to this process. Do not treat a healthy worker as evidence to enable live trading.
