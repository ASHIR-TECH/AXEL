"""
Broker-to-AXEL reconciliation (Phase 6, spec §10).

The broker is authoritative for what it accepted and executed; AXEL is
authoritative for its own proposals, approvals, risk decisions and audit
history. Neither side is silently overwritten to force agreement: every
discrepancy becomes an :class:`~axel.execution.incidents.Incident` with a stable
reason code and a deterministic action set.

``reconcile_positions`` and ``reconcile_orders`` retain their original Phase 1
signatures for backwards compatibility. ``reconcile_all`` /
``reconcile_live`` implement the full §10.2 loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from axel.comms.alerts import reconciliation_alert
from axel.core.clock import Clock, RealClock
from axel.core.contracts import Order
from axel.core.logging import logger
from axel.core.money import to_decimal
from axel.core.types import OrderState
from axel.execution.broker_base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerFill,
    BrokerOrder,
    PositionInfo,
)
from axel.execution.incidents import Incident, IncidentStore
from axel.execution.order_store import OrderRecord
from axel.execution.reason_codes import IncidentCategory, IncidentSeverity, ReconReason

TERMINAL_STATES = frozenset(
    {OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED, OrderState.REJECTED}
)
IN_FLIGHT_STATES = frozenset(
    {OrderState.SUBMITTED, OrderState.APPROVED, OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED, OrderState.UNKNOWN, OrderState.CANCEL_PENDING}
)


@dataclass(frozen=True)
class PositionDrift:
    symbol: str
    db_qty: float
    broker_qty: float
    difference: float


@dataclass(frozen=True)
class ReconciliationReport:
    timestamp: str
    is_healthy: bool
    position_drifts: list[PositionDrift] = field(default_factory=list)
    ghost_broker_orders: list[str] = field(default_factory=list)
    unmatched_db_orders: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReconMismatch:
    """One classified discrepancy from the reconciliation loop."""

    case: ReconReason
    category: IncidentCategory
    severity: IncidentSeverity
    summary: str
    client_order_id: str | None = None
    broker_order_id: str | None = None
    symbol: str | None = None
    expected: str | None = None
    observed: str | None = None

    @property
    def dedup_key(self) -> str:
        return "|".join(
            [
                self.case.value,
                self.client_order_id or self.broker_order_id or "",
                self.symbol or "",
            ]
        )


@dataclass
class ReconciliationResult:
    """Full result of a §10.2 reconciliation run."""

    as_of: datetime
    mismatches: list[ReconMismatch] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    snapshot_age_seconds: float | None = None
    broker_account_id: str | None = None

    @property
    def is_healthy(self) -> bool:
        return not self.mismatches

    @property
    def blocks_new_risk(self) -> bool:
        return any(m.severity is IncidentSeverity.CRITICAL for m in self.mismatches)

    @property
    def reason_codes(self) -> list[str]:
        return [m.case.value for m in self.mismatches]


class ReconciliationEngine:
    """
    Periodic job that verifies synchronisation between AXEL state and broker
    reality, and classifies every discrepancy into an incident.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        alert_callback: Callable[[object], None] | None = None,
        incident_store: IncidentStore | None = None,
        clock: Clock | None = None,
    ):
        self.broker = broker
        self.alert_callback = alert_callback
        self.incidents = incident_store or IncidentStore()
        self.clock = clock or RealClock()

    # ── Legacy Phase 1 API ──────────────────────────────────────────────────
    def reconcile_positions(
        self,
        db_positions: dict[str, float],
    ) -> list[PositionDrift]:
        """Compares DB position quantities against live broker positions."""
        broker_positions = {p.symbol: p.qty for p in self.broker.get_positions()}
        drifts: list[PositionDrift] = []

        all_symbols = set(db_positions.keys()).union(set(broker_positions.keys()))
        for sym in all_symbols:
            db_qty = db_positions.get(sym, 0.0)
            broker_qty = broker_positions.get(sym, 0.0)
            diff = abs(db_qty - broker_qty)
            if diff > 1e-4:
                drifts.append(
                    PositionDrift(
                        symbol=sym,
                        db_qty=db_qty,
                        broker_qty=broker_qty,
                        difference=round(diff, 4),
                    )
                )
        return drifts

    def reconcile_orders(
        self,
        active_db_orders: list[Order],
        broker_open_order_ids: list[str],
    ) -> ReconciliationReport:
        """
        Compares open order states between DB and broker (legacy API).
        Fires a reconciliation_alert via alert_callback when any drift is detected.
        """
        db_client_ids = {
            o.client_order_id for o in active_db_orders if o.state not in TERMINAL_STATES
        }
        broker_id_set = set(broker_open_order_ids)

        ghost_orders = list(broker_id_set - db_client_ids)
        unmatched_db = list(db_client_ids - broker_id_set)

        is_healthy = len(ghost_orders) == 0 and len(unmatched_db) == 0
        details = []
        if ghost_orders:
            details.append(
                f"Detected {len(ghost_orders)} orders on broker not tracked in DB (Ghost Orders)!"
            )
        if unmatched_db:
            details.append(f"Detected {len(unmatched_db)} active DB orders missing on broker!")

        report = ReconciliationReport(
            timestamp=self.clock.now().isoformat(),
            is_healthy=is_healthy,
            ghost_broker_orders=ghost_orders,
            unmatched_db_orders=unmatched_db,
            details=details,
        )

        if not is_healthy:
            logger.warning(
                "RECONCILIATION BREAK DETECTED between internal state and broker.",
                extra={"report": details},
            )
            if self.alert_callback:
                self.alert_callback(reconciliation_alert(ghost_orders, unmatched_db))

        return report

    # ── Phase 6 full loop ───────────────────────────────────────────────────
    def reconcile_live(
        self,
        *,
        axel_orders: list[OrderRecord],
        fill_derived_positions: dict[str, Decimal],
        axel_cash: Decimal | None = None,
        axel_buying_power: Decimal | None = None,
        max_snapshot_age_seconds: int | None = None,
        since: datetime | None = None,
    ) -> ReconciliationResult:
        """
        Acquire a broker snapshot and run the full reconciliation loop.

        Read-only and safe to run during a halt.
        """
        account = self.broker.get_account()
        open_orders = self.broker.get_open_orders()
        positions = self.broker.get_positions()
        fills = self.broker.list_fills(since=since)
        result = self.reconcile_all(
            axel_orders=axel_orders,
            broker_account=account,
            broker_open_orders=open_orders,
            broker_positions=positions,
            broker_fills=fills,
            fill_derived_positions=fill_derived_positions,
            axel_cash=axel_cash,
            axel_buying_power=axel_buying_power,
            snapshot_time=self.clock.now(),
            max_snapshot_age_seconds=max_snapshot_age_seconds,
        )
        if not result.is_healthy:
            self._alert(result)
        return result

    def reconcile_all(
        self,
        *,
        axel_orders: list[OrderRecord],
        broker_account: BrokerAccount | None,
        broker_open_orders: list[BrokerOrder],
        broker_positions: list[PositionInfo],
        broker_fills: list[BrokerFill],
        fill_derived_positions: dict[str, Decimal],
        axel_cash: Decimal | None = None,
        axel_buying_power: Decimal | None = None,
        snapshot_time: datetime | None = None,
        max_snapshot_age_seconds: int | None = None,
    ) -> ReconciliationResult:
        """Pure comparison of an AXEL snapshot against a broker snapshot."""
        now = self.clock.now()
        snapshot_time = snapshot_time or now
        mismatches: list[ReconMismatch] = []

        axel_by_client = {o.client_order_id: o for o in axel_orders}
        axel_by_broker = {
            o.broker_order_id: o for o in axel_orders if o.broker_order_id is not None
        }
        broker_open_client_ids = {
            o.client_order_id for o in broker_open_orders if o.client_order_id is not None
        }

        # 9. Snapshot freshness.
        age = (now - snapshot_time).total_seconds()
        if max_snapshot_age_seconds is not None and age > max_snapshot_age_seconds:
            mismatches.append(
                ReconMismatch(
                    case=ReconReason.STALE_SNAPSHOT,
                    category=IncidentCategory.STALE_MARKET_DATA,
                    severity=IncidentSeverity.CRITICAL,
                    summary=f"Broker snapshot is {age:.0f}s old (max {max_snapshot_age_seconds}s).",
                    expected=f"<={max_snapshot_age_seconds}s",
                    observed=f"{age:.0f}s",
                )
            )

        # 1. Broker orders with no matching AXEL intent.
        for broker_order in broker_open_orders:
            matched = None
            if broker_order.client_order_id:
                matched = axel_by_client.get(broker_order.client_order_id)
            if matched is None and broker_order.broker_order_id:
                matched = axel_by_broker.get(broker_order.broker_order_id)
            if matched is None:
                mismatches.append(
                    ReconMismatch(
                        case=ReconReason.BROKER_ORDER_WITHOUT_INTENT,
                        category=IncidentCategory.UNKNOWN_BROKER_ORDER,
                        severity=IncidentSeverity.CRITICAL,
                        summary=f"Broker order {broker_order.broker_order_id} has no AXEL intent.",
                        client_order_id=broker_order.client_order_id,
                        broker_order_id=broker_order.broker_order_id,
                        symbol=broker_order.symbol,
                    )
                )

        # 2/7/8. AXEL orders vs broker.
        for record in axel_orders:
            if record.state in TERMINAL_STATES:
                # 8. Broker still open while AXEL considers terminal.
                if record.client_order_id in broker_open_client_ids:
                    mismatches.append(
                        ReconMismatch(
                            case=ReconReason.TERMINAL_STATE_MISMATCH,
                            category=IncidentCategory.UNKNOWN_BROKER_ORDER,
                            severity=IncidentSeverity.CRITICAL,
                            summary=(
                                f"AXEL order {record.client_order_id} is {record.state.value} "
                                f"but broker still reports it open."
                            ),
                            client_order_id=record.client_order_id,
                            broker_order_id=record.broker_order_id,
                            symbol=record.symbol,
                        )
                    )
                continue

            if record.state in IN_FLIGHT_STATES:
                broker_order = self._find_broker_order(record, broker_open_orders)
                if broker_order is None:
                    # 2. Submitted/unknown but broker cannot find it.
                    mismatches.append(
                        ReconMismatch(
                            case=ReconReason.MISSING_BROKER_ORDER,
                            category=IncidentCategory.UNKNOWN_BROKER_ORDER,
                            severity=IncidentSeverity.CRITICAL,
                            summary=(
                                f"AXEL order {record.client_order_id} is {record.state.value} "
                                f"but the broker has no such order."
                            ),
                            client_order_id=record.client_order_id,
                            symbol=record.symbol,
                        )
                    )
                else:
                    # 7. Internally cancelled/patched but broker still open.
                    if record.state is OrderState.CANCEL_PENDING and broker_order.state not in TERMINAL_STATES:
                        mismatches.append(
                            ReconMismatch(
                                case=ReconReason.CANCELLATION_NOT_REFLECTED,
                                category=IncidentCategory.UNKNOWN_BROKER_ORDER,
                                severity=IncidentSeverity.WARNING,
                                summary=(
                                    f"Cancellation of {record.client_order_id} is internal but "
                                    f"broker order remains {broker_order.state.value}."
                                ),
                                client_order_id=record.client_order_id,
                                broker_order_id=broker_order.broker_order_id,
                                symbol=record.symbol,
                            )
                        )
                    # 5. Fill quantity the broker reports exceeds what AXEL derived.
                    if record.filled_qty > broker_order.filled_qty:
                        mismatches.append(
                            ReconMismatch(
                                case=ReconReason.FILL_QTY_EXCEEDS_BROKER,
                                category=IncidentCategory.UNEXPECTED_FILL,
                                severity=IncidentSeverity.CRITICAL,
                                summary=(
                                    f"AXEL fill qty {record.filled_qty} for "
                                    f"{record.client_order_id} exceeds broker {broker_order.filled_qty}."
                                ),
                                client_order_id=record.client_order_id,
                                broker_order_id=broker_order.broker_order_id,
                                symbol=record.symbol,
                                expected=str(broker_order.filled_qty),
                                observed=str(record.filled_qty),
                            )
                        )

        # 3/10. Broker fills AXEL has not recorded (dedup by identity).
        seen_fill_keys: set[str] = set()
        for fill in broker_fills:
            key = self._fill_key(fill)
            if key in seen_fill_keys:
                mismatches.append(
                    ReconMismatch(
                        case=ReconReason.DUPLICATE_FILL,
                        category=IncidentCategory.DUPLICATE_SUBMISSION,
                        severity=IncidentSeverity.WARNING,
                        summary=f"Broker returned a duplicate fill {fill.broker_fill_id}.",
                        broker_order_id=fill.broker_order_id,
                        symbol=fill.symbol,
                    )
                )
                continue
            seen_fill_keys.add(key)
            record = None
            if fill.client_order_id:
                record = axel_by_client.get(fill.client_order_id)
            if record is None and fill.broker_order_id:
                record = axel_by_broker.get(fill.broker_order_id)
            if record is None or record.filled_qty < to_decimal(fill.qty):
                mismatches.append(
                    ReconMismatch(
                        case=ReconReason.UNRECORDED_FILL,
                        category=IncidentCategory.UNEXPECTED_FILL,
                        severity=IncidentSeverity.CRITICAL,
                        summary=(
                            f"Broker fill {fill.broker_fill_id} on {fill.symbol} "
                            f"(qty {fill.qty}) is not fully recorded by AXEL."
                        ),
                        client_order_id=fill.client_order_id,
                        broker_order_id=fill.broker_order_id,
                        symbol=fill.symbol,
                        observed=str(fill.qty),
                    )
                )

        # 4/6. Positions and cash derived from the AXEL ledger.
        broker_position_qty = {p.symbol: to_decimal(p.qty) for p in broker_positions}
        for symbol in set(broker_position_qty) | set(fill_derived_positions):
            derived = to_decimal(fill_derived_positions.get(symbol, 0))
            observed = broker_position_qty.get(symbol, Decimal(0))
            if derived != observed:
                mismatches.append(
                    ReconMismatch(
                        case=ReconReason.POSITION_MISMATCH,
                        category=IncidentCategory.POSITION_MISMATCH,
                        severity=IncidentSeverity.CRITICAL,
                        summary=(
                            f"Position for {symbol}: AXEL ledger {derived} vs broker {observed}."
                        ),
                        symbol=symbol,
                        expected=str(derived),
                        observed=str(observed),
                    )
                )

        if broker_account is not None:
            if axel_cash is not None and to_decimal(axel_cash) != broker_account.cash:
                mismatches.append(
                    ReconMismatch(
                        case=ReconReason.CASH_MISMATCH,
                        category=IncidentCategory.CASH_MISMATCH,
                        severity=IncidentSeverity.CRITICAL,
                        summary=(
                            f"Cash mismatch: AXEL {axel_cash} vs broker {broker_account.cash}."
                        ),
                        expected=str(axel_cash),
                        observed=str(broker_account.cash),
                    )
                )
            if (
                axel_buying_power is not None
                and to_decimal(axel_buying_power) != broker_account.buying_power
            ):
                mismatches.append(
                    ReconMismatch(
                        case=ReconReason.CASH_MISMATCH,
                        category=IncidentCategory.CASH_MISMATCH,
                        severity=IncidentSeverity.CRITICAL,
                        summary=(
                            f"Buying power mismatch: AXEL {axel_buying_power} vs broker "
                            f"{broker_account.buying_power}."
                        ),
                        expected=str(axel_buying_power),
                        observed=str(broker_account.buying_power),
                    )
                )

        incidents = self._record_incidents(mismatches)
        if mismatches:
            logger.warning(
                "RECONCILIATION BREAK DETECTED.",
                extra={"reason_codes": [m.case.value for m in mismatches]},
            )
        return ReconciliationResult(
            as_of=now,
            mismatches=mismatches,
            incidents=incidents,
            snapshot_age_seconds=age,
            broker_account_id=broker_account.account_id if broker_account else None,
        )

    # ── Internals ───────────────────────────────────────────────────────────
    def _record_incidents(self, mismatches: list[ReconMismatch]) -> list[Incident]:
        incidents: list[Incident] = []
        for mismatch in mismatches:
            incidents.append(
                self.incidents.open_or_update(
                    category=mismatch.category,
                    reason_code=mismatch.case.value,
                    summary=mismatch.summary,
                    dedup_key=mismatch.dedup_key,
                    severity=mismatch.severity,
                    client_order_id=mismatch.client_order_id,
                    broker_order_id=mismatch.broker_order_id,
                    symbol=mismatch.symbol,
                    evidence={
                        "expected": mismatch.expected,
                        "observed": mismatch.observed,
                    },
                )
            )
        return incidents

    def _alert(self, result: ReconciliationResult) -> None:
        if self.alert_callback:
            ghosts = [m.broker_order_id or "" for m in result.mismatches if m.case is ReconReason.BROKER_ORDER_WITHOUT_INTENT]
            unmatched = [m.client_order_id or "" for m in result.mismatches if m.case is ReconReason.MISSING_BROKER_ORDER]
            self.alert_callback(reconciliation_alert(ghosts, unmatched))

    @staticmethod
    def _find_broker_order(record: OrderRecord, broker_open_orders: list[BrokerOrder]) -> BrokerOrder | None:
        for broker_order in broker_open_orders:
            if record.client_order_id and broker_order.client_order_id == record.client_order_id:
                return broker_order
            if record.broker_order_id and broker_order.broker_order_id == record.broker_order_id:
                return broker_order
        return None

    @staticmethod
    def _fill_key(fill: BrokerFill) -> str:
        if fill.broker_fill_id:
            return f"id|{fill.broker_fill_id}"
        event_time = fill.event_time.isoformat() if fill.event_time else ""
        return f"tuple|{fill.broker_order_id}|{fill.symbol}|{fill.side.value}|{fill.qty}|{fill.price}|{event_time}"
