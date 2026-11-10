"""Tests for the real Alpaca market-data feed used by the paper runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import axel.db.models  # noqa: F401 — register tables
from axel.core.config import settings
from axel.db.base import Base
from axel.db.models import OhlcvBar
from axel.runtime.alpaca_feed import (
    STOCK_SYMBOLS,
    _to_synthetic,
    ingest_alpaca_once,
)
from axel.runtime.ingest import ingest_once
from axel.runtime.sections import trading_sections


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _record(symbol: str, when: datetime, close: float = 100.0) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        event_time=when,
        open=close - 0.5,
        high=close + 0.5,
        low=close - 1.0,
        close=close,
        volume=12_345.0,
    )


def _fake_market_data(when: datetime) -> SimpleNamespace:
    def fetch_bars(symbol: str, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(records=[_record(symbol, when)])

    return SimpleNamespace(fetch_bars=fetch_bars)


def test_to_synthetic_normalizes_naive_timestamps() -> None:
    bars = _to_synthetic([_record("aapl", datetime(2026, 11, 10, 21, 0), 190.5)])
    assert len(bars) == 1
    bar = bars[0]
    assert bar.symbol == "AAPL"
    assert bar.timestamp.tzinfo == UTC
    assert bar.close == 190.5


def test_ingest_alpaca_once_persists_and_is_idempotent(session_factory) -> None:
    now = datetime(2026, 11, 10, 21, 0, tzinfo=UTC)
    market_data = _fake_market_data(now)

    with session_factory() as session:
        result = ingest_alpaca_once(session, now=now, market_data=market_data)
        session.commit()
        count = session.execute(select(func.count()).select_from(OhlcvBar)).scalar_one()

    assert result == {"bars": len(STOCK_SYMBOLS), "news": 0}
    assert count == len(STOCK_SYMBOLS)

    with session_factory() as session:
        ingest_alpaca_once(session, now=now, market_data=market_data)
        session.commit()
        count = session.execute(select(func.count()).select_from(OhlcvBar)).scalar_one()

    assert count == len(STOCK_SYMBOLS)


def test_ingest_once_routes_to_alpaca(monkeypatch) -> None:
    monkeypatch.setattr(settings, "paper_data_source", "alpaca")
    monkeypatch.setattr(
        "axel.runtime.alpaca_feed.fetch_stock_bars", lambda *a, **k: []
    )

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        result = ingest_once(session, now=datetime(2026, 11, 10, 21, 0, tzinfo=UTC))
    assert result == {"bars": 0, "news": 0}


def test_trading_sections_narrow_to_stocks_for_alpaca(monkeypatch) -> None:
    monkeypatch.setattr(settings, "paper_data_source", "alpaca")
    sections = trading_sections()
    assert list(sections) == ["STOCKS"]
    assert sections["STOCKS"] == STOCK_SYMBOLS

    monkeypatch.setattr(settings, "paper_data_source", "synthetic")
    assert set(trading_sections()) == {
        "STOCKS",
        "OPTIONS",
        "CRYPTO",
        "FOREX",
        "PREDICTIONS",
    }
