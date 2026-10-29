"""Security, boundary, and governance tests for Phase 10 QRA."""

from __future__ import annotations

import pytest

from axel.research.governance import (
    GovernanceGate,
    PromotionState,
)
from axel.research.pr import (
    CodeChangeProposal,
    ProposalStatus,
    is_restricted_path,
)
from axel.research.qra import QuantResearchAgent


def test_restricted_paths_identification():
    assert is_restricted_path("axel/core/order.py") is True
    assert is_restricted_path("axel/execution/broker.py") is True
    assert is_restricted_path("axel/risk/limits.py") is True
    assert is_restricted_path("axel/watchdog/monitor.py") is True
    assert is_restricted_path("config/production/secrets.env") is True
    assert is_restricted_path("axel\\core\\trade.py") is True
    # Non-restricted research paths
    assert is_restricted_path("axel/research/baseline.py") is False
    assert is_restricted_path("axel/strategies/baseline/trend.py") is False


def test_proposals_blocking_restricted_paths():
    proposal = CodeChangeProposal(
        proposal_id="pr_risk_tamper",
        report_id="rpt_1",
        hypothesis_id="hyp_1",
        experiment_run_id="run_1",
        source_branch="research/exploit",
        files_changed=["axel/risk/limits.py"],
        summary="Tampering risk limits",
        intended_behavior_change="Relax max drawdown",
        test_evidence_ref="evidence_ref",
        confirmation_restricted_not_modified=True,
    )
    # Automated detection should flag restricted paths
    assert proposal.restricted_paths_changed is True
    assert proposal.can_merge() is False


def test_qra_cannot_self_approve_proposal():
    proposal = CodeChangeProposal(
        proposal_id="pr_safe",
        report_id="rpt_1",
        hypothesis_id="hyp_1",
        experiment_run_id="run_1",
        source_branch="research/feat",
        files_changed=["axel/strategies/baseline/trend.py"],
        summary="Safe strategy update",
        intended_behavior_change="Improve Sharpe",
        test_evidence_ref="evidence_ref",
        confirmation_restricted_not_modified=True,
        created_by="qra",
    )

    # QRA self-approval is forbidden
    with pytest.raises(PermissionError, match="cannot self-approve"):
        proposal.approve("qra")

    # Independent human/system review approval succeeds
    approved_proposal = proposal.approve("human_risk_officer")
    assert approved_proposal.approval_state == ProposalStatus.APPROVED
    assert approved_proposal.can_merge() is True


def test_governance_gate_prerequisites_and_no_live_promotion():
    gate = GovernanceGate()
    assert gate.state == PromotionState.RESEARCH

    # Cannot advance to EVALUATED without evidence
    with pytest.raises(ValueError, match="Missing required evidence"):
        gate.advance()

    # Add required evidence for EVALUATED
    gate = gate.add_evidence(["hypothesis_registered", "point_in_time_validated"])
    gate = gate.advance()
    assert gate.state == PromotionState.EVALUATED

    # Verify no live promotion state exists in enum or gate
    assert not hasattr(gate, "can_promote_to_live")
    with pytest.raises(AttributeError):
        _ = PromotionState.PROMOTED


def test_qra_has_no_order_or_live_execution_authority():
    qra = QuantResearchAgent()

    # Security check: QRA must not expose order execution methods
    prohibited_methods = [
        "place_order",
        "submit_order",
        "execute_trade",
        "modify_position",
        "write_fill",
        "disable_watchdog",
        "relax_risk_limits",
    ]
    for method in prohibited_methods:
        assert not hasattr(qra, method), f"QRA improperly exposes live trading method: {method}"
