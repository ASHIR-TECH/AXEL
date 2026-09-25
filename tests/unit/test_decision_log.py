"""
Tests for axel/dashboard/decision_log.py — read-only lineage and portfolio views.
"""

from datetime import UTC, datetime

import pytest

from axel.core.ids import generate_id
from axel.dashboard.decision_log import (
    agent_run_summary,
    equity_curve,
    killswitch_history,
    open_positions_snapshot,
    proposal_lineage,
    recent_decisions,
)
from axel.db.models import (
    AgentRunModel,
    EquitySnapshotModel,
    KillSwitchEventModel,
    PositionModel,
    RiskDecisionModel,
    TradeProposalModel,
)

# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_proposal(session, symbol="AAPL", status="pending", strategy_id="validated:ma_cross"):
    """Insert a minimal TradeProposalModel and return it."""
    p = TradeProposalModel(
        id=generate_id("prop"),
        section="stocks",
        symbol=symbol,
        side="buy",
        entry_price=150.0,
        stop_loss=145.0,
        take_profit=165.0,
        confidence_score=0.75,
        strategy_id=strategy_id,
        status=status,
        ttl_expires_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(p)
    session.commit()
    return p


def _make_risk_decision(session, proposal_id, approved=True, verdict="approved"):
    rd = RiskDecisionModel(
        id=generate_id("risk"),
        proposal_id=proposal_id,
        approved=approved,
        verdict=verdict,
        approved_qty=10.0,
        approved_notional_usd=1500.0,
        binding_limit="KELLY_FRACTION",
        reasons=["All checks passed"],
        checks_passed=["GLOBAL_KILL_SWITCH_CLEAR"],
        checks_failed=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(rd)
    session.commit()
    return rd


# ── recent_decisions ─────────────────────────────────────────────────────────

class TestRecentDecisions:
    def test_returns_list(self, in_memory_db):
        result = recent_decisions(in_memory_db)
        assert isinstance(result, list)

    def test_returns_proposal_without_risk_decision(self, in_memory_db):
        _make_proposal(in_memory_db, symbol="MSFT")
        result = recent_decisions(in_memory_db)
        assert len(result) == 1
        assert result[0]["symbol"] == "MSFT"
        assert result[0]["risk_verdict"] is None

    def test_returns_risk_verdict_when_present(self, in_memory_db):
        p = _make_proposal(in_memory_db)
        _make_risk_decision(in_memory_db, p.id)
        result = recent_decisions(in_memory_db)
        assert result[0]["risk_verdict"] == "approved"

    def test_limit_validation_lower(self, in_memory_db):
        with pytest.raises(ValueError):
            recent_decisions(in_memory_db, limit=0)

    def test_limit_validation_upper(self, in_memory_db):
        with pytest.raises(ValueError):
            recent_decisions(in_memory_db, limit=501)

    def test_limit_respected(self, in_memory_db):
        for sym in ["AAPL", "MSFT", "TSLA"]:
            _make_proposal(in_memory_db, symbol=sym)
        result = recent_decisions(in_memory_db, limit=2)
        assert len(result) == 2


# ── proposal_lineage ─────────────────────────────────────────────────────────

class TestProposalLineage:
    def test_raises_for_unknown_proposal(self, in_memory_db):
        with pytest.raises(KeyError, match="not found"):
            proposal_lineage(in_memory_db, "nonexistent-id")

    def test_basic_lineage_no_signals_no_orders(self, in_memory_db):
        p = _make_proposal(in_memory_db, symbol="AAPL")
        lineage = proposal_lineage(in_memory_db, p.id)
        assert lineage["proposal"]["symbol"] == "AAPL"
        assert lineage["signals"] == []
        assert lineage["panel_decision"] is None
        assert lineage["risk_decision"] is None
        assert lineage["orders"] == []
        assert lineage["fills"] == []

    def test_lineage_with_risk_decision(self, in_memory_db):
        p = _make_proposal(in_memory_db)
        _make_risk_decision(in_memory_db, p.id, approved=True, verdict="approved")
        lineage = proposal_lineage(in_memory_db, p.id)
        assert lineage["risk_decision"]["approved"] is True
        assert lineage["risk_decision"]["verdict"] == "approved"

    def test_lineage_rejected_risk_decision(self, in_memory_db):
        p = _make_proposal(in_memory_db)
        _make_risk_decision(in_memory_db, p.id, approved=False, verdict="rejected")
        lineage = proposal_lineage(in_memory_db, p.id)
        assert lineage["risk_decision"]["approved"] is False

    def test_lineage_keys_present(self, in_memory_db):
        p = _make_proposal(in_memory_db)
        lineage = proposal_lineage(in_memory_db, p.id)
        for key in ("proposal", "signals", "panel_decision", "risk_decision", "orders", "fills"):
            assert key in lineage


# ── equity_curve ─────────────────────────────────────────────────────────────

class TestEquityCurve:
    def test_returns_empty_for_empty_db(self, in_memory_db):
        result = equity_curve(in_memory_db)
        assert result == []

    def test_returns_rows_for_matching_section(self, in_memory_db):
        snap = EquitySnapshotModel(
            id=generate_id("eq"),
            timestamp=datetime.now(UTC),
            section="global",
            nav=100_000.0,
            cash=50_000.0,
            gross_exposure=50_000.0,
            net_exposure=40_000.0,
            realized_pnl=0.0,
            unrealized_pnl=500.0,
            drawdown_pct=0.0,
            high_water_mark=100_000.0,
        )
        in_memory_db.add(snap)
        in_memory_db.commit()
        result = equity_curve(in_memory_db, section="global")
        assert len(result) == 1
        assert result[0]["nav"] == 100_000.0

    def test_filters_by_section(self, in_memory_db):
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
        result = equity_curve(in_memory_db, section="stocks")
        assert all(r["section"] == "stocks" for r in result)

    def test_limit_validation(self, in_memory_db):
        with pytest.raises(ValueError):
            equity_curve(in_memory_db, limit=0)


# ── open_positions_snapshot ───────────────────────────────────────────────────

class TestOpenPositionsSnapshot:
    def test_empty(self, in_memory_db):
        assert open_positions_snapshot(in_memory_db) == []

    def test_returns_positions(self, in_memory_db):
        pos = PositionModel(
            id=generate_id("pos"),
            section="stocks",
            symbol="AAPL",
            side="LONG",
            qty=10.0,
            entry_price=150.0,
            current_price=155.0,
            unrealized_pnl=50.0,
            realized_pnl=0.0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        in_memory_db.add(pos)
        in_memory_db.commit()
        result = open_positions_snapshot(in_memory_db)
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"


# ── killswitch_history ────────────────────────────────────────────────────────

class TestKillswitchHistory:
    def test_empty(self, in_memory_db):
        assert killswitch_history(in_memory_db) == []

    def test_returns_events(self, in_memory_db):
        event = KillSwitchEventModel(
            id=generate_id("ks"),
            timestamp=datetime.now(UTC),
            reason="10% drawdown",
            action_taken="HALT_NEW_ORDERS",
            is_active=True,
        )
        in_memory_db.add(event)
        in_memory_db.commit()
        result = killswitch_history(in_memory_db)
        assert len(result) == 1
        assert result[0]["reason"] == "10% drawdown"

    def test_limit_validation(self, in_memory_db):
        with pytest.raises(ValueError):
            killswitch_history(in_memory_db, limit=0)


# ── agent_run_summary ─────────────────────────────────────────────────────────

class TestAgentRunSummary:
    def test_empty_returns_zero_totals(self, in_memory_db):
        result = agent_run_summary(in_memory_db)
        assert result["total_runs"] == 0
        assert result["total_cost_usd"] == 0.0

    def test_aggregates_cost_and_tokens(self, in_memory_db):
        for _ in range(3):
            run = AgentRunModel(
                id=generate_id("run"),
                timestamp=datetime.now(UTC),
                agent_name="technical_analyst",
                model="llama3-8b",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.01,
                latency_ms=250,
            )
            in_memory_db.add(run)
        in_memory_db.commit()
        result = agent_run_summary(in_memory_db, hours=24)
        assert result["total_runs"] == 3
        assert abs(result["total_cost_usd"] - 0.03) < 1e-6
        assert result["total_prompt_tokens"] == 300
        assert result["total_completion_tokens"] == 150

    def test_by_agent_breakdown(self, in_memory_db):
        for name in ("agent_a", "agent_b", "agent_a"):
            run = AgentRunModel(
                id=generate_id("run"),
                timestamp=datetime.now(UTC),
                agent_name=name,
                model="llama3-8b",
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=0.001,
                latency_ms=100,
            )
            in_memory_db.add(run)
        in_memory_db.commit()
        result = agent_run_summary(in_memory_db)
        assert result["by_agent"]["agent_a"]["runs"] == 2
        assert result["by_agent"]["agent_b"]["runs"] == 1

    def test_hours_validation(self, in_memory_db):
        with pytest.raises(ValueError):
            agent_run_summary(in_memory_db, hours=0)
