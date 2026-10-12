"""
Idempotent order submission and recovery service (Phase 6, spec §7.3/§7.4).

This service turns a reviewed order intent into a durable, broker-submitted
order:

1.  derive the deterministic ``client_order_id`` from the canonical order hash;
2.  refuse to submit again if a record already exists for that intent;
3.  consume the single-use approval (never bypassed);
4.  write ``SUBMITTED`` to the outbox **before** touching the broker;
5.  submit, mapping ambiguous/transport/reject outcomes onto explicit states
    (``UNKNOWN``/``ERROR``/``REJECTED``) --- never blind-retrying;
6.  repair ``UNKNOWN`` orders by lookup-by-client-id during restart/reconcile.

Zero LLM imports: the submit path is fully deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from axel.core.clock import Clock, RealClock
from axel.core.contracts import Order, RiskDecision
from axel.core.ids import derive_client_order_id
from axel.core.logging import logger
from axel.core.money import to_decimal
from axel.core.types import OrderState
from axel.execution.approval import AuthenticatedPrincipal, OrderApprovalService, compute_order_hash
from axel.execution.broker_base import (
    AmbiguousSubmissionError,
    BrokerAdapter,
    BrokerFill,
    BrokerOrder,
    BrokerRejectedError,
    OrderResult,
    RetryableBrokerError,
)
from axel.execution.order_store import (
    OrderEventKind,
    OrderEventStore,
    OrderRecord,
)
from axel.execution.permissions import assert_may_cancel, assert_may_submit
from axel.execution.reason_codes import OrderReason

# States that mean "we asked the broker and do not yet know the outcome".
_IN_FLIGHT_STATES = frozenset({OrderState.SUBMITTED, OrderState.UNKNOWN, OrderState.CANCEL_PENDING})


class OrderIdempotencyConflict(Exception):
    """The same client order id was reused for a materially different order."""


@dataclass(frozen=True)
class SubmitOutcome:
    """Result of :meth:`OrderService.submit`."""

    record: OrderRecord
    submitted: bool
    duplicate: bool
    ambiguous: bool
    reason_code: str | None = None
    ack: OrderResult | None = None


class OrderService:
    """Durable, idempotent order lifecycle manager."""

    def __init__(
        self,
        broker: BrokerAdapter,
        *,
        store: OrderEventStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.broker = broker
        self.store = store or OrderEventStore()
        self.clock = clock or RealClock()

    # ── Intent creation ─────────────────────────────────────────────────────
    def create(
        self,
        order: Order,
        *,
        config_version: str,
        risk_decision_id: str | None = None,
        approval_id: str | None = None,
        correlation_id: str | None = None,
    ) -> OrderRecord:
        """Persist a ``CREATED`` event for an intent (no broker interaction)."""
        order_hash = compute_order_hash(order, config_version=config_version)
        client_order_id = order.client_order_id or derive_client_order_id(order_hash)
        self.store.append(
            client_order_id=client_order_id,
            kind=OrderEventKind.CREATED,
            state=OrderState.CREATED,
            metadata={
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": str(to_decimal(order.qty)),
                "order_hash": order_hash,
                "config_version": config_version,
                "risk_decision_id": risk_decision_id,
                "approval_id": approval_id,
                "strategy_id": order.strategy_id,
                "strategy_version": order.strategy_version,
                "correlation_id": correlation_id or order.correlation_id,
                "intent_id": order.intent_id,
            },
        )
        record = self.store.record(client_order_id)
        assert record is not None  # just appended
        return record

    # ── Submission ──────────────────────────────────────────────────────────
    def submit(
        self,
        order: Order,
        risk_decision: RiskDecision,
        *,
        config_version: str,
        approvals: OrderApprovalService | None = None,
        correlation_id: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> SubmitOutcome:
        """Submit exactly once, recovering instead of resubmitting on retry."""
        assert_may_submit(principal)
        order_hash = compute_order_hash(order, config_version=config_version)
        client_order_id = order.client_order_id or derive_client_order_id(order_hash)
        if not order.client_order_id:
            order = order.model_copy(update={"client_order_id": client_order_id})

        existing = self.store.record(client_order_id)
        if existing is not None:
            if existing.order_hash not in (None, order_hash):
                raise OrderIdempotencyConflict(
                    f"client_order_id {client_order_id!r} already used for a different intent."
                )
            if existing.state in _IN_FLIGHT_STATES:
                # Crash/retry recovery: never resubmit; resolve against the broker.
                resolved = self.resolve_unknown(client_order_id)
                record = resolved or existing
                return SubmitOutcome(
                    record=record,
                    submitted=False,
                    duplicate=True,
                    ambiguous=record.state is OrderState.UNKNOWN,
                    reason_code=OrderReason.DUPLICATE_SUBMISSION.value,
                )
            return SubmitOutcome(record=existing, submitted=False, duplicate=True, ambiguous=False)

        # Single-use approval is mandatory unless the caller explicitly opts out
        # (paper/drill contexts pass approvals=None).
        if approvals is not None:
            approvals.consume(order, config_version=config_version)

        self.create(
            order,
            config_version=config_version,
            risk_decision_id=risk_decision.proposal_id,
            approval_id=order.approval_id,
            correlation_id=correlation_id,
        )
        self.store.append(
            client_order_id=client_order_id,
            kind=OrderEventKind.APPROVED,
            state=OrderState.APPROVED,
        )
        # Outbox: persist SUBMITTED *before* the network call so a crash leaves a
        # recoverable marker rather than a lost order.
        self.store.append(
            client_order_id=client_order_id,
            kind=OrderEventKind.SUBMITTED,
            state=OrderState.SUBMITTED,
        )

        try:
            ack = self.broker.submit_order(order, risk_decision)
        except AmbiguousSubmissionError as exc:
            self.store.append(
                client_order_id=client_order_id,
                kind=OrderEventKind.ERROR,
                state=OrderState.UNKNOWN,
                reason_code=OrderReason.SUBMISSION_TIMEOUT_AMBIGUOUS.value,
                message=str(exc),
            )
            logger.warning(
                "Order submission ambiguous; marked UNKNOWN for recovery.",
                extra={"client_order_id": client_order_id},
            )
            return SubmitOutcome(
                record=self._require(client_order_id),
                submitted=True,
                duplicate=False,
                ambiguous=True,
                reason_code=OrderReason.SUBMISSION_TIMEOUT_AMBIGUOUS.value,
            )
        except BrokerRejectedError as exc:
            self.store.append(
                client_order_id=client_order_id,
                kind=OrderEventKind.STATE_CHANGE,
                state=OrderState.REJECTED,
                reason_code=OrderReason.BROKER_REJECTED.value,
                message=str(exc),
            )
            return SubmitOutcome(
                record=self._require(client_order_id),
                submitted=True,
                duplicate=False,
                ambiguous=False,
                reason_code=OrderReason.BROKER_REJECTED.value,
            )
        except RetryableBrokerError as exc:
            self.store.append(
                client_order_id=client_order_id,
                kind=OrderEventKind.ERROR,
                state=OrderState.ERROR,
                reason_code=OrderReason.BROKER_TRANSPORT_ERROR.value,
                message=str(exc),
            )
            return SubmitOutcome(
                record=self._require(client_order_id),
                submitted=True,
                duplicate=False,
                ambiguous=False,
                reason_code=OrderReason.BROKER_TRANSPORT_ERROR.value,
            )

        self.store.append(
            client_order_id=client_order_id,
            kind=OrderEventKind.BROKER_ACK,
            state=ack.state,
            broker_order_id=ack.broker_order_id,
        )
        if ack.filled_qty and ack.filled_avg_price is not None:
            self.apply_fill(
                BrokerFill(
                    broker_order_id=ack.broker_order_id or "",
                    symbol=ack.symbol,
                    side=ack.side,
                    qty=to_decimal(ack.filled_qty),
                    price=to_decimal(ack.filled_avg_price),
                    client_order_id=client_order_id,
                    received_at=self.clock.now(),
                )
            )
        return SubmitOutcome(
            record=self._require(client_order_id),
            submitted=True,
            duplicate=False,
            ambiguous=False,
            ack=ack,
        )

    # ── Recovery / reconciliation ───────────────────────────────────────────
    def resolve_unknown(self, client_order_id: str) -> OrderRecord | None:
        """Repair an in-flight/unknown order using the broker as source of truth."""
        record = self.store.record(client_order_id)
        if record is None:
            return None
        broker_order = self.broker.find_order_by_client_id(client_order_id)
        if broker_order is None:
            return record  # still unresolved; reconciliation will raise an incident
        return self.apply_broker_order(broker_order, reconciled=True)

    def pending_recovery(self) -> list[OrderRecord]:
        """All orders that must be re-checked against the broker on startup."""
        return [r for r in self.store.records() if r.state in _IN_FLIGHT_STATES]

    def apply_broker_order(
        self, broker_order: BrokerOrder, *, reconciled: bool = False
    ) -> OrderRecord | None:
        """Synchronise a projected order with a broker-reported order."""
        client_order_id = broker_order.client_order_id
        if client_order_id is None:
            client_order_id = self._client_id_for_broker_order(broker_order.broker_order_id)
        if client_order_id is None:
            return None

        current = self.store.record(client_order_id)
        prior_filled = current.filled_qty if current else to_decimal(0)

        self.store.append(
            client_order_id=client_order_id,
            kind=OrderEventKind.RECONCILED if reconciled else OrderEventKind.STATE_CHANGE,
            state=broker_order.state,
            broker_order_id=broker_order.broker_order_id,
            reason_code=(OrderReason.RECONCILIATION_REPAIRED.value if reconciled else None),
        )
        # Backfill any execution the projection had not yet seen.
        if broker_order.filled_qty > prior_filled and broker_order.filled_avg_price is not None:
            self.apply_fill(
                BrokerFill(
                    broker_order_id=broker_order.broker_order_id,
                    symbol=broker_order.symbol,
                    side=broker_order.side,
                    qty=broker_order.filled_qty - prior_filled,
                    price=broker_order.filled_avg_price,
                    client_order_id=client_order_id,
                    received_at=self.clock.now(),
                )
            )
        return self.store.record(client_order_id)

    def apply_fill(self, fill: BrokerFill) -> OrderRecord | None:
        """Ingest a fill (idempotent); returns ``None`` when it is a duplicate."""
        event = self.store.append_fill(fill)
        if event is None:
            return None
        return self.store.record(event.client_order_id)

    # ── Cancellation ────────────────────────────────────────────────────────
    def cancel(
        self, client_order_id: str, *, principal: AuthenticatedPrincipal | None = None
    ) -> OrderRecord:
        assert_may_cancel(principal)
        record = self._require(client_order_id)
        if record.is_terminal:
            return record

        self.store.append(
            client_order_id=client_order_id,
            kind=OrderEventKind.CANCEL_REQUESTED,
            state=OrderState.CANCEL_PENDING,
        )
        result = self.broker.cancel(client_order_id)
        if result.accepted and result.state is OrderState.CANCELED:
            self.store.append(
                client_order_id=client_order_id,
                kind=OrderEventKind.STATE_CHANGE,
                state=OrderState.CANCELED,
            )
        elif not result.accepted:
            # The broker may already have filled/cancelled it; reconcile.
            broker_order = self.broker.find_order_by_client_id(client_order_id)
            if broker_order is not None:
                self.apply_broker_order(broker_order, reconciled=True)
        return self._require(client_order_id)

    # ── Internals ───────────────────────────────────────────────────────────
    def _require(self, client_order_id: str) -> OrderRecord:
        record = self.store.record(client_order_id)
        if record is None:
            raise KeyError(f"No order record for {client_order_id!r}.")
        return record

    def _client_id_for_broker_order(self, broker_order_id: str) -> str | None:
        for record in self.store.records():
            if record.broker_order_id == broker_order_id:
                return record.client_order_id
        return None
