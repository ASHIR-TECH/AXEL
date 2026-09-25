"""
Tests for axel/dashboard/query.py — read-only DashboardQuery service and write guard.
"""

from datetime import UTC, datetime

import pytest

from axel.core.ids import generate_id
from axel.dashboard.query import DashboardQuery
from axel.db.models import (
    AuditLogModel,
    EquitySnapshotModel,
    OrderModel,
    StrategyRegistryModel,
    TradeProposalModel,
)

# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_proposal(session, symbol="AAPL", status="pending", section="stocks"):
    p = TradeProposalModel(
        id=generate_id("prop"),
        section=section,
        symbol=symbol,
        side="buy",
        entry_price=150.0,
        stop_loss=145.0,
        take_profit=165.0,
        confidence_score=0.75,
        strategy_id="validated:ma_cross",
        status=status,
        ttl_expires_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(p)
    session.commit()
    return p


def _make_order(session, proposal_id, symbol="AAPL", state="submitted"):
    o = OrderModel(
        client_order_id=generate_id("ord"),
        proposal_id=proposal_id,
        symbol=symbol,
        side="buy",
        qty=10.0,
        state=state,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(o)
    session.commit()
    return o


# ── Write guard ──────────────────────────────────────────────────────────────

class TestWriteGuard:
    def test_raises_on_add(self, in_memory_db):
        _ = DashboardQuery(in_memory_db)
        new_row = TradeProposalModel(
            id=generate_id("prop"),
            section="stocks",
            symbol="AAPL",
            side="buy",
            entry_price=150.0,
            stop_loss=145.0,
            take_profit=165.0,
            confidence_score=0.7,
            strategy_id="validated:test",
            status="pending",
            ttl_expires_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        in_memory_db.add(new_row)
        with pytest.raises(PermissionError, match="read-only"):
            in_memory_db.flush()

    def test_raises_on_delete(self, in_memory_db):
        p = _make_proposal(in_memory_db)
        _ = DashboardQuery(in_memory_db)
        in_memory_db.delete(p)
        with pytest.raises(PermissionError, match="read-only"):
            in_memory_db.flush()



# ── search_proposals ─────────────────────────────────────────────────────────

class TestSearchProposals:
    def test_returns_all_when_no_filters(self, in_memory_db):
        _make_proposal(in_memory_db, symbol="AAPL")
        _make_proposal(in_memory_db, symbol="MSFT")
        dq = DashboardQuery(in_memory_db)
        result = dq.search_proposals()
        assert len(result) == 2

    def test_filter_by_symbol(self, in_memory_db):
        _make_proposal(in_memory_db, symbol="AAPL")
        _make_proposal(in_memory_db, symbol="MSFT")
        dq = DashboardQuery(in_memory_db)
        result = dq.search_proposals(symbol="aapl")  # lowercase should still work
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    def test_filter_by_status(self, in_memory_db):
        _make_proposal(in_memory_db, status="approved")
        _make_proposal(in_memory_db, status="rejected")
        dq = DashboardQuery(in_memory_db)
        result = dq.search_proposals(status="approved")
        assert len(result) == 1
        assert result[0]["status"] == "approved"

    def test_filter_by_section(self, in_memory_db):
        _make_proposal(in_memory_db, section="stocks")
        _make_proposal(in_memory_db, section="crypto")
        dq = DashboardQuery(in_memory_db)
        result = dq.search_proposals(section="crypto")
        assert all(r["section"] == "crypto" for r in result)

    def test_limit_enforced(self, in_memory_db):
        for sym in ["AAPL", "MSFT", "TSLA", "NVDA"]:
            _make_proposal(in_memory_db, symbol=sym)
        dq = DashboardQuery(in_memory_db)
        result = dq.search_proposals(limit=2)
        assert len(result) == 2

    def test_limit_validation(self, in_memory_db):
        dq = DashboardQuery(in_memory_db)
        with pytest.raises(ValueError):
            dq.search_proposals(limit=0)

    def test_result_keys_present(self, in_memory_db):
        _make_proposal(in_memory_db)
        dq = DashboardQuery(in_memory_db)
        result = dq.search_proposals()
        required_keys = {"id", "symbol", "section", "side", "entry_price", "status", "created_at"}
        assert required_keys.issubset(result[0].keys())


# ── get_order_history ─────────────────────────────────────────────────────────

class TestGetOrderHistory:
    def test_returns_empty_when_no_orders(self, in_memory_db):
        dq = DashboardQuery(in_memory_db)
        assert dq.get_order_history() == []

    def test_returns_orders(self, in_memory_db):
        p = _make_proposal(in_memory_db)
        _make_order(in_memory_db, p.id, symbol="AAPL")
        dq = DashboardQuery(in_memory_db)
        result = dq.get_order_history()
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    def test_filter_by_state(self, in_memory_db):
        p = _make_proposal(in_memory_db)
        _make_order(in_memory_db, p.id, state="filled")
        _make_order(in_memory_db, p.id, state="submitted")
        dq = DashboardQuery(in_memory_db)
        result = dq.get_order_history(state="filled")
        assert len(result) == 1

    def test_limit_validation(self, in_memory_db):
        dq = DashboardQuery(in_memory_db)
        with pytest.raises(ValueError):
            dq.get_order_history(limit=0)


# ── get_equity_snapshots ──────────────────────────────────────────────────────

class TestGetEquitySnapshots:
    def test_returns_empty_on_empty_db(self, in_memory_db):
        dq = DashboardQuery(in_memory_db)
        assert dq.get_equity_snapshots() == []

    def test_returns_matching_section(self, in_memory_db):
        for section in ("global", "stocks"):
            snap = EquitySnapshotModel(
                id=generate_id("eq"),
                timestamp=datetime.now(UTC),
                section=section,
                nav=100_000.0,
                cash=50_000.0,
            )
            in_memory_db.add(snap)
        in_memory_db.commit()
        dq = DashboardQuery(in_memory_db)
        result = dq.get_equity_snapshots(section="stocks")
        assert all(r["section"] == "stocks" for r in result)


# ── get_audit_log ─────────────────────────────────────────────────────────────

class TestGetAuditLog:
    def test_empty(self, in_memory_db):
        dq = DashboardQuery(in_memory_db)
        assert dq.get_audit_log() == []

    def test_returns_events(self, in_memory_db):
        entry = AuditLogModel(
            id=generate_id("audit"),
            timestamp=datetime.now(UTC),
            event_type="KILL_SWITCH_TRIP",
            actor="watchdog",
            payload={"reason": "drawdown"},
        )
        in_memory_db.add(entry)
        in_memory_db.commit()
        dq = DashboardQuery(in_memory_db)
        result = dq.get_audit_log()
        assert len(result) == 1
        assert result[0]["event_type"] == "KILL_SWITCH_TRIP"

    def test_filter_by_event_type(self, in_memory_db):
        for etype in ("KILL_SWITCH_TRIP", "ORDER_SUBMITTED"):
            entry = AuditLogModel(
                id=generate_id("audit"),
                timestamp=datetime.now(UTC),
                event_type=etype,
                actor="system",
            )
            in_memory_db.add(entry)
        in_memory_db.commit()
        dq = DashboardQuery(in_memory_db)
        result = dq.get_audit_log(event_type="ORDER_SUBMITTED")
        assert len(result) == 1


# ── get_strategy_registry ─────────────────────────────────────────────────────

class TestGetStrategyRegistry:
    def test_empty(self, in_memory_db):
        dq = DashboardQuery(in_memory_db)
        assert dq.get_strategy_registry() == []

    def test_returns_strategies(self, in_memory_db):
        strat = StrategyRegistryModel(
            id=generate_id("strat"),
            strategy_id="validated:ma_cross",
            section="stocks",
            status="paper_approved",
            parameters_hash="abc123",
            parameters_yaml="window: 20",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        in_memory_db.add(strat)
        in_memory_db.commit()
        dq = DashboardQuery(in_memory_db)
        result = dq.get_strategy_registry()
        assert len(result) == 1
        assert result[0]["strategy_id"] == "validated:ma_cross"

    def test_filter_by_status(self, in_memory_db):
        for status in ("candidate", "paper_approved"):
            strat = StrategyRegistryModel(
                id=generate_id("strat"),
                strategy_id=f"validated:{status}",
                section="stocks",
                status=status,
                parameters_hash="x",
                parameters_yaml="w: 1",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            in_memory_db.add(strat)
        in_memory_db.commit()
        dq = DashboardQuery(in_memory_db)
        result = dq.get_strategy_registry(status="paper_approved")
        assert len(result) == 1
        assert result[0]["status"] == "paper_approved"
