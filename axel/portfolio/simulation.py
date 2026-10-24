"""
P11-REBALANCE: paper-only rebalance simulation.

This module converts a rebalance proposal into simulated fills for research and
shadow-mode evidence. It has no broker adapter, no order IDs, and no execution
imports. A simulation result can never be submitted as a live order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite

from axel.portfolio.serialization import fingerprint
from axel.portfolio.types import (
    ExposureSnapshot,
    PortfolioSnapshot,
    ProposalState,
    RebalanceProposal,
    require_aware,
    require_non_negative,
)

SIMULATION_VERSION = "p11-paper-simulation-1"


class SimulationStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    REJECTED = "rejected"
    STALE_STATE = "stale_state"
    NO_LIQUIDITY = "no_liquidity"
    NO_CHANGES = "no_changes"


class SimulationError(ValueError):
    """Raised for malformed simulation configuration or inputs."""


@dataclass(frozen=True)
class SimulationPolicy:
    """Explicit paper-execution assumptions. These are not venue guarantees."""

    policy_version: str = "p11-simulation-policy-1"
    max_participation_rate: float = 0.10
    cost_bps: float = 10.0
    max_staleness_seconds: int = 300
    allow_partial_fills: bool = True
    min_fill_ratio: float = 0.0
    require_human_approval_for_increase: bool = True

    def __post_init__(self) -> None:
        if not self.policy_version:
            raise SimulationError("policy_version must be non-empty")
        if not 0.0 < self.max_participation_rate <= 1.0:
            raise SimulationError("max_participation_rate must be in (0, 1]")
        if not isfinite(self.cost_bps) or self.cost_bps < 0.0:
            raise SimulationError("cost_bps must be finite and non-negative")
        if self.max_staleness_seconds < 0:
            raise SimulationError("max_staleness_seconds must be non-negative")
        if not 0.0 <= self.min_fill_ratio <= 1.0:
            raise SimulationError("min_fill_ratio must be in [0, 1]")


@dataclass(frozen=True)
class SimulatedFill:
    instrument_id: str
    side: str
    requested_quantity: float
    filled_quantity: float
    remaining_quantity: float
    price: float
    notional: float
    cost: float
    participation_rate: float
    partial: bool

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise SimulationError("instrument_id must be non-empty")
        if self.side not in {"buy", "sell"}:
            raise SimulationError("side must be buy or sell")
        for name in (
            "requested_quantity",
            "filled_quantity",
            "remaining_quantity",
            "price",
            "notional",
            "cost",
            "participation_rate",
        ):
            value = getattr(self, name)
            if not isfinite(value):
                raise SimulationError(f"{name} must be finite")
            if value < 0:
                raise SimulationError(f"{name} must be non-negative")


@dataclass(frozen=True)
class UnfilledChange:
    instrument_id: str
    requested_notional: float
    unfilled_notional: float
    reason: str

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.reason:
            raise SimulationError("instrument_id and reason must be non-empty")


@dataclass(frozen=True)
class SimulatedExecution:
    """Paper fills only. This object has no live-order or broker capability."""

    simulation_id: str
    rebalance_id: str
    portfolio_snapshot_id: str
    status: SimulationStatus
    fills: tuple[SimulatedFill, ...]
    unfilled: tuple[UnfilledChange, ...]
    total_notional: float
    total_cost: float
    policy_version: str
    created_at: datetime
    warnings: tuple[str, ...] = ()
    live_orders_submitted: bool = False
    reconciliation_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", require_aware(self.created_at, "created_at"))
        if not self.simulation_id or not self.rebalance_id:
            raise SimulationError("simulation_id and rebalance_id must be non-empty")
        require_non_negative(self.total_notional, "total_notional")
        require_non_negative(self.total_cost, "total_cost")
        if self.live_orders_submitted:
            raise SimulationError("paper simulation cannot submit live orders")

    @property
    def successful(self) -> bool:
        return self.status in {SimulationStatus.COMPLETED, SimulationStatus.PARTIAL}

    @property
    def is_partial(self) -> bool:
        return self.status == SimulationStatus.PARTIAL

    @property
    def filled_instrument_ids(self) -> tuple[str, ...]:
        return tuple(fill.instrument_id for fill in self.fills)


def _empty(
    *,
    rebalance: RebalanceProposal,
    snapshot: PortfolioSnapshot,
    status: SimulationStatus,
    policy: SimulationPolicy,
    now: datetime,
    warnings: tuple[str, ...],
) -> SimulatedExecution:
    return SimulatedExecution(
        simulation_id=fingerprint(
            "sim",
            SIMULATION_VERSION,
            rebalance.rebalance_id,
            snapshot.snapshot_id,
            status.value,
            now,
        ),
        rebalance_id=rebalance.rebalance_id,
        portfolio_snapshot_id=snapshot.snapshot_id,
        status=status,
        fills=(),
        unfilled=(),
        total_notional=0.0,
        total_cost=0.0,
        policy_version=policy.policy_version,
        created_at=now,
        warnings=warnings,
    )


def simulate_rebalance(
    rebalance: RebalanceProposal,
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    *,
    policy: SimulationPolicy | None = None,
    now: datetime | None = None,
    available_liquidity_by_instrument: Mapping[str, float] | None = None,
    kill_switch_active: bool = False,
    human_approval_ref: str | None = None,
) -> SimulatedExecution:
    """Simulate paper fills under explicit liquidity and staleness assumptions."""
    rules = policy or SimulationPolicy()
    current = require_aware(now or snapshot.snapshot_time, "now")
    risk_increasing = rebalance.is_risk_increasing
    if rebalance.validation_state not in {
        ProposalState.PENDING_VALIDATION,
        ProposalState.DRAFT,
    }:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.REJECTED,
            policy=rules,
            now=current,
            warnings=("rebalance is not in a simulation-eligible state",),
        )
    if rebalance.expires_at <= current:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.STALE_STATE,
            policy=rules,
            now=current,
            warnings=("rebalance proposal is expired",),
        )
    if (current - rebalance.created_at).total_seconds() > rules.max_staleness_seconds:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.STALE_STATE,
            policy=rules,
            now=current,
            warnings=("rebalance proposal exceeds the staleness limit",),
        )
    if snapshot.age_seconds(current) > rules.max_staleness_seconds:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.STALE_STATE,
            policy=rules,
            now=current,
            warnings=("portfolio snapshot exceeds the staleness limit",),
        )
    if rebalance.portfolio_snapshot_id != snapshot.snapshot_id:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.REJECTED,
            policy=rules,
            now=current,
            warnings=("rebalance and snapshot references do not match",),
        )
    if exposure.portfolio_snapshot_id != snapshot.snapshot_id:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.REJECTED,
            policy=rules,
            now=current,
            warnings=("exposure and snapshot references do not match",),
        )
    if not rebalance.changes:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.NO_CHANGES,
            policy=rules,
            now=current,
            warnings=("rebalance contains no changes",),
        )
    if risk_increasing and kill_switch_active:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.REJECTED,
            policy=rules,
            now=current,
            warnings=("global kill switch blocks risk-increasing simulations",),
        )
    if (
        risk_increasing
        and rules.require_human_approval_for_increase
        and not human_approval_ref
    ):
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.REJECTED,
            policy=rules,
            now=current,
            warnings=("risk-increasing simulation requires a human approval reference",),
        )
    if risk_increasing and not exposure.is_complete:
        return _empty(
            rebalance=rebalance,
            snapshot=snapshot,
            status=SimulationStatus.REJECTED,
            policy=rules,
            now=current,
            warnings=("exposure snapshot is incomplete; cannot simulate an increase",),
        )

    positions = {position.instrument_id: position for position in snapshot.positions}
    fills: list[SimulatedFill] = []
    unfilled: list[UnfilledChange] = []
    warnings: list[str] = []
    for change in rebalance.changes:
        requested_notional = abs(change.delta_exposure)
        if requested_notional <= 0.0:
            continue
        position = positions.get(change.instrument_id)
        if position is None or position.price <= 0.0:
            unfilled.append(
                UnfilledChange(
                    instrument_id=change.instrument_id,
                    requested_notional=requested_notional,
                    unfilled_notional=requested_notional,
                    reason="missing_position_price",
                )
            )
            warnings.append(
                f"no position price for {change.instrument_id}; no fill simulated"
            )
            continue
        price = position.price
        requested_quantity = requested_notional / price
        liquidity = (
            None
            if available_liquidity_by_instrument is None
            else available_liquidity_by_instrument.get(change.instrument_id)
        )
        if liquidity is None or not isfinite(float(liquidity)) or liquidity <= 0.0:
            unfilled.append(
                UnfilledChange(
                    instrument_id=change.instrument_id,
                    requested_notional=requested_notional,
                    unfilled_notional=requested_notional,
                    reason="missing_liquidity",
                )
            )
            warnings.append(
                f"no approved liquidity for {change.instrument_id}; no fill simulated"
            )
            continue
        capacity = float(liquidity) * rules.max_participation_rate
        ratio = min(1.0, capacity / requested_notional)
        if not rules.allow_partial_fills and ratio < 1.0:
            unfilled.append(
                UnfilledChange(
                    instrument_id=change.instrument_id,
                    requested_notional=requested_notional,
                    unfilled_notional=requested_notional,
                    reason="partial_fills_disabled",
                )
            )
            warnings.append(
                f"partial fill disabled for {change.instrument_id}; no fill simulated"
            )
            continue
        if ratio < rules.min_fill_ratio:
            unfilled.append(
                UnfilledChange(
                    instrument_id=change.instrument_id,
                    requested_notional=requested_notional,
                    unfilled_notional=requested_notional,
                    reason="below_minimum_fill_ratio",
                )
            )
            warnings.append(
                f"fill ratio for {change.instrument_id} is below the configured minimum"
            )
            continue
        filled_quantity = requested_quantity * ratio
        filled_notional = filled_quantity * price
        cost = filled_notional * rules.cost_bps / 10_000.0
        fills.append(
            SimulatedFill(
                instrument_id=change.instrument_id,
                side="buy" if change.delta_exposure > 0.0 else "sell",
                requested_quantity=requested_quantity,
                filled_quantity=filled_quantity,
                remaining_quantity=requested_quantity - filled_quantity,
                price=price,
                notional=filled_notional,
                cost=cost,
                participation_rate=ratio,
                partial=ratio < 1.0,
            )
        )
        if ratio < 1.0:
            unfilled.append(
                UnfilledChange(
                    instrument_id=change.instrument_id,
                    requested_notional=requested_notional,
                    unfilled_notional=requested_notional - filled_notional,
                    reason="liquidity_limit",
                )
            )

    if not fills:
        status = SimulationStatus.NO_LIQUIDITY
    elif unfilled:
        status = SimulationStatus.PARTIAL
    else:
        status = SimulationStatus.COMPLETED
    if status == SimulationStatus.PARTIAL:
        warnings.append("partial fills simulated; reconcile actual fills before relying on targets")
    warnings.append(
        "Simulation uses supplied liquidity and a flat cost assumption; venue-specific "
        "fills, impact, and fees require paper or live execution evidence."
    )
    total_notional = sum(fill.notional for fill in fills)
    total_cost = sum(fill.cost for fill in fills)
    return SimulatedExecution(
        simulation_id=fingerprint(
            "sim",
            SIMULATION_VERSION,
            rebalance.rebalance_id,
            snapshot.snapshot_id,
            tuple(fills),
            tuple(unfilled),
            current,
        ),
        rebalance_id=rebalance.rebalance_id,
        portfolio_snapshot_id=snapshot.snapshot_id,
        status=status,
        fills=tuple(fills),
        unfilled=tuple(unfilled),
        total_notional=total_notional,
        total_cost=total_cost,
        policy_version=rules.policy_version,
        created_at=current,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def simulated_position_quantities(
    snapshot: PortfolioSnapshot,
    execution: SimulatedExecution,
) -> Mapping[str, float]:
    """Apply paper fills to current quantities for post-simulation reconciliation."""
    if execution.live_orders_submitted:
        raise SimulationError("cannot apply a live-order execution in paper simulation")
    quantities = {position.instrument_id: position.quantity for position in snapshot.positions}
    for fill in execution.fills:
        delta = fill.filled_quantity if fill.side == "buy" else -fill.filled_quantity
        quantities[fill.instrument_id] = quantities.get(fill.instrument_id, 0.0) + delta
    return quantities


__all__ = [
    "SIMULATION_VERSION",
    "SimulatedExecution",
    "SimulatedFill",
    "SimulationError",
    "SimulationPolicy",
    "SimulationStatus",
    "UnfilledChange",
    "simulate_rebalance",
    "simulated_position_quantities",
]
