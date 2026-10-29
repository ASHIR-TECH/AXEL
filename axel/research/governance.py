"""Governance and promotion control for Phase 10.

Crucial Architectural Constraint:
The QRA is an untrusted research subsystem. It governs research candidate lifecycle
ONLY up to PROMOTION_REVIEW. It contains NO live trading promotion authority or API.
Live trading authorization is exclusively the domain of the deterministic trading core.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class PromotionState(StrEnum):
    RESEARCH = "research"
    EVALUATED = "evaluated"
    REVIEW_PENDING = "review_pending"
    APPROVED_FOR_PAPER = "approved_for_paper"
    PAPER_VALIDATION = "paper_validation"
    PROMOTION_REVIEW = "promotion_review"
    REJECTED = "rejected"
    ARCHIVED = "archived"


ORDERED_PROMOTION_FLOW = [
    PromotionState.RESEARCH,
    PromotionState.EVALUATED,
    PromotionState.REVIEW_PENDING,
    PromotionState.APPROVED_FOR_PAPER,
    PromotionState.PAPER_VALIDATION,
    PromotionState.PROMOTION_REVIEW,
]

STATE_EVIDENCE_REQUIREMENTS: dict[PromotionState, list[str]] = {
    PromotionState.EVALUATED: [
        "hypothesis_registered",
        "point_in_time_validated",
    ],
    PromotionState.REVIEW_PENDING: [
        "baselines_compared",
        "walk_forward_completed",
        "costs_modeled",
        "stats_reviewed",
        "report_complete",
    ],
    PromotionState.APPROVED_FOR_PAPER: [
        "independent_review",
    ],
    PromotionState.PAPER_VALIDATION: [
        "paper_deployment_configured",
    ],
    PromotionState.PROMOTION_REVIEW: [
        "paper_soak_passed",
        "risk_limits_intact",
    ],
}


@dataclass(frozen=True)
class GovernanceGate:
    state: PromotionState = PromotionState.RESEARCH
    satisfied_evidence: set[str] = field(default_factory=set)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)

    def add_evidence(self, evidence: set[str] | list[str] | str, actor: str = "system") -> GovernanceGate:
        items = {evidence} if isinstance(evidence, str) else set(evidence)
        new_sat = self.satisfied_evidence | items
        audit_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "add_evidence",
            "items": sorted(items),
            "state": self.state.value,
        }
        return replace(self, satisfied_evidence=new_sat, audit_trail=self.audit_trail + [audit_entry])

    def check_prerequisites(self, target_state: PromotionState) -> tuple[bool, list[str]]:
        reqs = STATE_EVIDENCE_REQUIREMENTS.get(target_state, [])
        missing = [r for r in reqs if r not in self.satisfied_evidence]
        return (len(missing) == 0, missing)

    def advance(self, actor: str = "system") -> GovernanceGate:
        if self.state in (PromotionState.REJECTED, PromotionState.ARCHIVED, PromotionState.PROMOTION_REVIEW):
            return self

        try:
            curr_idx = ORDERED_PROMOTION_FLOW.index(self.state)
        except ValueError:
            return self

        next_state = ORDERED_PROMOTION_FLOW[curr_idx + 1]
        ok, missing = self.check_prerequisites(next_state)
        if not ok:
            raise ValueError(
                f"Cannot advance research state from '{self.state.value}' to '{next_state.value}'. "
                f"Missing required evidence: {missing}"
            )

        audit_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "state_transition",
            "from_state": self.state.value,
            "to_state": next_state.value,
        }
        return replace(self, state=next_state, audit_trail=self.audit_trail + [audit_entry])

    def reject(self, reason: str, actor: str = "reviewer") -> GovernanceGate:
        audit_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "reject",
            "reason": reason,
            "previous_state": self.state.value,
        }
        return replace(self, state=PromotionState.REJECTED, audit_trail=self.audit_trail + [audit_entry])

    def archive(self, reason: str, actor: str = "system") -> GovernanceGate:
        audit_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "archive",
            "reason": reason,
            "previous_state": self.state.value,
        }
        return replace(self, state=PromotionState.ARCHIVED, audit_trail=self.audit_trail + [audit_entry])
