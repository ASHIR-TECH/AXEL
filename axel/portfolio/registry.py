"""
P11-REGISTRY: instrument and risk-factor registry.

Every supported instrument has a validated mapping. Unknown, expired, or
ineligible instruments are surfaced explicitly; they never silently disappear
from portfolio risk. Factor-model covariance follows the specification:

    Sigma_r = B Sigma_f B' + Sigma_epsilon

Residual independence is not assumed by default; the residual covariance is an
explicit input.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from axel.portfolio.linalg import mat_mat, transpose
from axel.portfolio.types import (
    DataQuality,
    FactorKind,
    FactorLoading,
    InstrumentEligibility,
    InstrumentSpec,
    InstrumentType,
    IssueSeverity,
    RiskFactor,
    require_aware,
    utc_now,
)


class RegistryError(ValueError):
    """Base class for registry errors."""


class DuplicateInstrumentError(RegistryError):
    """Raised when an instrument ID is registered with conflicting metadata."""


class UnknownInstrumentError(RegistryError):
    """Raised when an instrument is required but not registered."""


@dataclass(frozen=True)
class RegistryIssue:
    instrument_id: str
    code: str
    message: str
    severity: IssueSeverity


@dataclass(frozen=True)
class UniverseValidation:
    eligible_ids: tuple[str, ...]
    issues: tuple[RegistryIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == IssueSeverity.CRITICAL for issue in self.issues)

    @property
    def is_complete(self) -> bool:
        return not self.issues


class InstrumentRegistry:
    """In-memory registry with deterministic, duplicate-safe registration."""

    def __init__(self, version: str = "p11-registry-1") -> None:
        if not version:
            raise ValueError("registry version must be non-empty")
        self.version = version
        self._instruments: dict[str, InstrumentSpec] = {}
        self._factors: dict[str, RiskFactor] = {}
        self._loadings: dict[tuple[str, str], float] = {}

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._instruments))

    @property
    def factor_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._factors))

    def register(self, spec: InstrumentSpec) -> InstrumentSpec:
        existing = self._instruments.get(spec.instrument_id)
        if existing is not None:
            if existing != spec:
                raise DuplicateInstrumentError(
                    f"instrument {spec.instrument_id} already registered with different metadata"
                )
            return existing
        self._instruments[spec.instrument_id] = spec
        return spec

    def register_many(self, specs: Sequence[InstrumentSpec]) -> tuple[InstrumentSpec, ...]:
        return tuple(self.register(spec) for spec in specs)

    def get(self, instrument_id: str) -> InstrumentSpec | None:
        return self._instruments.get(instrument_id)

    def require(self, instrument_id: str) -> InstrumentSpec:
        spec = self.get(instrument_id)
        if spec is None:
            raise UnknownInstrumentError(f"instrument {instrument_id} is not registered")
        return spec

    def register_factor(self, factor: RiskFactor) -> RiskFactor:
        existing = self._factors.get(factor.factor_id)
        if existing is not None:
            if existing != factor:
                raise RegistryError(
                    f"factor {factor.factor_id} already registered with different metadata"
                )
            return existing
        self._factors[factor.factor_id] = factor
        return factor

    def register_factors(self, factors: Sequence[RiskFactor]) -> tuple[RiskFactor, ...]:
        return tuple(self.register_factor(factor) for factor in factors)

    def set_loading(self, loading: FactorLoading) -> FactorLoading:
        self.require(loading.instrument_id)
        if loading.factor_id not in self._factors:
            raise RegistryError(f"factor {loading.factor_id} is not registered")
        self._loadings[(loading.instrument_id, loading.factor_id)] = loading.loading
        return loading

    def loadings_for(self, instrument_id: str) -> Mapping[str, float]:
        self.require(instrument_id)
        return {
            factor_id: value
            for (registered_instrument, factor_id), value in sorted(self._loadings.items())
            if registered_instrument == instrument_id
        }

    def factor_exposures(self, exposures_by_instrument: Mapping[str, float]) -> Mapping[str, float]:
        """Aggregate instrument exposures into factor exposures."""
        totals: dict[str, float] = {}
        for instrument_id, exposure in sorted(exposures_by_instrument.items()):
            for factor_id, loading in self.loadings_for(instrument_id).items():
                totals[factor_id] = totals.get(factor_id, 0.0) + loading * exposure
        return totals

    def validate_universe(
        self,
        instrument_ids: Sequence[str],
        *,
        as_of: datetime | None = None,
        allow_research: bool = False,
        require_factor_mapping: bool = False,
    ) -> UniverseValidation:
        """Validate eligibility, expiry, and (optionally) factor coverage."""
        current = require_aware(as_of or utc_now(), "as_of")
        issues: list[RegistryIssue] = []
        eligible: list[str] = []
        seen: set[str] = set()
        for instrument_id in instrument_ids:
            if instrument_id in seen:
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="DUPLICATE_IN_UNIVERSE",
                        message="instrument appears more than once in the universe",
                        severity=IssueSeverity.WARNING,
                    )
                )
                continue
            seen.add(instrument_id)
            spec = self.get(instrument_id)
            if spec is None:
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="UNKNOWN_INSTRUMENT",
                        message="instrument is not registered",
                        severity=IssueSeverity.CRITICAL,
                    )
                )
                continue
            if spec.eligibility == InstrumentEligibility.INELIGIBLE:
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="INELIGIBLE_INSTRUMENT",
                        message="instrument is marked ineligible",
                        severity=IssueSeverity.CRITICAL,
                    )
                )
                continue
            if spec.eligibility == InstrumentEligibility.UNKNOWN:
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="UNKNOWN_ELIGIBILITY",
                        message="instrument eligibility is unknown",
                        severity=IssueSeverity.CRITICAL,
                    )
                )
                continue
            if spec.eligibility == InstrumentEligibility.RESEARCH_ONLY and not allow_research:
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="RESEARCH_ONLY",
                        message="instrument is research-only and cannot receive live risk",
                        severity=IssueSeverity.CRITICAL,
                    )
                )
                continue
            if spec.expiry is not None and spec.expiry <= current:
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="EXPIRED_INSTRUMENT",
                        message=f"instrument expired at {spec.expiry.isoformat()}",
                        severity=IssueSeverity.CRITICAL,
                    )
                )
                continue
            if spec.instrument_type == InstrumentType.FX_PAIR and (
                not spec.base_currency or not spec.quote_currency
            ):
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="INCOMPLETE_FX_LEGS",
                        message="FX pair is missing base or quote currency",
                        severity=IssueSeverity.CRITICAL,
                    )
                )
                continue
            if require_factor_mapping and not self.loadings_for(instrument_id):
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="MISSING_FACTOR_MAPPING",
                        message="instrument has no factor loadings",
                        severity=IssueSeverity.CRITICAL,
                    )
                )
                continue
            if spec.risk_model_version == "unknown":
                issues.append(
                    RegistryIssue(
                        instrument_id=instrument_id,
                        code="UNKNOWN_RISK_MODEL",
                        message="instrument risk model version is unknown",
                        severity=IssueSeverity.WARNING,
                    )
                )
            eligible.append(instrument_id)
        return UniverseValidation(eligible_ids=tuple(eligible), issues=tuple(issues))

    def snapshot(self) -> Mapping[str, object]:
        """Deterministic registry snapshot for lineage and audit."""
        return {
            "version": self.version,
            "instruments": tuple(
                self._instruments[key] for key in sorted(self._instruments)
            ),
            "factors": tuple(self._factors[key] for key in sorted(self._factors)),
            "loadings": tuple(
                FactorLoading(instrument_id, factor_id, value)
                for (instrument_id, factor_id), value in sorted(self._loadings.items())
            ),
        }

    def quality(self, instrument_ids: Sequence[str]) -> DataQuality:
        validation = self.validate_universe(instrument_ids)
        if not validation.is_valid:
            return DataQuality.INVALID
        if validation.issues:
            return DataQuality.DEGRADED
        return DataQuality.VALID


def factor_model_covariance(
    loadings: Sequence[Sequence[float]],
    factor_covariance: Sequence[Sequence[float]],
    residual_variances: Sequence[float],
) -> list[list[float]]:
    """Build ``B Sigma_f B' + diag(residual_variances)``.

    Residual covariance is diagonal here only because the caller supplied
    residual *variances*. If residuals are correlated, pass a full residual
    matrix through a dedicated extension rather than pretending they are
    independent.
    """
    if not loadings or not factor_covariance:
        raise ValueError("loadings and factor covariance must be non-empty")
    if len(residual_variances) != len(loadings):
        raise ValueError("one residual variance is required per instrument")
    if len(factor_covariance) != len(loadings[0]):
        raise ValueError("factor covariance dimension does not match loadings")
    if any(value < 0 for value in residual_variances):
        raise ValueError("residual variances must be non-negative")

    common = mat_mat(mat_mat(loadings, factor_covariance), transpose(loadings))
    for index, variance in enumerate(residual_variances):
        common[index][index] += variance
    return common


def factor_kind_for_section(section: str) -> FactorKind:
    """Default factor family for a section, used when no explicit mapping exists."""
    normalized = section.lower()
    mapping = {
        "stocks": FactorKind.EQUITY_MARKET,
        "options": FactorKind.VOLATILITY,
        "crypto": FactorKind.CRYPTO,
        "forex": FactorKind.FX,
        "predictions": FactorKind.EVENT,
    }
    return mapping.get(normalized, FactorKind.CREDIT)


__all__ = [
    "DuplicateInstrumentError",
    "InstrumentRegistry",
    "RegistryError",
    "RegistryIssue",
    "UniverseValidation",
    "UnknownInstrumentError",
    "factor_kind_for_section",
    "factor_model_covariance",
]
