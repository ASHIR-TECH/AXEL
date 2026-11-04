"""Paper rebalance simulation and post-simulation reconciliation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from support import NOW, make_proposal_bundle

from axel.portfolio.recon import reconcile_positions
from axel.portfolio.simulation import (
    SimulatedExecution,
    SimulationError,
    SimulationPolicy,
    SimulationStatus,
    simulate_rebalance,
    simulated_position_quantities,
)


def _liquidity() -> dict[str, float]:
    return {"stock_a": 1_000_000.0, "stock_b": 1_000_000.0}


def test_completed_paper_simulation_has_no_live_order_surface() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    execution = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument=_liquidity(),
        human_approval_ref="approval-test-1",
    )
    assert execution.status == SimulationStatus.COMPLETED
    assert execution.successful
    assert not execution.is_partial
    assert not execution.live_orders_submitted
    assert execution.reconciliation_required
    assert len(execution.fills) == 2
    assert execution.total_notional > 0
    assert execution.total_cost > 0
    assert all(fill.partial is False for fill in execution.fills)
    assert not hasattr(execution, "order_id")
    assert not hasattr(execution, "submit")


def test_partial_fills_are_reported_and_quantified() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    execution = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument={"stock_a": 200_000.0, "stock_b": 200_000.0},
        human_approval_ref="approval-test-1",
    )
    assert execution.status == SimulationStatus.PARTIAL
    assert execution.is_partial
    assert execution.unfilled
    assert all(fill.partial for fill in execution.fills)
    assert all(fill.participation_rate == pytest.approx(0.5) for fill in execution.fills)
    assert sum(item.unfilled_notional for item in execution.unfilled) > 0
    assert any("reconcile actual fills" in warning for warning in execution.warnings)


def test_missing_liquidity_never_silently_disappears() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    execution = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument={},
        human_approval_ref="approval-test-1",
    )
    assert execution.status == SimulationStatus.NO_LIQUIDITY
    assert not execution.successful
    assert len(execution.unfilled) == 2
    assert {item.reason for item in execution.unfilled} == {"missing_liquidity"}
    assert execution.total_notional == 0.0


def test_stale_proposal_and_snapshot_are_rejected() -> None:
    stale_now = NOW - timedelta(seconds=400)
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle(now=stale_now)
    execution = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument=_liquidity(),
        human_approval_ref="approval-test-1",
    )
    assert execution.status == SimulationStatus.STALE_STATE
    assert not execution.successful
    assert execution.fills == ()


def test_kill_switch_and_missing_approval_block_increase() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    killed = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument=_liquidity(),
        kill_switch_active=True,
        human_approval_ref="approval-test-1",
    )
    assert killed.status == SimulationStatus.REJECTED
    unapproved = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument=_liquidity(),
    )
    assert unapproved.status == SimulationStatus.REJECTED
    assert "human approval" in unapproved.warnings[0]


def test_partial_fill_policy_can_disable_partial_fills() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    execution = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        policy=SimulationPolicy(allow_partial_fills=False),
        now=NOW,
        available_liquidity_by_instrument={"stock_a": 200_000.0, "stock_b": 200_000.0},
        human_approval_ref="approval-test-1",
    )
    assert execution.status == SimulationStatus.NO_LIQUIDITY
    assert {item.reason for item in execution.unfilled} == {"partial_fills_disabled"}


def test_simulated_positions_can_be_reconciled_after_execution() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    execution = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument=_liquidity(),
        human_approval_ref="approval-test-1",
    )
    quantities = simulated_position_quantities(snapshot, execution)
    assert quantities["stock_a"] == pytest.approx(500.0)
    assert quantities["stock_b"] == pytest.approx(250.0)
    authoritative = tuple(
        replace(
            position,
            quantity=quantities[position.instrument_id],
            market_value=quantities[position.instrument_id] * position.price,
        )
        for position in snapshot.positions
    )
    issues = reconcile_positions(authoritative, authoritative, NOW)
    assert issues == ()


def test_missing_position_price_is_reported() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    first = rebalance.changes[0]
    unknown = replace(first, instrument_id="unknown_stock")
    execution = simulate_rebalance(
        replace(rebalance, changes=(unknown,)),
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument={"unknown_stock": 1_000_000.0},
        human_approval_ref="approval-test-1",
    )
    assert execution.status == SimulationStatus.NO_LIQUIDITY
    assert execution.unfilled[0].reason == "missing_position_price"


def test_simulation_cannot_claim_live_orders() -> None:
    _proposal, rebalance, snapshot, exposure, _policy = make_proposal_bundle()
    execution = simulate_rebalance(
        rebalance,
        snapshot,
        exposure,
        now=NOW,
        available_liquidity_by_instrument=_liquidity(),
        human_approval_ref="approval-test-1",
    )
    with pytest.raises(SimulationError, match="live orders"):
        SimulatedExecution(
            **{
                **execution.__dict__,
                "live_orders_submitted": True,
            }
        )


def test_simulation_policy_rejects_invalid_assumptions() -> None:
    with pytest.raises(SimulationError):
        SimulationPolicy(max_participation_rate=0.0)
    with pytest.raises(SimulationError):
        SimulationPolicy(cost_bps=-1.0)
    with pytest.raises(SimulationError):
        SimulationPolicy(min_fill_ratio=1.5)
