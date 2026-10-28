"""Integration tests for Quant Research Agent lifecycle and Report generation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from axel.data.models import Bar
from axel.research.qra import QuantResearchAgent
from axel.research.report import Recommendation


def _sample_bars(n: int = 60) -> list[Bar]:
    base_ts = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    bars = []
    price = 100.0
    for i in range(n):
        price = price + (1.2 if i % 2 == 0 else -0.5)
        bars.append(
            Bar(
                symbol="TEST",
                timestamp=base_ts + timedelta(days=i),
                open=price - 0.2,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=10_000.0,
            )
        )
    return bars


def test_qra_full_research_lifecycle():
    qra = QuantResearchAgent()

    # 1. Propose hypothesis
    hyp = qra.propose_hypothesis(
        title="Trend Channel Breakout",
        question="Does a 3-bar breakout yield positive alpha after costs?",
        economic_rationale="Momentum underreaction",
        proposed_mechanism="3-day close breakout",
        target_definition="Next bar return",
    )
    assert hyp.status.value == "registered"

    # 2. Design experiment
    cfg = qra.design_experiment(
        hypothesis=hyp,
        fee_bps=1.0,
        slippage_bps=1.0,
        hyperparameters={"lookback": 3},
    )
    assert cfg.config_hash != ""

    # 3. Run experiment with market data
    bars = _sample_bars(80)
    run = qra.run_experiment(cfg, bars=bars)
    assert run.status.value == "completed"
    assert run.metrics["trades"] > 0

    # 4. Evaluate and generate evidence report
    report = qra.evaluate(
        run_id=run.config.run_id,
        hypothesis_id=hyp.hypothesis_id,
        hypothesis_version=hyp.version,
        num_trials=5,
    )
    assert report.hypothesis_id == hyp.hypothesis_id
    assert report.recommendation in list(Recommendation)
    assert any("Historical performance" in lim for lim in report.limitations)
    assert len(report.required_reviewer_decisions) >= 3

    # Check Markdown report generation
    md = report.to_markdown()
    assert "# AXEL Research Evidence Report:" in md
    assert "Deflated Sharpe Ratio" in md

    # 5. Propose code change
    proposal = qra.propose_code_change(
        report=report,
        source_branch="research/trend-v1",
        files_changed=["axel/strategies/baseline/trend.py"],
        summary="Introduce tuned 3-day trend strategy",
        intended_behavior_change="Add validated lookback window",
        test_evidence_ref=f"report://{report.report_id}",
    )
    assert proposal.approval_state.value == "pending_review"
    assert proposal.restricted_paths_changed is False
