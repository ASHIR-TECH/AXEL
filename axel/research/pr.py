"""Code change proposal with restricted path enforcement for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

RESTRICTED_PATH_PREFIXES = (
    "axel/core/",
    "axel/execution/",
    "axel/risk/",
    "axel/watchdog/",
    "config/production/",
    ".github/workflows/",
)


def is_restricted_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip().lower()
    return any(normalized.startswith(prefix.lower()) for prefix in RESTRICTED_PATH_PREFIXES)


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

    def __post_init__(self) -> None:
        # Automated audit of files changed against restricted paths
        has_restricted = any(is_restricted_path(f) for f in self.files_changed)
        if has_restricted:
            object.__setattr__(self, "restricted_paths_changed", True)

    def approve(self, approver: str) -> "CodeChangeProposal":
        # Security rule per PRD §2.2 & §6.9: QRA cannot approve its own proposal
        if approver.strip().lower() == self.created_by.strip().lower():
            raise PermissionError(
                f"Creator '{self.created_by}' cannot self-approve proposal '{self.proposal_id}'. "
                "Independent reviewer approval is required."
            )
        new_approvals = list(self.approvals)
        if approver not in new_approvals:
            new_approvals.append(approver)
        return replace(self, approvals=new_approvals, approval_state=ProposalStatus.APPROVED)

    def can_merge(self) -> bool:
        if self.approval_state != ProposalStatus.APPROVED:
            return False
        if self.restricted_paths_changed:
            return False
        if not self.confirmation_restricted_not_modified:
            return False
        if not self.approvals:
            return False
        # Security invariant: independent approval required
        if any(a.strip().lower() == self.created_by.strip().lower() for a in self.approvals):
            return False
        return True


def submit_proposal(proposal: CodeChangeProposal) -> CodeChangeProposal:
    if not proposal.summary or not proposal.intended_behavior_change:
        raise ValueError("Proposal must specify a summary and intended behavior change.")
    if not proposal.test_evidence_ref:
        raise ValueError("Proposal must link test evidence reference before submission.")
    return replace(proposal, approval_state=ProposalStatus.PENDING_REVIEW)
