"""Research evidence report generation for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Recommendation(StrEnum):
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"
    CONTINUE_RESEARCH = "continue_research"
    SUBMIT_FOR_REVIEW = "submit_for_review"
    PAPER_VALIDATION_CANDIDATE = "paper_validation_candidate"


@dataclass(frozen=True)
class ResearchReport:
    report_id: str
    run_id: str
    hypothesis_id: str
    hypothesis_version: int
    executive_summary: str = ""
    hypothesis_mechanism: str = ""
    dataset_lineage: list[dict[str, Any]] = field(default_factory=list)
    feature_definitions: list[dict[str, Any]] = field(default_factory=list)
    experiment_config: dict[str, Any] = field(default_factory=dict)
    baseline_results: dict[str, Any] = field(default_factory=dict)
    candidate_results: dict[str, Any] = field(default_factory=dict)
    costs_and_execution: dict[str, Any] = field(default_factory=dict)
    risk_results: dict[str, Any] = field(default_factory=dict)
    statistical_results: dict[str, Any] = field(default_factory=dict)
    robustness_results: dict[str, Any] = field(default_factory=dict)
    failure_cases: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    unsupported_assumptions: list[str] = field(default_factory=list)
    reproduction_instructions: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    code_refs: list[str] = field(default_factory=list)
    recommendation: Recommendation = Recommendation.INCONCLUSIVE
    required_reviewer_decisions: list[str] = field(default_factory=list)
    report_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build_research_report(report_id: str, run_id: str, hypothesis_id: str, hypothesis_version: int, **kwargs: Any) -> ResearchReport:
    return ResearchReport(report_id=report_id, run_id=run_id, hypothesis_id=hypothesis_id, hypothesis_version=hypothesis_version, **kwargs)
