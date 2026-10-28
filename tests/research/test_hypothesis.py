"""Unit tests for Phase 10 Hypothesis Registry & Lifecycle."""

from __future__ import annotations

import pytest

from axel.research.hypothesis import (
    Hypothesis,
    HypothesisRegistry,
    HypothesisStatus,
)


def test_hypothesis_creation_and_registration():
    registry = HypothesisRegistry()
    hyp = registry.create(
        title="Momentum 5-day Breakout",
        question="Does a 5-day breakout predict excess 1-day forward return?",
        economic_rationale="Short term underreaction to momentum",
        proposed_mechanism="5-day high channel breakout",
        target_definition="1-day forward return",
        primary_metric="sharpe",
    )
    assert hyp.version == 1
    assert hyp.status == HypothesisStatus.DRAFT
    assert hyp.proposed_mechanism == "5-day high channel breakout"

    # Register
    registered = registry.register(hyp.hypothesis_id)
    assert registered.status == HypothesisStatus.REGISTERED
    assert registry.get(hyp.hypothesis_id).status == HypothesisStatus.REGISTERED


def test_illegal_status_transitions():
    registry = HypothesisRegistry()
    hyp = registry.create(title="Test", question="Test question?")

    # Cannot jump from DRAFT directly to EVALUATED or ACCEPTED_FOR_REVIEW
    with pytest.raises(ValueError, match="Illegal hypothesis transition"):
        registry.transition(hyp.hypothesis_id, HypothesisStatus.EVALUATED)

    # Valid: DRAFT -> REGISTERED
    reg = registry.transition(hyp.hypothesis_id, HypothesisStatus.REGISTERED)
    assert reg.status == HypothesisStatus.REGISTERED

    # Valid: REGISTERED -> RUNNING -> EVALUATED
    run = registry.transition(hyp.hypothesis_id, HypothesisStatus.RUNNING)
    assert run.status == HypothesisStatus.RUNNING
    ev = registry.transition(hyp.hypothesis_id, HypothesisStatus.EVALUATED)
    assert ev.status == HypothesisStatus.EVALUATED

    # Transition to REJECTED (terminal)
    rej = registry.transition(hyp.hypothesis_id, HypothesisStatus.REJECTED)
    assert rej.status == HypothesisStatus.REJECTED

    # From REJECTED cannot transition to RUNNING
    with pytest.raises(ValueError, match="Illegal hypothesis transition"):
        registry.transition(hyp.hypothesis_id, HypothesisStatus.RUNNING)


def test_version_bumping_preserves_audit_snapshot():
    registry = HypothesisRegistry()
    hyp = registry.create(
        title="Original V1",
        question="Original question?",
        primary_metric="sharpe",
    )
    v2 = hyp.bump_version(
        changed_by="analyst_alice",
        change_reason="Refined target definition",
        title="Refined V2",
        target_definition="2-day forward return",
    )
    assert v2.version == 2
    assert v2.title == "Refined V2"
    assert v2.target_definition == "2-day forward return"
    assert len(v2.versions) == 1
    prev = v2.versions[0]
    assert prev.version == 1
    assert prev.title == "Original V1"
    assert prev.changed_by == "analyst_alice"
    assert prev.change_reason == "Refined target definition"
    assert prev.snapshot["title"] == "Original V1"


def test_duplicate_hypothesis_linking():
    registry = HypothesisRegistry()
    h1 = registry.create(title="Hyp 1", question="Q1?")
    h2 = registry.create(title="Hyp 2 (Duplicate)", question="Q2?")

    u1, u2 = registry.link_duplicate(source_id=h2.hypothesis_id, target_id=h1.hypothesis_id)
    assert u2.duplicate_of == h1.hypothesis_id
    assert h1.hypothesis_id in u2.linked_hypothesis_ids
    assert h2.hypothesis_id in u1.linked_hypothesis_ids


def test_querying_by_status():
    registry = HypothesisRegistry()
    h1 = registry.create(title="Draft 1", question="Q?")
    h2 = registry.create(title="Draft 2", question="Q?")
    registry.register(h2.hypothesis_id)
    h3 = registry.create(title="Rejected", question="Q?")
    registry.register(h3.hypothesis_id)
    registry.transition(h3.hypothesis_id, HypothesisStatus.RUNNING)
    registry.transition(h3.hypothesis_id, HypothesisStatus.EVALUATED)
    registry.transition(h3.hypothesis_id, HypothesisStatus.REJECTED)

    assert len(registry.query_by_status(HypothesisStatus.DRAFT)) == 1
    assert len(registry.query_by_status(HypothesisStatus.REGISTERED)) == 1
    rejected = registry.query_rejected()
    assert len(rejected) == 1
    assert rejected[0].hypothesis_id == h3.hypothesis_id
