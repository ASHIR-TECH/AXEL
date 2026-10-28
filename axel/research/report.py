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
    """Standardized Research Evidence Report covering all 18 requirements of PRD §6.8."""

    # 1. Identifiers & Metadata
    report_id: str
    run_id: str
    hypothesis_id: str
    hypothesis_version: int

    # 2. Required Content Fields (PRD §6.8 Items 1-18)
    executive_summary: str = ""
    hypothesis_mechanism: str = ""
    dataset_lineage: list[dict[str, Any]] = field(default_factory=list)
    feature_definitions: list[dict[str, Any]] = field(default_factory=list)
    experiment_config: dict[str, Any] = field(default_factory=dict)
    baseline_results: dict[str, Any] = field(default_factory=dict)
    validation_design: dict[str, Any] = field(default_factory=dict)
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

    def __post_init__(self) -> None:
        # Mandatory disclaimer enforcement per PRD §1 & §6.8
        mandatory_disclaimer = "Historical performance and backtest metrics do not guarantee future profitability."
        if mandatory_disclaimer not in self.limitations:
            object.__setattr__(self, "limitations", list(self.limitations) + [mandatory_disclaimer])

    def to_markdown(self) -> str:
        """Render evidence report to Markdown for pull request attachment or audit archiving."""
        md = [
            f"# AXEL Research Evidence Report: {self.report_id}",
            f"- **Run ID:** `{self.run_id}`",
            f"- **Hypothesis:** `{self.hypothesis_id}` (v{self.hypothesis_version})",
            f"- **Recommendation:** `{self.recommendation.value}`",
            f"- **Generated:** {self.created_at.isoformat()}",
            "",
            "## 1. Executive Summary",
            self.executive_summary or "No executive summary provided.",
            "",
            "## 2. Hypothesis & Proposed Mechanism",
            self.hypothesis_mechanism or "None specified.",
            "",
            "## 3. Performance & Baseline Comparisons",
            f"- Candidate Sharpe: {self.candidate_results.get('sharpe', 'N/A')}",
            f"- Candidate Return: {self.candidate_results.get('expectancy_r', self.candidate_results.get('return', 'N/A'))}",
            f"- Max Drawdown: {self.candidate_results.get('max_drawdown', 'N/A')}",
            "",
            "## 4. Statistical Reliability Assessment",
            f"- Deflated Sharpe Ratio (DSR): {self.statistical_results.get('dsr', 'N/A')}",
            f"- Multiple-Testing Adjusted: {self.statistical_results.get('multiple_testing', 'N/A')}",
            "",
            "## 5. Risk, Robustness & Limitations",
            "### Limitations",
        ]
        for lim in self.limitations:
            md.append(f"- {lim}")
        md.extend([
            "",
            "### Required Reviewer Decisions",
        ])
        for dec in self.required_reviewer_decisions:
            md.append(f"- [ ] {dec}")
        return "\n".join(md)


def build_research_report(
    report_id: str,
    run_id: str,
    hypothesis_id: str,
    hypothesis_version: int,
    **kwargs: Any,
) -> ResearchReport:
    return ResearchReport(
        report_id=report_id,
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        hypothesis_version=hypothesis_version,
        **kwargs,
    )
