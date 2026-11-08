"""Tests for the read-only FastAPI dashboard endpoints."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import axel.db.models
import axel.portfolio.db_models  # noqa: F401 — register tables
from axel.core.config import settings
from axel.dashboard import api as api_module
from axel.db.base import Base
from axel.db.models import EquitySnapshotModel


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(api_module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "dashboard_api_token", None)
    return TestClient(api_module.app), factory


def test_healthz_reports_database_readiness(client):
    http, _factory = client
    response = http.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_snapshot_is_read_only_and_terminal_shaped(client):
    http, factory = client
    with factory() as session:
        session.add(
            EquitySnapshotModel(
                id="eq_1",
                timestamp=datetime.now(UTC),
                section="global",
                nav=10_250.0,
                cash=10_000.0,
                gross_exposure=250.0,
                net_exposure=250.0,
                realized_pnl=0.0,
                unrealized_pnl=250.0,
                drawdown_pct=0.0,
                high_water_mark=10_250.0,
            )
        )
        session.commit()

    response = http.get("/api/v1/dashboard/snapshot")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "server"
    assert payload["equity"]["equity"] == 10_250.0
    assert set(payload["sections"]) == {
        "STOCKS",
        "OPTIONS",
        "CRYPTO",
        "FOREX",
        "PREDICTIONS",
    }
    assert payload["portfolio"]["holdings"] == []
    assert "logs" in payload
    assert "counters" in payload


def test_token_is_required_when_configured(client, monkeypatch):
    http, _factory = client
    monkeypatch.setattr(settings, "dashboard_api_token", SecretStr("s3cret"))

    assert http.get("/api/v1/dashboard/snapshot").status_code == 401
    assert (
        http.get(
            "/api/v1/dashboard/snapshot", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert (
        http.get(
            "/api/v1/dashboard/snapshot", headers={"Authorization": "Bearer s3cret"}
        ).status_code
        == 200
    )
    # EventSource cannot set headers, so the token may travel in the query.
    assert http.get("/api/v1/dashboard/snapshot?token=s3cret").status_code == 200


def test_stream_starts_with_a_snapshot_event(client):
    http, _factory = client
    with http.stream("GET", "/api/v1/dashboard/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        assert next(lines) == "event: snapshot"
        data = next(lines)
        assert data.startswith("data: ")
        payload = json.loads(data.removeprefix("data: "))
        assert payload["equity"]["equity"] == 10_000.0
