"""
Read-only HTTP API for the live AXEL terminal.

Endpoints
---------
``GET /api/v1/healthz``
    Liveness plus a cheap database round-trip.
``GET /api/v1/dashboard/snapshot``
    Full dashboard state built from durable rows.
``GET /api/v1/dashboard/stream``
    Server-Sent Events stream of the same state's deltas.  SSE is sufficient
    for a read-only dashboard; WebSocket is reserved for chat and approvals.

The API never writes to the database.  Approval/chat actions belong in a
separate authenticated service.  If ``DASHBOARD_API_TOKEN`` is configured,
every dashboard route requires a bearer token (or ``?token=`` for
``EventSource``, which cannot set custom headers).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from axel.core.config import settings
from axel.dashboard.projector import DashboardStreamState, build_snapshot
from axel.db.session import SessionLocal

UI_DIR = Path(__file__).resolve().parent / "ui"

app = FastAPI(
    title="AXEL Dashboard API",
    version="1.0.0",
    description="Read-only live terminal feed for AXEL.",
)

_origins = [item.strip() for item in settings.dashboard_cors_origins.split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Auth ──────────────────────────────────────────────────────────────────────


def _expected_token() -> str | None:
    if settings.dashboard_api_token is None:
        return None
    value = settings.dashboard_api_token.get_secret_value()
    return value or None


def require_token(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Enforce a bearer token when one is configured; no-op otherwise."""
    expected = _expected_token()
    if expected is None:
        return
    provided = token
    if provided is None and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            provided = value.strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing dashboard token")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _snapshot_sync() -> dict[str, Any]:
    with SessionLocal() as session:
        return build_snapshot(session)


def _event(name: str, payload: Any) -> str:
    body = json.dumps(payload, separators=(",", ":"), default=str)
    return f"event: {name}\ndata: {body}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/api/v1/healthz")
def healthz() -> dict[str, Any]:
    """Liveness and database readiness; safe for an external uptime probe."""
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exercised by deployment probes
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    return {"status": "ok", "database": "ok", "mode": settings.environment}


@app.get("/api/v1/dashboard/snapshot", dependencies=[Depends(require_token)])
def dashboard_snapshot() -> dict[str, Any]:
    """Full dashboard state: counters, equity, sections, orders, signals, logs."""
    return _snapshot_sync()


@app.get("/api/v1/dashboard/stream", dependencies=[Depends(require_token)])
async def dashboard_stream() -> StreamingResponse:
    """SSE stream of dashboard deltas.

    Each connection keeps its own cursor.  The database is the source of
    truth, so the API can run in a separate container from the workers.
    """
    interval = max(0.25, float(settings.dashboard_poll_seconds))
    heartbeat = max(1.0, float(settings.dashboard_stream_heartbeat_seconds))

    async def events() -> AsyncIterator[str]:
        snapshot = await run_in_threadpool(_snapshot_sync)
        state = DashboardStreamState(snapshot)
        yield _event("snapshot", snapshot)
        yield ": connected\n\n"

        elapsed = 0.0
        try:
            while True:
                await asyncio.sleep(interval)
                elapsed += interval
                current = await run_in_threadpool(_snapshot_sync)
                for name, payload in state.poll(current):
                    yield _event(name, payload)
                if elapsed >= heartbeat:
                    elapsed = 0.0
                    yield _event("watchdog", {"at": current.get("generated_at")})
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:  # client disconnected
            return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Serve the unchanged terminal UI from the same origin as the API.  The mount
# is registered last so the /api routes above always win.
if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")


__all__ = ["app"]
