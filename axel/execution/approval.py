"""
Human-In-The-Loop (HITL) approval gateway with strict TTL expiration.
Proposals in live mode (or paper graduation soak) must be explicitly approved before execution.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from axel.core.clock import Clock, RealClock
from axel.core.contracts import Proposal, RiskDecision
from axel.core.logging import logger
from axel.core.types import ProposalStatus, RiskVerdict, Section


@dataclass
class ApprovalTicket:
    proposal: Proposal
    risk_decision: RiskDecision
    submitted_at: datetime
    expires_at: datetime
    status: ProposalStatus
    operator_id: Optional[str] = None
    decision_reason: Optional[str] = None


class ApprovalService:
    """
    Manages pending human approvals with automatic TTL expiration.
    Default TTL:
      - Stocks (market hours): 300 seconds (5 min)
      - Crypto: 120 seconds (2 min)
      - Predictions: 1800 seconds (30 min)
      - Options: 600 seconds (10 min)
    """

    DEFAULT_TTLS = {
        Section.STOCKS: 300,
        Section.CRYPTO: 120,
        Section.PREDICTIONS: 1800,
        Section.OPTIONS: 600,
    }

    def __init__(self, clock: Optional[Clock] = None):
        self.clock = clock or RealClock()
        self._tickets: Dict[str, ApprovalTicket] = {}

    def submit_for_approval(
        self,
        proposal: Proposal,
        risk_decision: RiskDecision,
        custom_ttl_seconds: Optional[int] = None,
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

    def sweep_expired(self) -> List[ApprovalTicket]:
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

    def get_pending(self) -> List[ApprovalTicket]:
        """Returns all unexpired, pending tickets."""
        self.sweep_expired()
        return [t for t in self._tickets.values() if t.status == ProposalStatus.PENDING]
