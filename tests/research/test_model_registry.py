"""Tests for Phase 14 model registry and lifecycle governance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from axel.research.model_registry import (
    VALID_DEPLOYMENT_TRANSITIONS,
    DeploymentState,
    ModelRecord,
    ModelRegistry,
    PerformanceSnapshot,
)


def _model(
    model_id: str = "trend_v1",
    version: str = "1.0",
    owner: str = "dev2",
    purpose: str = "trend following",
    **kwargs,
) -> ModelRecord:
    return ModelRecord(
        model_id=model_id,
        version=version,
        owner=owner,
        purpose=purpose,
        **kwargs,
    )


# --- ModelRecord validation ---


class TestModelRecordValidation:
    def test_requires_model_id(self):
        with pytest.raises(ValueError, match="model_id"):
            _model(model_id="")

    def test_requires_version(self):
        with pytest.raises(ValueError, match="version"):
            _model(version="")

    def test_requires_owner(self):
        with pytest.raises(ValueError, match="owner"):
            _model(owner="")

    def test_requires_purpose(self):
        with pytest.raises(ValueError, match="purpose"):
            _model(purpose="")

    def test_valid_model_creates_successfully(self):
        m = _model()
        assert m.model_id == "trend_v1"
        assert m.deployment_state == DeploymentState.DEVELOPMENT


# --- Evidence and prerequisites ---


class TestModelEvidence:
    def test_add_evidence_returns_new_record(self):
        m = _model()
        m2 = m.add_evidence("evaluation_results_present", actor="tester")
        assert "evaluation_results_present" in m2.satisfied_evidence
        assert "evaluation_results_present" not in m.satisfied_evidence

    def test_add_multiple_evidence(self):
        m = _model()
        m2 = m.add_evidence(["a", "b", "c"])
        assert m2.satisfied_evidence == {"a", "b", "c"}

    def test_audit_trail_records_evidence(self):
        m = _model()
        m2 = m.add_evidence("test_evidence", actor="ci")
        assert len(m2.audit_trail) == 1
        assert m2.audit_trail[0]["actor"] == "ci"
        assert m2.audit_trail[0]["action"] == "add_evidence"

    def test_check_promotion_prerequisites_validated(self):
        m = _model()
        ok, missing = m.check_promotion_prerequisites(DeploymentState.VALIDATED)
        assert not ok
        assert "evaluation_results_present" in missing

        m2 = m.add_evidence([
            "evaluation_results_present",
            "dataset_lineage_recorded",
            "feature_versions_recorded",
        ])
        ok2, missing2 = m2.check_promotion_prerequisites(DeploymentState.VALIDATED)
        assert ok2
        assert missing2 == []


# --- Performance monitoring ---


class TestPerformanceMonitoring:
    def test_record_performance_appends(self):
        m = _model()
        snap = PerformanceSnapshot(prediction_error=0.05, oos_sharpe=1.2)
        m2 = m.record_performance(snap)
        assert len(m2.performance_history) == 1
        assert m2.performance_history[0].prediction_error == 0.05

    def test_review_overdue_when_never_validated(self):
        m = _model()
        assert m.is_review_overdue()

    def test_review_not_overdue_when_recent(self):
        m = _model(last_validation=datetime.now(UTC))
        assert not m.is_review_overdue()

    def test_review_overdue_after_schedule(self):
        old = datetime.now(UTC) - timedelta(days=100)
        m = _model(last_validation=old, review_schedule_days=90)
        assert m.is_review_overdue()

    def test_needs_deactivation_on_concept_drift(self):
        m = _model(last_validation=datetime.now(UTC))
        snap = PerformanceSnapshot(concept_drift_detected=True)
        m2 = m.record_performance(snap)
        needs, reasons = m2.needs_deactivation()
        assert needs
        assert "concept_drift_detected" in reasons

    def test_needs_deactivation_on_high_failure_rate(self):
        m = _model(last_validation=datetime.now(UTC))
        snap = PerformanceSnapshot(failure_rate=0.15)
        m2 = m.record_performance(snap)
        needs, reasons = m2.needs_deactivation()
        assert needs
        assert "high_failure_rate" in reasons

    def test_needs_deactivation_on_significant_drift(self):
        m = _model(last_validation=datetime.now(UTC))
        snap = PerformanceSnapshot(feature_drift_psi=0.30)
        m2 = m.record_performance(snap)
        needs, reasons = m2.needs_deactivation()
        assert needs
        assert "significant_feature_drift" in reasons

    def test_healthy_model_does_not_need_deactivation(self):
        m = _model(last_validation=datetime.now(UTC))
        snap = PerformanceSnapshot(
            prediction_error=0.02,
            feature_drift_psi=0.05,
            failure_rate=0.01,
        )
        m2 = m.record_performance(snap)
        needs, _ = m2.needs_deactivation()
        assert not needs


# --- ModelRegistry ---


class TestModelRegistry:
    def test_register_and_get(self):
        reg = ModelRegistry()
        m = _model()
        reg.register(m)
        assert reg.get("trend_v1", "1.0") is not None

    def test_duplicate_registration_fails(self):
        reg = ModelRegistry()
        reg.register(_model())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_model())

    def test_get_latest_version(self):
        reg = ModelRegistry()
        reg.register(_model(version="1.0"))
        reg.register(_model(version="2.0"))
        latest = reg.get("trend_v1")
        assert latest is not None
        # Should get the one with latest created_at
        assert latest.version in ("1.0", "2.0")

    def test_get_nonexistent_returns_none(self):
        reg = ModelRegistry()
        assert reg.get("nonexistent") is None

    def test_list_by_state(self):
        reg = ModelRegistry()
        reg.register(_model(model_id="a", version="1"))
        reg.register(_model(model_id="b", version="1"))
        devs = reg.list_by_state(DeploymentState.DEVELOPMENT)
        assert len(devs) == 2

    def test_list_by_owner(self):
        reg = ModelRegistry()
        reg.register(_model(model_id="a", version="1", owner="alice"))
        reg.register(_model(model_id="b", version="1", owner="bob"))
        alice_models = reg.list_by_owner("alice")
        assert len(alice_models) == 1


# --- Promotion lifecycle ---


class TestPromotion:
    def _registry_with_model(self) -> tuple[ModelRegistry, str, str]:
        reg = ModelRegistry()
        m = _model()
        reg.register(m)
        return reg, m.model_id, m.version

    def test_promote_without_evidence_fails(self):
        reg, mid, ver = self._registry_with_model()
        with pytest.raises(ValueError, match="Missing evidence"):
            reg.promote(mid, ver, DeploymentState.VALIDATED, "tester")

    def test_promote_with_evidence_succeeds(self):
        reg, mid, ver = self._registry_with_model()
        m = reg.get(mid, ver)
        assert m is not None
        m = m.add_evidence([
            "evaluation_results_present",
            "dataset_lineage_recorded",
            "feature_versions_recorded",
        ])
        reg.update(m)
        promoted = reg.promote(mid, ver, DeploymentState.VALIDATED, "tester")
        assert promoted.deployment_state == DeploymentState.VALIDATED

    def test_invalid_transition_fails(self):
        reg, mid, ver = self._registry_with_model()
        # DEVELOPMENT -> PRODUCTION is not valid
        with pytest.raises(ValueError, match="Cannot transition"):
            reg.promote(mid, ver, DeploymentState.PRODUCTION, "tester")

    def test_all_valid_transitions_are_documented(self):
        for state in DeploymentState:
            assert state in VALID_DEPLOYMENT_TRANSITIONS

    def test_retired_cannot_be_promoted(self):
        assert VALID_DEPLOYMENT_TRANSITIONS[DeploymentState.RETIRED] == ()


# --- Rollback and retirement ---


class TestRollbackAndRetirement:
    def test_rollback_moves_to_validated(self):
        reg = ModelRegistry()
        m = _model()
        m = m.add_evidence([
            "evaluation_results_present",
            "dataset_lineage_recorded",
            "feature_versions_recorded",
        ])
        reg.register(m)
        reg.promote(m.model_id, m.version, DeploymentState.VALIDATED, "tester")

        rolled = reg.rollback(m.model_id, m.version, "operator")
        assert rolled.deployment_state == DeploymentState.VALIDATED
        assert any(e["action"] == "rollback" for e in rolled.audit_trail)

    def test_retire_moves_to_retired(self):
        reg = ModelRegistry()
        reg.register(_model())
        retired = reg.retire("trend_v1", "1.0", "end of life", "admin")
        assert retired.deployment_state == DeploymentState.RETIRED

    def test_retire_records_reason(self):
        reg = ModelRegistry()
        reg.register(_model())
        retired = reg.retire("trend_v1", "1.0", "poor performance", "admin")
        assert any(
            e["action"] == "retire" and e["reason"] == "poor performance"
            for e in retired.audit_trail
        )


# --- Auto-deactivation (§10.4) ---


class TestAutoDeactivation:
    def test_deactivate_if_needed_with_drift(self):
        reg = ModelRegistry()
        m = _model(last_validation=datetime.now(UTC))
        snap = PerformanceSnapshot(data_drift_detected=True)
        m = m.record_performance(snap)
        reg.register(m)

        result = reg.deactivate_if_needed(m.model_id, m.version)
        assert result is not None
        assert result.deployment_state == DeploymentState.DEGRADED

    def test_deactivate_if_needed_healthy_returns_none(self):
        reg = ModelRegistry()
        m = _model(last_validation=datetime.now(UTC))
        snap = PerformanceSnapshot(failure_rate=0.01, feature_drift_psi=0.05)
        m = m.record_performance(snap)
        reg.register(m)

        result = reg.deactivate_if_needed(m.model_id, m.version)
        assert result is None

    def test_deactivate_skips_already_retired(self):
        reg = ModelRegistry()
        m = _model(last_validation=datetime.now(UTC))
        snap = PerformanceSnapshot(concept_drift_detected=True)
        m = m.record_performance(snap)
        reg.register(m)
        reg.retire(m.model_id, m.version, "done", "admin")

        result = reg.deactivate_if_needed(m.model_id, m.version)
        assert result is None

    def test_models_needing_review(self):
        reg = ModelRegistry()
        old = datetime.now(UTC) - timedelta(days=200)
        m = _model(last_validation=old, review_schedule_days=90)
        reg.register(m)

        needs = reg.models_needing_review()
        assert len(needs) == 1
        assert needs[0].model_id == "trend_v1"
