"""Tests for the paper execution backends and their core wiring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import axel.db.models
import axel.portfolio.db_models  # noqa: F401 — register tables
from axel.core.config import settings
from axel.core.contracts import Order, Proposal
from axel.core.ids import generate_client_order_id
from axel.core.types import OrderSide, OrderState, OrderType, Section, TimeInForce, TradingMode
from axel.db.base import Base
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    FillModel,
    OrderModel,
    PositionModel,
    TradeProposalModel,
)
from axel.risk.killswitch import KillSwitch
from axel.runtime.core import core_once
from axel.runtime.execution import (
    SLIPPAGE_BPS,
    AlpacaBackend,
    InternalBackend,
    market_is_open,
)
from axel.runtime.ingest import ingest_once

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # Wednesday, regular hours


def _order(side: OrderSide = OrderSide.BUY, qty: float = 10.0) -> Order:
    return Order(
        client_order_id="co_test",
        proposal_id="prop_test",
        symbol="AAPL",
        side=side,
        qty=qty,
        type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        state=OrderState.SUBMITTED,
    )


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# ── InternalBackend ───────────────────────────────────────────────────────────


def test_internal_backend_applies_slippage_adversely() -> None:
    backend = InternalBackend()
    buy = backend.execute(_order(OrderSide.BUY), reference_price=100.0, now=NOW)
    sell = backend.execute(_order(OrderSide.SELL), reference_price=100.0, now=NOW)

    assert buy is not None and sell is not None
    assert buy.price == pytest.approx(100.0 * (1 + SLIPPAGE_BPS / 10_000))
    assert sell.price == pytest.approx(100.0 * (1 - SLIPPAGE_BPS / 10_000))
    assert buy.broker_order_id is None
    assert buy.qty == 10.0
    assert backend.available(datetime(2026, 11, 14, 3, 0, tzinfo=UTC)) is True


# ── Market hours ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        # November: US Eastern is on standard time (UTC-5), so the session is
        # 14:30–21:00 UTC.
        (datetime(2026, 11, 11, 14, 29, tzinfo=UTC), False),
        (datetime(2026, 11, 11, 14, 30, tzinfo=UTC), True),
        (datetime(2026, 11, 11, 21, 0, tzinfo=UTC), True),
        (datetime(2026, 11, 11, 21, 1, tzinfo=UTC), False),
        (datetime(2026, 11, 14, 15, 0, tzinfo=UTC), False),  # Saturday
        # July: daylight time (UTC-4), so the session is 13:30–20:00 UTC.
        (datetime(2026, 7, 15, 13, 29, tzinfo=UTC), False),
        (datetime(2026, 7, 15, 13, 30, tzinfo=UTC), True),
        (datetime(2026, 7, 15, 20, 0, tzinfo=UTC), True),
        (datetime(2026, 7, 15, 20, 1, tzinfo=UTC), False),
    ],
)
def test_market_is_open(stamp: datetime, expected: bool) -> None:
    assert market_is_open(stamp) is expected


# ── AlpacaBackend ─────────────────────────────────────────────────────────────


class _FakeAdapter:
    """Minimal stand-in for the parts of AlpacaAdapter the backend uses."""

    base_url = "https://paper-api.alpaca.markets"

    def __init__(self, *, fill: bool = True) -> None:
        self.fill = fill
        self.submitted: list[Order] = []
        self.canceled: list[str] = []

    def submit_order(self, order: Order, decision: object) -> SimpleNamespace:
        self.submitted.append(order)
        return SimpleNamespace(
            broker_order_id="broker-1",
            filled_qty=0.0,
            filled_avg_price=None,
            state=OrderState.ACCEPTED,
        )

    def get_order(self, broker_order_id: str) -> SimpleNamespace:
        if self.fill:
            return SimpleNamespace(
                filled_qty=10.0,
                filled_avg_price=101.25,
                state=OrderState.FILLED,
            )
        return SimpleNamespace(
            filled_qty=0.0,
            filled_avg_price=None,
            state=OrderState.ACCEPTED,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        self.canceled.append(client_order_id)
        return True


def _backend(adapter: _FakeAdapter) -> AlpacaBackend:
    return AlpacaBackend(adapter, poll_seconds=0.01, timeout_seconds=0.05)  # type: ignore[arg-type]


def test_alpaca_backend_reports_the_broker_fill(monkeypatch) -> None:
    monkeypatch.setattr(settings, "alpaca_paper", True)
    adapter = _FakeAdapter()
    report = _backend(adapter).execute(_order(), reference_price=100.0, now=NOW)

    assert report is not None
    assert report.broker_order_id == "broker-1"
    assert report.price == pytest.approx(101.25)
    assert report.qty == 10.0
    assert adapter.submitted[0].client_order_id == "co_test"
    assert adapter.canceled == []


def test_alpaca_backend_defers_outside_market_hours(monkeypatch) -> None:
    monkeypatch.setattr(settings, "alpaca_paper", True)
    adapter = _FakeAdapter()
    report = _backend(adapter).execute(
        _order(), reference_price=100.0, now=datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
    )

    assert report is None
    assert adapter.submitted == []


def test_alpaca_backend_cancels_and_defers_an_unfilled_order(monkeypatch) -> None:
    monkeypatch.setattr(settings, "alpaca_paper", True)
    adapter = _FakeAdapter(fill=False)
    report = _backend(adapter).execute(_order(), reference_price=100.0, now=NOW)

    assert report is None
    assert adapter.canceled == ["co_test"]


def test_alpaca_backend_requires_paper_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "alpaca_paper", False)
    with pytest.raises(RuntimeError, match="ALPACA_PAPER"):
        AlpacaBackend(_FakeAdapter())  # type: ignore[arg-type]

    monkeypatch.setattr(settings, "alpaca_paper", True)
    live = _FakeAdapter()
    live.base_url = "https://api.alpaca.markets"
    with pytest.raises(RuntimeError, match="non-paper"):
        AlpacaBackend(live)  # type: ignore[arg-type]


# ── Core wiring ───────────────────────────────────────────────────────────────


class _DeferringBackend:
    name = "alpaca"

    def available(self, now: datetime) -> bool:
        return False

    def execute(self, order: Order, *, reference_price: float, now: datetime):
        return None


def _seed(session, now: datetime) -> TradeProposalModel:
    for index in range(10):
        ingest_once(session, now=now + timedelta(seconds=index * 5), interval_seconds=5, seed=7)
    proposal = Proposal(
        section=Section.STOCKS,
        symbol="AAPL",
        side=OrderSide.BUY,
        entry=100.0,
        stop=98.0,
        target=104.0,
        strategy_id="TEST_STRATEGY",
        panel_confidence=0.8,
        win_rate=0.55,
        avg_win_loss_ratio=2.0,
        mode=TradingMode.PAPER,
        ttl_seconds=300,
    )
    row = TradeProposalModel(
        id=proposal.id,
        section="STOCKS",
        symbol=proposal.symbol,
        side="buy",
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        r_multiple_target=2.0,
        position_size_usd=0.0,
        confidence_score=0.8,
        strategy_id=proposal.strategy_id,
        mode="paper",
        status="pending",
        ttl_expires_at=now + timedelta(seconds=300),
        agent_reasoning="unit test",
        created_at=now,
    )
    session.add(row)
    session.commit()
    return row


def test_core_counts_an_order_only_when_it_filled(session_factory, tmp_path) -> None:
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    killswitch = KillSwitch(state_file_path=tmp_path / "killswitch.json")
    with session_factory() as session:
        proposal = _seed(session, now + timedelta(seconds=50))

        result = core_once(
            session,
            now=now + timedelta(seconds=50),
            killswitch=killswitch,
            backend=_DeferringBackend(),
        )
        session.commit()

        assert result.evaluated == 1
        assert result.approved == 1
        assert result.orders == 0
        session.refresh(proposal)
        assert proposal.status == "pending"
        assert session.execute(select(func.count()).select_from(OrderModel)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(FillModel)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(PositionModel)).scalar_one() == 0


def test_core_uses_the_alpaca_fill_price_and_broker_id(session_factory, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "alpaca_paper", True)
    now = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
    killswitch = KillSwitch(state_file_path=tmp_path / "killswitch.json")
    adapter = _FakeAdapter()
    with session_factory() as session:
        proposal = _seed(session, now)

        result = core_once(
            session,
            now=now,
            killswitch=killswitch,
            backend=_backend(adapter),
        )
        session.commit()

        assert result.orders == 1
        order = session.get(OrderModel, generate_client_order_id(proposal.id))
        assert order is not None
        assert order.broker_order_id == "broker-1"
        assert order.type == "market"
        assert order.state == "filled"
        fill = session.execute(select(FillModel)).scalar_one()
        assert fill.price == pytest.approx(101.25)
        position = session.execute(select(PositionModel)).scalar_one()
        assert position.entry_price == pytest.approx(101.25)


class _ReconcilingBackend:
    """Pretends to be a real venue that reports a broker account."""

    name = "alpaca"

    def __init__(self, equity: float) -> None:
        self.equity = equity

    def available(self, now: datetime) -> bool:
        return False

    def execute(self, order: Order, *, reference_price: float, now: datetime):
        return None

    def account_summary(self) -> dict[str, float]:
        return {"total_equity": self.equity, "cash": self.equity, "buying_power": self.equity * 4}


def test_ledger_starts_from_the_broker_cash(session_factory, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "paper_starting_equity", 100_000.0)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    killswitch = KillSwitch(state_file_path=tmp_path / "killswitch.json")
    with session_factory() as session:
        result = core_once(session, now=now, killswitch=killswitch, backend=_DeferringBackend())
        session.commit()

        assert result.nav == pytest.approx(100_000.0)
        snapshot = session.execute(
            select(EquitySnapshotModel).where(EquitySnapshotModel.section == "global")
        ).scalar_one()
        assert snapshot.nav == pytest.approx(100_000.0)
        assert snapshot.high_water_mark == pytest.approx(100_000.0)


def test_reconcile_logs_only_when_the_ledger_drifts(session_factory, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "paper_starting_equity", 100_000.0)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    killswitch = KillSwitch(state_file_path=tmp_path / "killswitch.json")

    with session_factory() as session:
        core_once(
            session,
            now=now,
            killswitch=killswitch,
            backend=_ReconcilingBackend(99_900.0),
        )
        session.commit()
        drift = session.execute(
            select(AuditLogModel).where(AuditLogModel.event_type == "paper.reconcile")
        ).scalars().all()
        assert drift == []

        core_once(
            session,
            now=now + timedelta(seconds=60),
            killswitch=killswitch,
            backend=_ReconcilingBackend(50_000.0),
        )
        session.commit()
        drift = session.execute(
            select(AuditLogModel).where(AuditLogModel.event_type == "paper.reconcile")
        ).scalars().all()
        assert len(drift) == 1
        assert drift[0].payload["delta"] == pytest.approx(50_000.0)
