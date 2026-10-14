"""
Human-In-The-Loop (HITL) approval.

Two services live here:

* :class:`ApprovalService` --- the original Phase 4 proposal-level approval
  queue with TTL expiration. Retained for backwards compatibility.
* :class:`OrderApprovalService` --- the Phase 6 canonical approval. It is
  authenticated, expiring, single-use, and bound to the *exact* order hash and
  live-configuration version, per
  `docs/AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md` §6.4 and §8.2.

Zero LLM imports: this module lives on the deterministic side of the trust
boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import ClassVar

from axel.core.clock import Clock, RealClock
from axel.core.contracts import Order, Proposal, RiskDecision
from axel.core.logging import logger
from axel.core.money import to_decimal
from axel.core.types import ProposalStatus, RiskVerdict, Section
from axel.execution.reason_codes import ApprovalReason


class PrincipalKind(str, Enum):
    """What kind of actor is requesting or approving an order."""

    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """
    An authenticated actor.

    ``kind`` is structural: only :attr:`PrincipalKind.HUMAN` may approve an
    order, so an agent can never approve its own proposal even if it can reach
    the approval API.
    """

    principal_id: str
    kind: PrincipalKind
    auth_reference: str


class ApprovalError(Exception):
    """Raised when an approval cannot be issued, consumed or verified."""

    def __init__(self, reason: ApprovalReason, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason.value)


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


def canonical_order_payload(order: Order, *, config_version: str) -> dict[str, str | None]:
    """
    Deterministic, JSON-safe projection of every order field that must be
    covered by an approval. Material changes to any of these invalidate the
    approval (spec §6.4).
    """
    return {
        "proposal_id": order.proposal_id,
        "intent_id": order.intent_id,
        "strategy_id": order.strategy_id,
        "strategy_version": order.strategy_version,
        "symbol": order.symbol,
        "side": order.side.value,
        "qty": str(to_decimal(order.qty)),
        "order_type": order.type.value,
        "limit_price": str(to_decimal(order.limit_price)) if order.limit_price is not None else None,
        "stop_price": str(to_decimal(order.stop_price)) if order.stop_price is not None else None,
        "time_in_force": order.time_in_force.value,
        "live_config_version": config_version,
    }


def compute_order_hash(order: Order, *, config_version: str) -> str:
    """
    Canonical SHA-256 hash of the order intent plus the live-config version.

    Stable across processes: the payload is JSON-serialised with sorted keys and
    a fixed separator set, so two calls with the same order always agree.
    """
    payload = canonical_order_payload(order, config_version=config_version)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class ApprovalRecord:
    """Canonical approval record (spec §6.4)."""

    approval_id: str
    order_hash: str
    config_version: str
    requested_by: str
    requested_at: datetime
    decision: ApprovalDecision | None = None
    approver: AuthenticatedPrincipal | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    reason: str | None = None
    consumed_at: datetime | None = None
    audit_reference: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.decision is None

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None


class OrderApprovalService:
    """
    Authenticated, expiring, single-use order approval registry.

    Approval is looked up by the exact ``(order_hash, config_version)`` pair, so
    an approval for one order version can never authorize another.
    """

    DEFAULT_TTL_SECONDS: ClassVar[int] = 300

    def __init__(self, clock: Clock | None = None):
        self.clock = clock or RealClock()
        self._records: dict[str, ApprovalRecord] = {}
        self._by_binding: dict[tuple[str, str], str] = {}

    # ── Requests ───────────────────────────────────────────────────────────
    def request(
        self,
        order: Order,
        *,
        config_version: str,
        requested_by: str,
    ) -> ApprovalRecord:
        """Register a pending approval request for an exact order version."""
        order_hash = compute_order_hash(order, config_version=config_version)
        approval_id = f"appr_{hashlib.sha256((order_hash + config_version).encode()).hexdigest()[:16]}"
        now = self.clock.now()
        record = ApprovalRecord(
            approval_id=approval_id,
            order_hash=order_hash,
            config_version=config_version,
            requested_by=requested_by,
            requested_at=now,
        )
        self._records[approval_id] = record
        # An existing binding is replaced only by a fresh *pending* request; a
        # consumed approval is never resurrected.
        self._by_binding[(order_hash, config_version)] = approval_id
        logger.info(
            "Order approval requested.",
            extra={"approval_id": approval_id, "order_hash": order_hash, "requested_by": requested_by},
        )
        return record

    # ── Decisions ──────────────────────────────────────────────────────────
    def approve(
        self,
        approval_id: str,
        approver: AuthenticatedPrincipal,
        *,
        reason: str = "Approved by human operator",
        ttl_seconds: int | None = None,
    ) -> ApprovalRecord:
        record = self._get_pending(approval_id)

        if approver.kind is not PrincipalKind.HUMAN:
            raise ApprovalError(
                ApprovalReason.UNAUTHENTICATED,
                f"Principal {approver.principal_id!r} is not an authenticated human.",
            )
        if not approver.principal_id.strip() or not approver.auth_reference.strip():
            raise ApprovalError(ApprovalReason.UNAUTHENTICATED, "Approver identity/reference is required.")
        if approver.principal_id == record.requested_by:
            raise ApprovalError(
                ApprovalReason.SELF_APPROVAL_FORBIDDEN,
                "A principal cannot approve its own request.",
            )

        now = self.clock.now()
        ttl = ttl_seconds or self.DEFAULT_TTL_SECONDS
        record.decision = ApprovalDecision.APPROVED
        record.approver = approver
        record.issued_at = now
        record.expires_at = now + timedelta(seconds=ttl)
        record.reason = reason
        record.audit_reference = approver.auth_reference
        logger.info(
            "Order approval granted.",
            extra={"approval_id": approval_id, "approver": approver.principal_id},
        )
        return record

    def reject(
        self,
        approval_id: str,
        approver: AuthenticatedPrincipal,
        *,
        reason: str,
    ) -> ApprovalRecord:
        record = self._get_pending(approval_id)
        if approver.kind is not PrincipalKind.HUMAN:
            raise ApprovalError(ApprovalReason.UNAUTHENTICATED, "Only an authenticated human may decide.")
        now = self.clock.now()
        record.decision = ApprovalDecision.REJECTED
        record.approver = approver
        record.issued_at = now
        record.expires_at = now
        record.reason = reason
        record.audit_reference = approver.auth_reference
        return record

    # ── Verification / consumption ─────────────────────────────────────────
    def verify(
        self,
        order: Order,
        *,
        config_version: str,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        """Non-mutating check that an unconsumed, unexpired approval exists."""
        record = self._resolve(order, config_version=config_version)
        self._assert_usable(record, now=now)
        return record

    def consume(
        self,
        order: Order,
        *,
        config_version: str,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        """Verify then mark the approval consumed; a second call fails."""
        record = self._resolve(order, config_version=config_version)
        self._assert_usable(record, now=now)
        record.consumed_at = now or self.clock.now()
        logger.info("Order approval consumed.", extra={"approval_id": record.approval_id})
        return record

    # ── Internals ──────────────────────────────────────────────────────────
    def _resolve(self, order: Order, *, config_version: str) -> ApprovalRecord:
        order_hash = compute_order_hash(order, config_version=config_version)
        approval_id = self._by_binding.get((order_hash, config_version))
        if approval_id is None:
            raise ApprovalError(
                ApprovalReason.NOT_FOUND,
                "No approval exists for this exact order hash and config version.",
            )
        return self._records[approval_id]

    def _assert_usable(self, record: ApprovalRecord, *, now: datetime | None = None) -> None:
        current = now or self.clock.now()
        if record.decision is None:
            raise ApprovalError(ApprovalReason.UNAUTHENTICATED, "Approval has no decision yet.")
        if record.decision is not ApprovalDecision.APPROVED:
            raise ApprovalError(ApprovalReason.REJECTED, "Approval was rejected.")
        if record.is_consumed:
            raise ApprovalError(ApprovalReason.ALREADY_CONSUMED, "Approval has already been consumed.")
        if record.expires_at is None or current >= record.expires_at:
            raise ApprovalError(ApprovalReason.EXPIRED, "Approval has expired.")

    def _get_pending(self, approval_id: str) -> ApprovalRecord:
        record = self._records.get(approval_id)
        if record is None:
            raise ApprovalError(ApprovalReason.NOT_FOUND, f"Approval {approval_id!r} not found.")
        if not record.is_pending:
            raise ApprovalError(
                ApprovalReason.ALREADY_CONSUMED,
                f"Approval {approval_id!r} already has a decision.",
            )
        return record


# ─────────────────────────────────────────────────────────────────────────────
# Legacy proposal-level approval queue (Phase 4). Retained unchanged in
# behaviour for backwards compatibility with existing callers and tests.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ApprovalTicket:
    proposal: Proposal
    risk_decision: RiskDecision
    submitted_at: datetime
    expires_at: datetime
    status: ProposalStatus
    operator_id: str | None = None
    decision_reason: str | None = None


class ApprovalService:
    """
    Manages pending human approvals with automatic TTL expiration.
    Default TTL:
      - Stocks (market hours): 300 seconds (5 min)
      - Crypto: 120 seconds (2 min)
      - Predictions: 1800 seconds (30 min)
      - Options: 600 seconds (10 min)
    """

    DEFAULT_TTLS: ClassVar[dict[Section, int]] = {
        Section.STOCKS: 300,
        Section.CRYPTO: 120,
        Section.PREDICTIONS: 1800,
        Section.OPTIONS: 600,
    }

    def __init__(self, clock: Clock | None = None):
        self.clock = clock or RealClock()
        self._tickets: dict[str, ApprovalTicket] = {}

    def submit_for_approval(
        self,
        proposal: Proposal,
        risk_decision: RiskDecision,
        custom_ttl_seconds: int | None = None,
    ) -> ApprovalTicket:
        """Submit an approved proposal into the human approval queue."""
        if not risk_decision.approved or risk_decision.verdict != RiskVerdict.APPROVED:
            raise ValueError(f"Cannot request approval for unapproved proposal (verdict: {risk_decision.verdict}).")

        now = self.clock.now()
        ttl = custom_ttl_seconds or self.DEFAULT_TTLS.get(proposal.section, 300)
        expires_at = now + timedelta(seconds=ttl)

        ticket = ApprovalTicket(
            proposal=proposal,
            risk_decision=risk_decision,
            submitted_at=now,
            expires_at=expires_at,
            status=ProposalStatus.PENDING,
        )
        self._tickets[proposal.id] = ticket

        logger.info(
            f"HITL Ticket Created: {proposal.symbol} ({proposal.section.value}) awaiting operator approval.",
            extra={"proposal_id": proposal.id, "ttl_seconds": ttl, "expires_at": expires_at.isoformat()},
        )
        return ticket

    def approve(self, proposal_id: str, operator_id: str) -> ApprovalTicket:
        """Operator explicitly approves execution before expiration."""
        self.sweep_expired()
        ticket = self._tickets.get(proposal_id)
        if not ticket:
            raise KeyError(f"Ticket '{proposal_id}' not found.")

        if ticket.status == ProposalStatus.EXPIRED:
            raise ValueError(f"Proposal '{proposal_id}' has already expired and cannot be approved.")

        if ticket.status != ProposalStatus.PENDING:
            raise ValueError(f"Proposal '{proposal_id}' is in status '{ticket.status.value}', cannot approve.")

        ticket.status = ProposalStatus.APPROVED
        ticket.operator_id = operator_id
        ticket.decision_reason = "Approved by human operator"

        logger.info(
            f"HITL Approved: Proposal {proposal_id} approved by operator {operator_id}.",
            extra={"proposal_id": proposal_id, "operator": operator_id},
        )
        return ticket

    def reject(self, proposal_id: str, operator_id: str, reason: str) -> ApprovalTicket:
        """Operator rejects proposal."""
        ticket = self._tickets.get(proposal_id)
        if not ticket:
            raise KeyError(f"Ticket '{proposal_id}' not found.")

        ticket.status = ProposalStatus.REJECTED
        ticket.operator_id = operator_id
        ticket.decision_reason = reason

        logger.info(
            f"HITL Rejected: Proposal {proposal_id} rejected by {operator_id}. Reason: {reason}",
            extra={"proposal_id": proposal_id, "reason": reason},
        )
        return ticket

    def sweep_expired(self) -> list[ApprovalTicket]:
        """Checks for expired tickets and transitions them to EXPIRED."""
        now = self.clock.now()
        expired_tickets = []
        for ticket in self._tickets.values():
            if ticket.status == ProposalStatus.PENDING and now >= ticket.expires_at:
                ticket.status = ProposalStatus.EXPIRED
                ticket.decision_reason = "TTL expired without operator action"
                expired_tickets.append(ticket)
                logger.warning(
                    f"HITL Ticket EXPIRED: Proposal {ticket.proposal.id} ({ticket.proposal.symbol}) exceeded TTL.",
                    extra={"proposal_id": ticket.proposal.id, "symbol": ticket.proposal.symbol},
                )
        return expired_tickets

    def get_pending(self) -> list[ApprovalTicket]:
        """Returns all unexpired, pending tickets."""
        self.sweep_expired()
        return [t for t in self._tickets.values() if t.status == ProposalStatus.PENDING]
