"""Code change proposal for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass(frozen=True)
class CodeChangeProposal:
    proposal_id: str
    report_id: str
    hypothesis_id: str
    experiment_run_id: str
    source_branch: str
    target_branch: str = "main"
    diff_ref: str = ""
    summary: str = ""
    intended_behavior_change: str = ""
    test_evidence_ref: str = ""
    files_changed: list[str] = field(default_factory=list)
    restricted_paths_changed: bool = False
    risk_assessment: str = ""
    rollback_plan: str = ""
    known_limitations: list[str] = field(default_factory=list)
    confirmation_restricted_not_modified: bool = False
    approval_state: ProposalStatus = ProposalStatus.DRAFT
    approvals: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_by: str = "qra"

    def can_merge(self) -> bool:
        if self.approval_state != ProposalStatus.APPROVED:
            return False
        if self.restricted_paths_changed:
            return False
        if not self.confirmation_restricted_not_modified:
            return False
        return True


def submit_proposal(proposal: CodeChangeProposal) -> CodeChangeProposal:
    return replace(proposal, approval_state=ProposalStatus.PENDING_REVIEW)
