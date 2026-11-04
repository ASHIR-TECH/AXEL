"""Model registry and lifecycle governance for Phase 14.

Implements §10.1–10.4 of the Developer 2 spec and §6.4 model-registry
fields.  A model only reaches PRODUCTION after evidence-gated promotion.
A model must be deactivated if validation assumptions are violated.

Architectural constraint: this module proposes deployment state changes.
It possesses NO live trading authority — the deterministic core owns
actual execution enablement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# §10.1 – Deployment lifecycle states
# ---------------------------------------------------------------------------


class DeploymentState(StrEnum):
    """Lifecycle deployment states for a registered model."""

    DEVELOPMENT = "development"
    VALIDATED = "validated"
    STAGING = "staging"
    PAPER = "paper"
    PRODUCTION = "production"
    DEGRADED = "degraded"
    RETIRED = "retired"


# Only forward transitions are allowed; rollback is a special operation.
VALID_DEPLOYMENT_TRANSITIONS: dict[DeploymentState, tuple[DeploymentState, ...]] = {
    DeploymentState.DEVELOPMENT: (DeploymentState.VALIDATED,),
    DeploymentState.VALIDATED: (DeploymentState.STAGING, DeploymentState.RETIRED),
    DeploymentState.STAGING: (
        DeploymentState.PAPER,
        DeploymentState.RETIRED,
    ),
    DeploymentState.PAPER: (
        DeploymentState.PRODUCTION,
        DeploymentState.DEGRADED,
        DeploymentState.RETIRED,
    ),
    DeploymentState.PRODUCTION: (
        DeploymentState.DEGRADED,
        DeploymentState.RETIRED,
    ),
    DeploymentState.DEGRADED: (
        DeploymentState.PRODUCTION,
        DeploymentState.RETIRED,
    ),
    DeploymentState.RETIRED: (),
}

# Evidence gates that must be satisfied before a model can be promoted to the
# given target state.
PROMOTION_EVIDENCE_REQUIREMENTS: dict[DeploymentState, list[str]] = {
    DeploymentState.VALIDATED: [
        "evaluation_results_present",
        "dataset_lineage_recorded",
        "feature_versions_recorded",
    ],
    DeploymentState.STAGING: [
        "code_review_passed",
        "robustness_tests_passed",
    ],
    DeploymentState.PAPER: [
        "staging_metrics_acceptable",
        "risk_review_passed",
    ],
    DeploymentState.PRODUCTION: [
        "paper_soak_passed",
        "human_sign_off",
        "rollback_procedure_documented",
    ],
}


# ---------------------------------------------------------------------------
# §10.2 – Model performance monitoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerformanceSnapshot:
    """Point-in-time model monitoring record (§10.2).

    Captures all performance-monitoring fields required by the spec:
    prediction error, calibration, feature / concept / data drift,
    latency, failure rate, cost, and out-of-sample performance.
    """

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    prediction_error: float | None = None
    calibration_score: float | None = None
    feature_drift_psi: float | None = None
    concept_drift_detected: bool = False
    data_drift_detected: bool = False
    latency_ms: float | None = None
    failure_rate: float | None = None
    cost_usd: float | None = None
    oos_sharpe: float | None = None
    oos_trades: int = 0
    notes: str = ""


# ---------------------------------------------------------------------------
# §6.4 / §10.4 – Model record with governance metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRecord:
    """A versioned model registered in the AXEL model registry (§6.4, §10.4).

    Every deployed model must have (§10.4):
    - Identifiable *owner*
    - Documented *purpose*
    - Approved *asset_classes*
    - *known_limitations*
    - Evaluation *evaluation_results* evidence
    - *rollback_version* procedure
    - *monitoring_policy*
    - *review_schedule_days*

    The record also carries dataset lineage (*training_data*), feature
    lineage (*feature_versions*), experiment history, and a full
    immutable audit trail.
    """

    # §6.4 required fields ------------------------------------------------
    model_id: str
    version: str
    owner: str
    purpose: str
    asset_classes: list[str] = field(default_factory=list)
    training_data: list[tuple[str, str]] = field(
        default_factory=list,
    )  # (dataset_id, version)
    feature_versions: list[tuple[str, str]] = field(
        default_factory=list,
    )  # (feature_id, version)
    experiment_history: list[str] = field(default_factory=list)  # run_ids
    evaluation_results: dict[str, Any] = field(default_factory=dict)
    deployment_state: DeploymentState = DeploymentState.DEVELOPMENT
    rollback_version: str | None = None
    last_validation: datetime | None = None

    # §10.4 governance metadata --------------------------------------------
    known_limitations: list[str] = field(default_factory=list)
    monitoring_policy: str = "default"
    review_schedule_days: int = 90

    # Operational bookkeeping ----------------------------------------------
    performance_history: list[PerformanceSnapshot] = field(default_factory=list)
    satisfied_evidence: set[str] = field(default_factory=set)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    code_commit: str = ""
    model_hash: str = ""

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id is required")
        if not self.version:
            raise ValueError("version is required")
        if not self.owner:
            raise ValueError("owner is required")
        if not self.purpose:
            raise ValueError("purpose is required")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def key(self) -> tuple[str, str]:
        """Natural composite key used by the registry."""
        return (self.model_id, self.version)

    def add_evidence(
        self,
        evidence: set[str] | list[str] | str,
        actor: str = "system",
    ) -> ModelRecord:
        """Return a new record with additional promotion evidence attached."""
        items = {evidence} if isinstance(evidence, str) else set(evidence)
        new_sat = self.satisfied_evidence | items
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "add_evidence",
            "items": sorted(items),
            "state": self.deployment_state.value,
        }
        return replace(
            self,
            satisfied_evidence=new_sat,
            audit_trail=self.audit_trail + [entry],
            updated_at=datetime.now(UTC),
        )

    def check_promotion_prerequisites(
        self,
        target: DeploymentState,
    ) -> tuple[bool, list[str]]:
        """Check whether the model satisfies evidence gates for *target*.

        Returns ``(True, [])`` when all requirements are met, or
        ``(False, missing)`` with a list of unsatisfied evidence keys.
        """
        reqs = PROMOTION_EVIDENCE_REQUIREMENTS.get(target, [])
        missing = [r for r in reqs if r not in self.satisfied_evidence]
        return (len(missing) == 0, missing)

    def record_performance(
        self,
        snapshot: PerformanceSnapshot,
    ) -> ModelRecord:
        """Return a new record with *snapshot* appended to performance history."""
        return replace(
            self,
            performance_history=self.performance_history + [snapshot],
            updated_at=datetime.now(UTC),
        )

    def is_review_overdue(self, now: datetime | None = None) -> bool:
        """Return ``True`` if the model's scheduled review is overdue."""
        ref = now or datetime.now(UTC)
        if self.last_validation is None:
            return True
        delta = ref - self.last_validation
        return delta.days > self.review_schedule_days

    def needs_deactivation(self) -> tuple[bool, list[str]]:
        """§10.4: a model must be deactivated if its validation or data
        assumptions are no longer satisfied.

        Returns ``(True, reasons)`` when deactivation is warranted.
        """
        reasons: list[str] = []
        if self.is_review_overdue():
            reasons.append("review_overdue")
        if self.performance_history:
            latest = self.performance_history[-1]
            if latest.concept_drift_detected:
                reasons.append("concept_drift_detected")
            if latest.data_drift_detected:
                reasons.append("data_drift_detected")
            if (
                latest.feature_drift_psi is not None
                and latest.feature_drift_psi > 0.25
            ):
                reasons.append("significant_feature_drift")
            if (
                latest.failure_rate is not None
                and latest.failure_rate > 0.10
            ):
                reasons.append("high_failure_rate")
        return (len(reasons) > 0, reasons)


# ---------------------------------------------------------------------------
# §10.1 – Central model registry
# ---------------------------------------------------------------------------


class ModelRegistry:
    """Central registry for all AXEL models (§10.1).

    Provides model registration, versioning, evidence-gated promotion,
    rollback, retirement, and automatic deactivation when governance
    assumptions are violated.

    This class has **no** execution authority.  It records and proposes
    lifecycle state changes; the deterministic core decides whether a
    model is actually used for live trading.
    """

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], ModelRecord] = {}
        self._audit: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, record: ModelRecord) -> None:
        """Register a new model record.  Raises if already present."""
        if record.key in self._models:
            raise ValueError(
                f"Model {record.model_id} v{record.version} already registered",
            )
        self._models[record.key] = record
        self._audit.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "action": "register",
                "model_id": record.model_id,
                "version": record.version,
            },
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(
        self,
        model_id: str,
        version: str | None = None,
    ) -> ModelRecord | None:
        """Look up a model by id and optional version.

        When *version* is ``None`` the latest version (by *created_at*)
        is returned.
        """
        if version:
            return self._models.get((model_id, version))
        # Return latest version by created_at
        candidates = [
            r for r in self._models.values() if r.model_id == model_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.created_at)

    def list_all(self) -> list[ModelRecord]:
        """Return every registered model record."""
        return list(self._models.values())

    def list_by_state(self, state: DeploymentState) -> list[ModelRecord]:
        """Return all models in *state*."""
        return [
            r for r in self._models.values() if r.deployment_state == state
        ]

    def list_by_owner(self, owner: str) -> list[ModelRecord]:
        """Return all models owned by *owner*."""
        return [r for r in self._models.values() if r.owner == owner]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def update(self, record: ModelRecord) -> None:
        """Replace an existing record in-place.  Raises if not found."""
        if record.key not in self._models:
            raise KeyError(
                f"Model {record.model_id} v{record.version} not found",
            )
        self._models[record.key] = record

    def promote(
        self,
        model_id: str,
        version: str,
        target: DeploymentState,
        actor: str,
    ) -> ModelRecord:
        """Evidence-gated promotion to *target* state.

        Validates that:
        1. The transition is legal according to
           ``VALID_DEPLOYMENT_TRANSITIONS``.
        2. All evidence requirements in
           ``PROMOTION_EVIDENCE_REQUIREMENTS`` are satisfied.

        Raises ``KeyError`` if the model is not found and ``ValueError``
        if the transition or evidence check fails.
        """
        record = self._models.get((model_id, version))
        if record is None:
            raise KeyError(f"Model {model_id} v{version} not found")

        current = record.deployment_state
        valid_targets = VALID_DEPLOYMENT_TRANSITIONS.get(current, ())
        if target not in valid_targets:
            raise ValueError(
                f"Cannot transition {model_id} v{version} from "
                f"'{current.value}' to '{target.value}'. "
                f"Valid targets: {[t.value for t in valid_targets]}",
            )

        ok, missing = record.check_promotion_prerequisites(target)
        if not ok:
            raise ValueError(
                f"Cannot promote {model_id} v{version} to '{target.value}'. "
                f"Missing evidence: {missing}",
            )

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "promote",
            "from_state": current.value,
            "to_state": target.value,
        }
        updated = replace(
            record,
            deployment_state=target,
            audit_trail=record.audit_trail + [entry],
            updated_at=datetime.now(UTC),
        )
        self._models[updated.key] = updated
        return updated

    # ------------------------------------------------------------------
    # Rollback & retirement
    # ------------------------------------------------------------------

    def rollback(
        self,
        model_id: str,
        version: str,
        actor: str,
    ) -> ModelRecord:
        """Roll back to the previous version's deployment state, or to VALIDATED.

        If *rollback_version* is set on the record and that version
        exists, it is re-activated into the current record's deployment
        state.  The current record is demoted to ``VALIDATED``.
        """
        record = self._models.get((model_id, version))
        if record is None:
            raise KeyError(f"Model {model_id} v{version} not found")

        if record.rollback_version:
            rollback_target = self._models.get(
                (model_id, record.rollback_version),
            )
            if rollback_target:
                # Re-activate the rollback version
                reactivated = replace(
                    rollback_target,
                    deployment_state=record.deployment_state,
                    audit_trail=rollback_target.audit_trail
                    + [
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "actor": actor,
                            "action": "rollback_reactivate",
                            "replacing": version,
                        },
                    ],
                    updated_at=datetime.now(UTC),
                )
                self._models[reactivated.key] = reactivated

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "rollback",
            "from_state": record.deployment_state.value,
            "to_state": DeploymentState.VALIDATED.value,
        }
        updated = replace(
            record,
            deployment_state=DeploymentState.VALIDATED,
            audit_trail=record.audit_trail + [entry],
            updated_at=datetime.now(UTC),
        )
        self._models[updated.key] = updated
        return updated

    def retire(
        self,
        model_id: str,
        version: str,
        reason: str,
        actor: str,
    ) -> ModelRecord:
        """Move a model to ``RETIRED``.  Terminal state — no further
        transitions are possible.
        """
        record = self._models.get((model_id, version))
        if record is None:
            raise KeyError(f"Model {model_id} v{version} not found")

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "retire",
            "reason": reason,
            "from_state": record.deployment_state.value,
        }
        updated = replace(
            record,
            deployment_state=DeploymentState.RETIRED,
            audit_trail=record.audit_trail + [entry],
            updated_at=datetime.now(UTC),
        )
        self._models[updated.key] = updated
        return updated

    # ------------------------------------------------------------------
    # §10.4 – Automatic deactivation
    # ------------------------------------------------------------------

    def deactivate_if_needed(
        self,
        model_id: str,
        version: str,
        actor: str = "monitor",
    ) -> ModelRecord | None:
        """§10.4: deactivate a model whose assumptions are no longer satisfied.

        Returns the updated record if deactivation occurred, else ``None``.
        Already-retired models are skipped.
        """
        record = self._models.get((model_id, version))
        if record is None:
            return None
        needs, reasons = record.needs_deactivation()
        if not needs:
            return None
        if record.deployment_state == DeploymentState.RETIRED:
            return None

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": "auto_deactivate",
            "reasons": reasons,
            "from_state": record.deployment_state.value,
        }
        updated = replace(
            record,
            deployment_state=DeploymentState.DEGRADED,
            audit_trail=record.audit_trail + [entry],
            updated_at=datetime.now(UTC),
        )
        self._models[updated.key] = updated
        return updated

    # ------------------------------------------------------------------
    # Review monitoring
    # ------------------------------------------------------------------

    def models_needing_review(
        self,
        now: datetime | None = None,
    ) -> list[ModelRecord]:
        """Return all models whose scheduled review is overdue."""
        return [
            r for r in self._models.values() if r.is_review_overdue(now)
        ]
