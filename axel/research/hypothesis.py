"""Hypothesis registry for Phase 10 QRA."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from axel.core.ids import generate_id


class HypothesisStatus(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    RUNNING = "running"
    EVALUATED = "evaluated"
    ACCEPTED_FOR_REVIEW = "accepted_for_review"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


VALID_STATUS_TRANSITIONS: dict[HypothesisStatus, set[HypothesisStatus]] = {
    HypothesisStatus.DRAFT: {HypothesisStatus.REGISTERED, HypothesisStatus.CANCELLED},
    HypothesisStatus.REGISTERED: {HypothesisStatus.RUNNING, HypothesisStatus.CANCELLED, HypothesisStatus.INVALIDATED},
    HypothesisStatus.RUNNING: {HypothesisStatus.EVALUATED, HypothesisStatus.INCONCLUSIVE, HypothesisStatus.CANCELLED, HypothesisStatus.INVALIDATED},
    HypothesisStatus.EVALUATED: {HypothesisStatus.ACCEPTED_FOR_REVIEW, HypothesisStatus.REJECTED, HypothesisStatus.INCONCLUSIVE, HypothesisStatus.INVALIDATED},
    HypothesisStatus.ACCEPTED_FOR_REVIEW: {HypothesisStatus.REJECTED, HypothesisStatus.INCONCLUSIVE, HypothesisStatus.INVALIDATED},
    HypothesisStatus.REJECTED: set(),
    HypothesisStatus.INCONCLUSIVE: set(),
    HypothesisStatus.CANCELLED: set(),
    HypothesisStatus.INVALIDATED: set(),
}


@dataclass(frozen=True)
class HypothesisVersion:
    version: int
    title: str
    question: str
    changed_at: datetime
    changed_by: str
    change_reason: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    version: int
    title: str
    question: str
    section: str = "stocks"
    universe_spec_id: str = "default"
    economic_rationale: str = ""
    proposed_mechanism: str = ""
    expected_effect: str = ""
    target_definition: str = ""
    dataset_requirements: list[str] = field(default_factory=list)
    proposed_features: list[str] = field(default_factory=list)
    baseline_ids: list[str] = field(default_factory=list)
    primary_metric: str = "sharpe"
    secondary_metrics: list[str] = field(default_factory=list)
    acceptance_criteria: dict[str, Any] = field(default_factory=dict)
    max_drawdown_limit: float | None = None
    expected_holding_period: str | None = None
    cost_model: str = "default"
    known_limitations: list[str] = field(default_factory=list)
    research_owner: str = "qra"
    status: HypothesisStatus = HypothesisStatus.DRAFT
    linked_hypothesis_ids: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    versions: tuple[HypothesisVersion, ...] = ()

    def with_status(self, status: HypothesisStatus, force: bool = False) -> "Hypothesis":
        if not force and status != self.status:
            allowed = VALID_STATUS_TRANSITIONS.get(self.status, set())
            if status not in allowed:
                raise ValueError(
                    f"Illegal hypothesis transition: {self.status.value} -> {status.value}. "
                    f"Allowed transitions: {[s.value for s in allowed]}"
                )
        return replace(self, status=status)

    def link_duplicate(self, duplicate_of_id: str) -> "Hypothesis":
        linked = list(self.linked_hypothesis_ids)
        if duplicate_of_id not in linked:
            linked.append(duplicate_of_id)
        return replace(self, linked_hypothesis_ids=linked, duplicate_of=duplicate_of_id)

    def bump_version(self, changed_by: str, change_reason: str, **updates: Any) -> "Hypothesis":
        new_version = self.version + 1
        snapshot = {
            "title": self.title,
            "question": self.question,
            "section": self.section,
            "economic_rationale": self.economic_rationale,
            "proposed_mechanism": self.proposed_mechanism,
            "expected_effect": self.expected_effect,
            "target_definition": self.target_definition,
            "dataset_requirements": list(self.dataset_requirements),
            "proposed_features": list(self.proposed_features),
            "baseline_ids": list(self.baseline_ids),
            "primary_metric": self.primary_metric,
            "secondary_metrics": list(self.secondary_metrics),
            "acceptance_criteria": dict(self.acceptance_criteria),
            "max_drawdown_limit": self.max_drawdown_limit,
            "known_limitations": list(self.known_limitations),
        }
        version_record = HypothesisVersion(
            version=self.version,
            title=self.title,
            question=self.question,
            changed_at=datetime.now(UTC),
            changed_by=changed_by,
            change_reason=change_reason,
            snapshot=snapshot,
        )
        data = {
            "hypothesis_id": self.hypothesis_id,
            "version": new_version,
            "title": updates.get("title", self.title),
            "question": updates.get("question", self.question),
            "section": updates.get("section", self.section),
            "universe_spec_id": updates.get("universe_spec_id", self.universe_spec_id),
            "economic_rationale": updates.get("economic_rationale", self.economic_rationale),
            "proposed_mechanism": updates.get("proposed_mechanism", self.proposed_mechanism),
            "expected_effect": updates.get("expected_effect", self.expected_effect),
            "target_definition": updates.get("target_definition", self.target_definition),
            "dataset_requirements": updates.get("dataset_requirements", list(self.dataset_requirements)),
            "proposed_features": updates.get("proposed_features", list(self.proposed_features)),
            "baseline_ids": updates.get("baseline_ids", list(self.baseline_ids)),
            "primary_metric": updates.get("primary_metric", self.primary_metric),
            "secondary_metrics": updates.get("secondary_metrics", list(self.secondary_metrics)),
            "acceptance_criteria": updates.get("acceptance_criteria", dict(self.acceptance_criteria)),
            "max_drawdown_limit": updates.get("max_drawdown_limit", self.max_drawdown_limit),
            "expected_holding_period": updates.get("expected_holding_period", self.expected_holding_period),
            "cost_model": updates.get("cost_model", self.cost_model),
            "known_limitations": updates.get("known_limitations", list(self.known_limitations)),
            "research_owner": updates.get("research_owner", self.research_owner),
            "status": self.status,
            "linked_hypothesis_ids": list(self.linked_hypothesis_ids),
            "duplicate_of": self.duplicate_of,
            "created_at": self.created_at,
            "versions": self.versions + (version_record,),
        }
        return Hypothesis(**data)


class HypothesisRegistry:
    def __init__(self) -> None:
        self._hypotheses: dict[str, Hypothesis] = {}

    def create(self, **kwargs: Any) -> Hypothesis:
        h = Hypothesis(hypothesis_id=kwargs.pop("hypothesis_id", generate_id("hyp")), version=1, **kwargs)
        self._hypotheses[h.hypothesis_id] = h
        return h

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        return self._hypotheses.get(hypothesis_id)

    def register(self, hypothesis_id: str) -> Hypothesis:
        return self.transition(hypothesis_id, HypothesisStatus.REGISTERED)

    def transition(self, hypothesis_id: str, new_status: HypothesisStatus, force: bool = False) -> Hypothesis:
        h = self._hypotheses[hypothesis_id]
        updated = h.with_status(new_status, force=force)
        self._hypotheses[hypothesis_id] = updated
        return updated

    def link_duplicate(self, source_id: str, target_id: str) -> tuple[Hypothesis, Hypothesis]:
        h_source = self._hypotheses[source_id]
        h_target = self._hypotheses[target_id]
        updated_source = h_source.link_duplicate(target_id)
        updated_target = replace(
            h_target,
            linked_hypothesis_ids=list(set(h_target.linked_hypothesis_ids + [source_id])),
        )
        self._hypotheses[source_id] = updated_source
        self._hypotheses[target_id] = updated_target
        return updated_source, updated_target

    def list_all(self) -> list[Hypothesis]:
        return list(self._hypotheses.values())

    def query_by_status(self, status: HypothesisStatus) -> list[Hypothesis]:
        return [h for h in self._hypotheses.values() if h.status == status]

    def query_rejected(self) -> list[Hypothesis]:
        return self.query_by_status(HypothesisStatus.REJECTED)
