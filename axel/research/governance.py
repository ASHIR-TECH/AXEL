"""Governance and promotion control for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum


class PromotionState(StrEnum):
    RESEARCH = "research"
    EVALUATED = "evaluated"
    REVIEW_PENDING = "review_pending"
    APPROVED_FOR_PAPER = "approved_for_paper"
    PAPER_VALIDATION = "paper_validation"
    PROMOTION_REVIEW = "promotion_review"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class GovernanceGate:
    state: PromotionState = PromotionState.RESEARCH
    required_evidence: list[str] = field(default_factory=lambda: [
        "hypothesis_registered",
        "point_in_time_validated",
        "baselines_compared",
        "walk_forward_completed",
        "costs_modeled",
        "stats_reviewed",
        "report_complete",
        "independent_review",
    ])
    satisfied: set[str] = field(default_factory=set)
    blocked_reasons: list[str] = field(default_factory=list)
    can_promote_to_live: bool = False

    def check(self, evidence: set[str]) -> "GovernanceGate":
        sat = self.satisfied | evidence
        missing = [r for r in self.required_evidence if r not in sat]
        blocked = self.blocked_reasons.copy()
        if missing:
            blocked.append(f"missing_evidence: {missing}")
        can_live = (self.state == PromotionState.PROMOTION_REVIEW) and ("independent_approval" in sat) and (len(missing) == 0)
        return replace(self, satisfied=sat, blocked_reasons=blocked, can_promote_to_live=can_live)

    def advance(self) -> "GovernanceGate":
        order = [
            PromotionState.RESEARCH,
            PromotionState.EVALUATED,
            PromotionState.REVIEW_PENDING,
            PromotionState.APPROVED_FOR_PAPER,
            PromotionState.PAPER_VALIDATION,
            PromotionState.PROMOTION_REVIEW,
        ]
        try:
            idx = order.index(self.state)
            if idx < len(order) - 1:
                return replace(self, state=order[idx + 1])
        except ValueError:
            pass
        return self
