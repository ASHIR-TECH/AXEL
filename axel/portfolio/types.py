"""
Domain types for Phase 11 cross-asset portfolio intelligence.

This module is pure data: frozen dataclasses and enums with no I/O. It is the
shared vocabulary for portfolio snapshots, reconciliation, exposure
normalization, risk analytics, optimization, stress testing, proposals, and
deterministic validation.

Design rules encoded here (see ``phases/AXEL Phase 11 updated .md``):

* Missing data is represented explicitly (``None``, ``MISSING``, ``UNKNOWN``)
  and is never coerced to zero.
* A risk budget is not cash and not an order.
* A proposal is not authorization.
* Every value carries a currency, timestamp, source, and quality marker where
  the underlying source can provide one.
* A disabled section is not a verified zero-exposure section.

Units are documented on each field. Currency amounts are in the stated
currency; risk measures are decimal returns unless explicitly named otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
from typing import Any

from axel.core.types import OrderSide, Section


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp factory used by default fields."""
    return datetime.now(UTC)


def require_aware(value: datetime, field_name: str) -> datetime:
    """Return a UTC-aware datetime, rejecting naive timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def require_finite(value: float, field_name: str) -> float:
    """Reject NaN and infinity at the boundary."""
    if not isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


def require_non_negative(value: float, field_name: str) -> float:
    require_finite(value, field_name)
    if value < 0.0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def require_positive(value: float, field_name: str) -> float:
    require_finite(value, field_name)
    if value <= 0.0:
        raise ValueError(f"{field_name} must be positive")
    return value


def as_section(value: Section | str) -> Section:
    """Coerce a section value and reject unknown sections."""
    return value if isinstance(value, Section) else Section(value)


class SectionReadiness(str, Enum):
    """Authorized readiness state consumed by the portfolio layer.

    The portfolio layer never promotes a section; it only reads this state.
    """

    DISABLED = "disabled"
    RESEARCH_ONLY = "research_only"
    PAPER_ONLY = "paper_only"
    LIVE_ELIGIBLE = "live_eligible"
    HALTED = "halted"
    RECONCILIATION_BLOCKED = "reconciliation_blocked"
    UNKNOWN = "unknown"


class DataQuality(str, Enum):
    VALID = "valid"
    DEGRADED = "degraded"
    STALE = "stale"
    MISSING = "missing"
    INVALID = "invalid"


class SnapshotCompleteness(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


class ReconciliationStatus(str, Enum):
    RECONCILED = "reconciled"
    PENDING = "pending"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IssueStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"


class AssetClass(str, Enum):
    EQUITY = "equity"
    OPTION = "option"
    CRYPTO = "crypto"
    FOREX = "forex"
    PREDICTION = "prediction"
    CASH = "cash"


class InstrumentType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    CALL = "call"
    PUT = "put"
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_PERPETUAL = "crypto_perpetual"
    FX_PAIR = "fx_pair"
    BINARY_CONTRACT = "binary_contract"
    MULTI_OUTCOME_CONTRACT = "multi_outcome_contract"


class InstrumentEligibility(str, Enum):
    ELIGIBLE = "eligible"
    RESEARCH_ONLY = "research_only"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class FactorKind(str, Enum):
    EQUITY_MARKET = "equity_market"
    SECTOR = "sector"
    STYLE = "style"
    RATE = "rate"
    FX = "fx"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    VOLATILITY = "volatility"
    CREDIT = "credit"
    FUNDING = "funding"
    EVENT = "event"
    CUSTODY = "custody"


class CommitmentKind(str, Enum):
    RESERVED_CASH = "reserved_cash"
    MARGIN = "margin"
    BET_STAKE = "bet_stake"
    UNSETTLED_CASH = "unsettled_cash"
    OPTION_ASSIGNMENT = "option_assignment"
    OTHER = "other"


class ProposalAction(str, Enum):
    INCREASE = "increase"
    REDUCE = "reduce"
    HOLD = "hold"
    DEFER = "defer"


class ProposalState(str, Enum):
    DRAFT = "draft"
    PENDING_VALIDATION = "pending_validation"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DEFERRED = "deferred"


class SolverStatus(str, Enum):
    """Explicit optimizer result. Failure is never an empty allocation."""

    OPTIMAL = "optimal"
    APPROXIMATE = "approximate"
    INFEASIBLE = "infeasible"
    INVALID_INPUT = "invalid_input"
    STALE_DATA = "stale_data"
    MISSING_EXPOSURE = "missing_exposure"
    SOLVER_ERROR = "solver_error"
    TIMEOUT = "timeout"
    UNSUPPORTED_INSTRUMENT = "unsupported_instrument"


class ConstraintSeverity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class ValidationDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REQUIRE_REVIEW = "require_review"


@dataclass(frozen=True)
class FreshnessPolicy:
    """Approved freshness thresholds, in seconds.

    Thresholds are instrument/section appropriate in production; the defaults
    here are deliberately conservative starting points, not universal truths.
    """

    max_source_age_seconds: int = 300
    max_price_age_seconds: int = 300
    max_fx_age_seconds: int = 300
    max_proposal_age_seconds: int = 900
    stale_grace_seconds: int = 60
    section_overrides: tuple[tuple[str, int], ...] = ()

    def max_age_for(self, section: Section | str | None = None) -> int:
        overrides = dict(self.section_overrides)
        if section is not None:
            key = as_section(section).value
            if key in overrides:
                return overrides[key]
        return self.max_source_age_seconds


@dataclass(frozen=True)
class SourceRef:
    """Lineage for one source observation used by a snapshot."""

    source_id: str
    source_type: str
    observed_at: datetime
    received_at: datetime
    quality: DataQuality
    section: Section | None = None
    lineage: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must be non-empty")
        if not self.source_type:
            raise ValueError("source_type must be non-empty")
        object.__setattr__(self, "observed_at", require_aware(self.observed_at, "observed_at"))
        object.__setattr__(self, "received_at", require_aware(self.received_at, "received_at"))
        if self.received_at < self.observed_at:
            raise ValueError("received_at cannot precede observed_at")
        if self.section is not None:
            object.__setattr__(self, "section", as_section(self.section))

    def age_seconds(self, now: datetime) -> float:
        return (require_aware(now, "now") - self.observed_at).total_seconds()


@dataclass(frozen=True)
class FxRate:
    """One FX quote: 1 unit of ``base_currency`` costs ``rate`` quote units."""

    base_currency: str
    quote_currency: str
    rate: float
    as_of: datetime
    source: str
    quality: DataQuality = DataQuality.VALID

    def __post_init__(self) -> None:
        if not self.base_currency or not self.quote_currency:
            raise ValueError("FX currencies must be non-empty")
        if self.base_currency == self.quote_currency:
            raise ValueError("FX base and quote currencies must differ")
        require_positive(self.rate, "rate")
        object.__setattr__(self, "as_of", require_aware(self.as_of, "as_of"))
        if not self.source:
            raise ValueError("FX source must be non-empty")

    @property
    def pair(self) -> str:
        return f"{self.base_currency}/{self.quote_currency}"


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a currency conversion, including the rate path used."""

    ok: bool
    amount: float | None
    rate: float | None
    path: tuple[str, ...]
    as_of: datetime | None
    quality: DataQuality
    reason: str | None = None

    @classmethod
    def failure(cls, reason: str) -> ConversionResult:
        return cls(
            ok=False,
            amount=None,
            rate=None,
            path=(),
            as_of=None,
            quality=DataQuality.MISSING,
            reason=reason,
        )


@dataclass(frozen=True)
class AccountCapital:
    """Capital state for one account at one venue.

    ``cash_balance`` is reconciled cash in ``currency``. Reserved capacity and
    margin are separate fields and must never be added back to available cash.
    ``available_to_trade`` is the authoritative venue value where available.
    """

    account_id: str
    venue: str
    currency: str
    cash_balance: float
    equity_value: float
    account_equity: float
    reserved_order_capacity: float
    margin_requirement: float
    as_of: datetime
    source: str
    quality: DataQuality = DataQuality.VALID
    available_to_trade: float | None = None
    risk_budget_remaining: float | None = None
    liquidation_value_estimate: float | None = None

    def __post_init__(self) -> None:
        for name in ("account_id", "venue", "currency", "source"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        for name in (
            "cash_balance",
            "equity_value",
            "account_equity",
            "reserved_order_capacity",
            "margin_requirement",
        ):
            require_finite(getattr(self, name), name)
        for name in ("available_to_trade", "risk_budget_remaining", "liquidation_value_estimate"):
            value = getattr(self, name)
            if value is not None:
                require_finite(value, name)
        object.__setattr__(self, "as_of", require_aware(self.as_of, "as_of"))

    @property
    def free_cash(self) -> float:
        """Conservative internally-derived free cash.

        Reserved capacity and margin are subtracted once. This is not a
        substitute for the authoritative venue value.
        """
        return self.cash_balance - self.reserved_order_capacity - self.margin_requirement

    def conservative_available(self) -> float:
        """Min of authoritative and internally-derived availability.

        Using the minimum is deliberately conservative: if either source is
        wrong, the smaller number cannot overstate spendable capital.
        """
        derived = self.free_cash
        if self.available_to_trade is None:
            return derived
        return min(self.available_to_trade, derived)


@dataclass(frozen=True)
class OptionRiskInput:
    """Contract-level option risk data required for nonlinear normalization.

    Greeks are local sensitivities from a named model version. They are not a
    complete risk measure; max loss, margin, and scenario revaluation remain
    separate fields.
    """

    underlying_id: str
    underlying_price: float
    strike: float
    time_to_expiry_years: float
    risk_free_rate: float
    volatility: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    model_version: str
    option_right: str
    max_loss_per_contract: float | None = None
    margin_per_contract: float | None = None
    exercise_style: str = "american"
    settlement_type: str = "physical"

    def __post_init__(self) -> None:
        if not self.underlying_id:
            raise ValueError("underlying_id must be non-empty")
        if not self.model_version:
            raise ValueError("model_version must be non-empty")
        if self.option_right.lower() not in {"call", "put"}:
            raise ValueError("option_right must be 'call' or 'put'")
        for name in (
            "underlying_price",
            "strike",
            "time_to_expiry_years",
            "risk_free_rate",
            "volatility",
            "delta",
            "gamma",
            "vega",
            "theta",
            "rho",
        ):
            require_finite(getattr(self, name), name)
        if self.underlying_price <= 0 or self.strike <= 0:
            raise ValueError("option prices and strikes must be positive")
        if self.time_to_expiry_years < 0:
            raise ValueError("time_to_expiry_years must be non-negative")
        for name in ("max_loss_per_contract", "margin_per_contract"):
            value = getattr(self, name)
            if value is not None:
                require_non_negative(value, name)


@dataclass(frozen=True)
class PredictionOutcome:
    """One possible settlement outcome for a prediction contract."""

    label: str
    payout_per_contract: float
    probability: float | None = None

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("outcome label must be non-empty")
        require_non_negative(self.payout_per_contract, "payout_per_contract")
        if self.probability is not None:
            require_finite(self.probability, "probability")
            if not 0.0 <= self.probability <= 1.0:
                raise ValueError("probability must be in [0, 1]")


@dataclass(frozen=True)
class PredictionRiskInput:
    """Payoff model for a prediction-market position.

    The quoted probability is never treated as certainty of settlement. The
    full outcome schedule is retained so maximum loss and expected payoff can be
    computed explicitly.
    """

    event_id: str
    outcome_id: str
    entry_price_per_contract: float
    outcomes: tuple[PredictionOutcome, ...]
    platform: str
    settlement_status: str = "open"
    dependency_group: str | None = None
    fee_per_contract: float = 0.0

    def __post_init__(self) -> None:
        for name in ("event_id", "outcome_id", "platform"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        require_non_negative(self.entry_price_per_contract, "entry_price_per_contract")
        require_non_negative(self.fee_per_contract, "fee_per_contract")
        if not self.outcomes:
            raise ValueError("outcomes must be non-empty")
        probabilities = [o.probability for o in self.outcomes if o.probability is not None]
        if probabilities and len(probabilities) != len(self.outcomes):
            raise ValueError("probabilities must be supplied for every outcome or none")
        if probabilities and abs(sum(probabilities) - 1.0) > 1e-6:
            raise ValueError("outcome probabilities must sum to 1")


@dataclass(frozen=True)
class PositionRecord:
    """A reconciled position in one section and venue.

    ``quantity`` is signed: positive long, negative short. ``market_value`` is
    the signed source-reported value in ``currency``. For linear instruments it
    should equal ``quantity * multiplier * price``; the snapshot builder records
    a warning when the identity does not hold rather than silently replacing the
    source value.
    """

    position_id: str
    instrument_id: str
    section: Section
    venue: str
    quantity: float
    multiplier: float
    price: float
    currency: str
    market_value: float
    unrealized_pnl: float
    as_of: datetime
    source: str
    valuation_method: str
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.RECONCILED
    quality: DataQuality = DataQuality.VALID
    option: OptionRiskInput | None = None
    prediction: PredictionRiskInput | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("position_id", "instrument_id", "venue", "currency", "source", "valuation_method"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "section", as_section(self.section))
        require_finite(self.quantity, "quantity")
        require_positive(self.multiplier, "multiplier")
        require_non_negative(self.price, "price")
        require_finite(self.market_value, "market_value")
        require_finite(self.unrealized_pnl, "unrealized_pnl")
        object.__setattr__(self, "as_of", require_aware(self.as_of, "as_of"))

    @property
    def computed_market_value(self) -> float:
        return self.quantity * self.multiplier * self.price

    @property
    def is_long(self) -> bool:
        return self.quantity > 0.0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0.0


@dataclass(frozen=True)
class OpenOrderRecord:
    """An outstanding order. Its possible fill is exposure not yet a position."""

    order_id: str
    instrument_id: str
    section: Section
    venue: str
    side: OrderSide
    remaining_quantity: float
    order_type: str
    reference_price: float
    multiplier: float
    currency: str
    state: str
    as_of: datetime
    source: str
    reserved_capacity: float = 0.0
    limit_price: float | None = None
    reduces_position: bool = False
    quality: DataQuality = DataQuality.VALID
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("order_id", "instrument_id", "venue", "order_type", "currency", "state", "source"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "section", as_section(self.section))
        object.__setattr__(self, "side", self.side if isinstance(self.side, OrderSide) else OrderSide(self.side))
        require_non_negative(self.remaining_quantity, "remaining_quantity")
        require_positive(self.reference_price, "reference_price")
        require_positive(self.multiplier, "multiplier")
        require_non_negative(self.reserved_capacity, "reserved_capacity")
        if self.limit_price is not None:
            require_positive(self.limit_price, "limit_price")
        object.__setattr__(self, "as_of", require_aware(self.as_of, "as_of"))

    @property
    def signed_incremental_notional(self) -> float:
        """Signed exposure created if the remaining quantity fills."""
        direction = 1.0 if self.side == OrderSide.BUY else -1.0
        return direction * self.remaining_quantity * self.multiplier * self.reference_price

    @property
    def max_incremental_notional(self) -> float:
        return abs(self.signed_incremental_notional)


@dataclass(frozen=True)
class CommitmentRecord:
    """A pending obligation that consumes capital but is not an open order.

    ``included_in_reserved_capacity`` prevents double counting when a venue
    already reports the obligation inside reserved order capacity.
    """

    commitment_id: str
    section: Section
    kind: CommitmentKind
    amount: float
    currency: str
    as_of: datetime
    source: str
    resolved: bool = False
    included_in_reserved_capacity: bool = False
    quality: DataQuality = DataQuality.VALID
    description: str = ""

    def __post_init__(self) -> None:
        for name in ("commitment_id", "currency", "source"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "section", as_section(self.section))
        require_non_negative(self.amount, "amount")
        object.__setattr__(self, "as_of", require_aware(self.as_of, "as_of"))


@dataclass(frozen=True)
class SectionStatus:
    """Readiness, quality, and reconciliation state for one section."""

    section: Section
    readiness: SectionReadiness
    data_quality: DataQuality
    reconciliation_status: ReconciliationStatus
    last_update: datetime | None = None
    reason: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", as_section(self.section))
        if self.last_update is not None:
            object.__setattr__(self, "last_update", require_aware(self.last_update, "last_update"))

    @property
    def permits_new_risk(self) -> bool:
        return (
            self.readiness in {SectionReadiness.LIVE_ELIGIBLE, SectionReadiness.PAPER_ONLY}
            and self.data_quality == DataQuality.VALID
            and self.reconciliation_status == ReconciliationStatus.RECONCILED
        )


@dataclass(frozen=True)
class CapitalSummary:
    """Consolidated capital in the snapshot base currency.

    ``risk_budget_remaining`` and ``liquidation_value_estimate`` are ``None``
    when any account cannot supply them; they are never assumed to be zero.
    """

    base_currency: str
    cash_balance: float | None
    equity_value: float | None
    account_equity: float | None
    reserved_order_capacity: float | None
    margin_requirement: float | None
    available_to_trade: float | None
    commitments_not_reserved: float | None
    unsettled_proceeds: float | None
    risk_budget_remaining: float | None
    liquidation_value_estimate: float | None
    by_account: Mapping[str, AccountCapital] = field(default_factory=dict)
    conversion_complete: bool = True
    unconverted_accounts: tuple[str, ...] = ()

    @property
    def total_commitments(self) -> float | None:
        if self.reserved_order_capacity is None or self.commitments_not_reserved is None:
            return None
        return self.reserved_order_capacity + self.commitments_not_reserved

    @property
    def free_equity(self) -> float | None:
        commitments = self.total_commitments
        if self.equity_value is None or commitments is None:
            return None
        return self.equity_value - commitments


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Immutable, versioned consolidated portfolio state.

    Corrections create a new snapshot version; this object is never mutated.
    """

    snapshot_id: str
    snapshot_version: int
    snapshot_time: datetime
    source_cutoff_time: datetime
    base_currency: str
    capital_summary: CapitalSummary
    positions: tuple[PositionRecord, ...]
    open_orders: tuple[OpenOrderRecord, ...]
    commitments: tuple[CommitmentRecord, ...]
    section_status: Mapping[str, SectionStatus]
    source_refs: tuple[SourceRef, ...]
    data_quality_status: DataQuality
    completeness_status: SnapshotCompleteness
    reconciliation_status: ReconciliationStatus
    is_usable_for_risk_increase: bool
    fingerprint: str
    schema_version: str = "p11-snapshot-1"
    calculation_version: str = "p11-state-1"
    known_limitations: tuple[str, ...] = ()
    fx_rates: tuple[FxRate, ...] = ()

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id must be non-empty")
        if self.snapshot_version < 1:
            raise ValueError("snapshot_version must be >= 1")
        object.__setattr__(self, "snapshot_time", require_aware(self.snapshot_time, "snapshot_time"))
        object.__setattr__(
            self, "source_cutoff_time", require_aware(self.source_cutoff_time, "source_cutoff_time")
        )
        if not self.base_currency:
            raise ValueError("base_currency must be non-empty")

    def position(self, position_id: str) -> PositionRecord | None:
        return next((p for p in self.positions if p.position_id == position_id), None)

    def section(self, section: Section | str) -> SectionStatus | None:
        return self.section_status.get(as_section(section).value)

    def age_seconds(self, now: datetime) -> float:
        return (require_aware(now, "now") - self.snapshot_time).total_seconds()


@dataclass(frozen=True)
class InstrumentSpec:
    """Validated instrument metadata and risk mapping."""

    instrument_id: str
    symbol: str
    section: Section
    asset_class: AssetClass
    instrument_type: InstrumentType
    venue: str
    currency: str
    settlement_currency: str
    multiplier: float = 1.0
    tick_size: float = 0.01
    lot_size: float = 1.0
    underlying_id: str | None = None
    issuer_id: str | None = None
    event_id: str | None = None
    expiry: datetime | None = None
    exercise_style: str | None = None
    settlement_style: str | None = None
    trading_calendar: str = "24/7"
    price_source: str = "unknown"
    liquidity_class: str = "unknown"
    valuation_method: str = "market_value"
    risk_model_version: str = "p11-instrument-1"
    eligibility: InstrumentEligibility = InstrumentEligibility.ELIGIBLE
    base_currency: str | None = None
    quote_currency: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("instrument_id", "symbol", "venue", "currency", "settlement_currency"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "section", as_section(self.section))
        require_positive(self.multiplier, "multiplier")
        require_positive(self.tick_size, "tick_size")
        require_positive(self.lot_size, "lot_size")
        if self.expiry is not None:
            object.__setattr__(self, "expiry", require_aware(self.expiry, "expiry"))
        if self.instrument_type in {InstrumentType.CALL, InstrumentType.PUT} and (
            not self.underlying_id or self.expiry is None
        ):
            raise ValueError("options require underlying_id and expiry")
        if self.instrument_type == InstrumentType.FX_PAIR and (
            not self.base_currency or not self.quote_currency
        ):
            raise ValueError("FX pairs require base_currency and quote_currency")
        if self.asset_class == AssetClass.PREDICTION and not self.event_id:
            raise ValueError("prediction instruments require event_id")

    @property
    def is_derivative(self) -> bool:
        return self.asset_class in {AssetClass.OPTION, AssetClass.FOREX}


@dataclass(frozen=True)
class RiskFactor:
    factor_id: str
    name: str
    kind: FactorKind
    currency: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.factor_id or not self.name:
            raise ValueError("factor_id and name must be non-empty")


@dataclass(frozen=True)
class FactorLoading:
    instrument_id: str
    factor_id: str
    loading: float

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.factor_id:
            raise ValueError("factor ids must be non-empty")
        require_finite(self.loading, "loading")


@dataclass(frozen=True)
class NormalizedExposure:
    """One instrument's exposure in a common risk framework.

    ``market_value`` is signed and in ``base_currency``. ``delta_equivalent``,
    ``gamma``, ``vega`` and ``theta`` are only populated for options and are in
    the units documented by ``method``. ``max_loss`` is a positive magnitude in
    ``base_currency``; ``None`` means the loss is undefined or unbounded and is
    never replaced by zero.
    """

    instrument_id: str
    section: Section
    asset_class: AssetClass
    venue: str
    currency: str
    base_currency: str
    market_value: float
    gross_market_value: float
    method: str
    model_version: str
    quality: DataQuality
    as_of: datetime
    delta_equivalent: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    underlying_price: float | None = None
    underlying_currency: str | None = None
    max_loss: float | None = None
    margin_requirement: float | None = None
    expected_payoff: float | None = None
    liquidity_horizon_days: float | None = None
    factor_exposures: Mapping[str, float] = field(default_factory=dict)
    currency_exposures: Mapping[str, float] = field(default_factory=dict)
    uncertainty_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", as_section(self.section))
        require_finite(self.market_value, "market_value")
        require_non_negative(self.gross_market_value, "gross_market_value")
        object.__setattr__(self, "as_of", require_aware(self.as_of, "as_of"))
        for name in (
            "delta_equivalent",
            "delta",
            "gamma",
            "vega",
            "theta",
            "underlying_price",
            "max_loss",
            "margin_requirement",
            "expected_payoff",
            "liquidity_horizon_days",
        ):
            value = getattr(self, name)
            if value is not None:
                require_finite(value, name)
        if self.max_loss is not None and self.max_loss < 0:
            raise ValueError("max_loss must be non-negative")


@dataclass(frozen=True)
class SectionExposure:
    section: Section
    gross_market_value: float
    net_market_value: float
    delta_equivalent: float | None
    max_loss: float | None
    margin_requirement: float | None
    expected_payoff: float | None
    liquidity_horizon_days: float | None
    instrument_count: int
    uncertainty_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", as_section(self.section))


@dataclass(frozen=True)
class NormalizationFailure:
    instrument_id: str
    section: Section
    reason: str
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", as_section(self.section))


@dataclass(frozen=True)
class ExposureSnapshot:
    exposure_snapshot_id: str
    portfolio_snapshot_id: str
    normalization_version: str
    base_currency: str
    instrument_exposures: tuple[NormalizedExposure, ...]
    order_exposures: tuple[NormalizedExposure, ...]
    section_exposures: Mapping[str, SectionExposure]
    factor_exposures: Mapping[str, float]
    currency_exposures: Mapping[str, float]
    concentration_metrics: Mapping[str, float]
    liquidity_metrics: Mapping[str, float]
    uncertainty_flags: tuple[str, ...]
    failures: tuple[NormalizationFailure, ...]
    is_complete: bool
    coverage_ratio: float
    created_at: datetime
    schema_version: str = "p11-exposure-1"

    def __post_init__(self) -> None:
        if not self.exposure_snapshot_id or not self.portfolio_snapshot_id:
            raise ValueError("exposure and portfolio snapshot ids must be non-empty")
        object.__setattr__(self, "created_at", require_aware(self.created_at, "created_at"))
        require_finite(self.coverage_ratio, "coverage_ratio")

    def section(self, section: Section | str) -> SectionExposure | None:
        return self.section_exposures.get(as_section(section).value)


@dataclass(frozen=True)
class ConstraintResult:
    """Result of one independently evaluated allocation constraint."""

    name: str
    severity: ConstraintSeverity
    passed: bool
    actual: float | None
    limit: float | None
    unit: str
    message: str = ""

    @property
    def hard(self) -> bool:
        return self.severity == ConstraintSeverity.HARD


@dataclass(frozen=True)
class RiskSummary:
    """Compact risk snapshot attached to proposals and validations."""

    volatility: float
    var_95: float
    var_99: float
    expected_shortfall_95: float
    expected_shortfall_99: float
    max_drawdown: float
    hhi_instrument: float
    hhi_section: float
    gross_exposure: float
    net_exposure: float
    max_loss: float | None
    margin_requirement: float | None
    liquidity_horizon_days: float | None

    @classmethod
    def empty(cls) -> RiskSummary:
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, None)


@dataclass(frozen=True)
class CostEstimate:
    """Estimated cost of one proposed trade. Positive numbers are costs."""

    instrument_id: str
    venue: str
    asset_class: AssetClass
    expected_notional: float
    spread_cost: float
    fee_cost: float
    slippage_cost: float
    impact_cost: float
    funding_cost: float
    borrow_cost: float
    conversion_cost: float
    settlement_cost: float
    total_cost: float
    stress_cost: float
    participation_rate: float
    model_version: str
    as_of: datetime
    coverage: DataQuality = DataQuality.VALID
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def total_bps(self) -> float:
        if self.expected_notional == 0:
            return 0.0
        return self.total_cost / self.expected_notional * 10_000.0


@dataclass(frozen=True)
class CostSummary:
    total_cost: float
    stress_cost: float
    by_component: Mapping[str, float]
    by_instrument: Mapping[str, float]
    model_version: str
    coverage: DataQuality
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskBudgetPolicy:
    """Approved portfolio policy. Hard limits live here, not in the optimizer."""

    policy_version: str = "p11-budget-1"
    global_max_gross_exposure: float = 1_000_000.0
    global_max_net_exposure: float = 1_000_000.0
    global_max_var: float | None = None
    global_max_expected_shortfall: float | None = None
    global_max_stress_loss: float | None = None
    global_max_margin: float | None = None
    global_max_turnover: float = 1.0
    global_max_concentration_hhi: float = 0.25
    max_leverage: float = 2.0
    max_single_instrument_weight: float = 0.25
    max_illiquid_weight: float = 0.30
    min_cash_reserve_pct: float = 0.05
    incomplete_data_haircut: float = 0.5
    section_caps: tuple[tuple[str, float], ...] = ()
    section_min_budget: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "global_max_gross_exposure",
            "global_max_net_exposure",
            "global_max_turnover",
            "global_max_concentration_hhi",
            "max_leverage",
            "max_single_instrument_weight",
            "max_illiquid_weight",
            "min_cash_reserve_pct",
            "incomplete_data_haircut",
            "section_min_budget",
        ):
            require_non_negative(getattr(self, name), name)
        for name in (
            "global_max_var",
            "global_max_expected_shortfall",
            "global_max_stress_loss",
            "global_max_margin",
        ):
            value = getattr(self, name)
            if value is not None:
                require_non_negative(value, name)
        for section, cap in self.section_caps:
            as_section(section)
            require_non_negative(cap, "section cap")

    def cap_for(self, section: Section | str) -> float | None:
        key = as_section(section).value
        return dict(self.section_caps).get(key)


@dataclass(frozen=True)
class SectionBudget:
    section: Section
    capital_budget: float
    risk_budget: float
    max_gross_exposure: float
    current_gross_exposure: float
    current_net_exposure: float
    readiness: SectionReadiness
    eligible_for_increase: bool
    binding_constraints: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", as_section(self.section))

    @property
    def utilization(self) -> float:
        if self.max_gross_exposure <= 0:
            return 0.0
        return self.current_gross_exposure / self.max_gross_exposure


@dataclass(frozen=True)
class RiskBudgetProposal:
    budget_id: str
    portfolio_snapshot_id: str
    exposure_snapshot_id: str
    policy_version: str
    method: str
    section_budgets: tuple[SectionBudget, ...]
    total_capital_budget: float
    total_risk_budget: float
    binding_constraints: tuple[str, ...]
    warnings: tuple[str, ...]
    is_valid: bool
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", require_aware(self.created_at, "created_at"))
        require_non_negative(self.total_capital_budget, "total_capital_budget")
        require_non_negative(self.total_risk_budget, "total_risk_budget")

    def budget_for(self, section: Section | str) -> SectionBudget | None:
        key = as_section(section)
        return next((b for b in self.section_budgets if b.section == key), None)


class OptimizationMethod(str, Enum):
    NO_REBALANCE = "no_rebalance"
    EQUAL_WEIGHT = "equal_weight"
    INVERSE_VOLATILITY = "inverse_volatility"
    MINIMUM_VARIANCE = "minimum_variance"
    RISK_PARITY = "risk_parity"
    MEAN_VARIANCE = "mean_variance"
    CVAR = "cvar"


@dataclass(frozen=True)
class OptimizationConfig:
    method: OptimizationMethod
    risk_aversion: float = 1.0
    long_only: bool = True
    fully_invested: bool = True
    lower_bound: float = 0.0
    upper_bound: float = 1.0
    max_iterations: int = 500
    tolerance: float = 1e-8
    turnover_limit: float = 1.0
    cost_penalty: float = 0.0
    min_trade_weight: float = 0.0
    require_comparable_assets: bool = True
    confidence: float = 0.95

    def __post_init__(self) -> None:
        if not isinstance(self.method, OptimizationMethod):
            object.__setattr__(self, "method", OptimizationMethod(self.method))
        require_non_negative(self.risk_aversion, "risk_aversion")
        require_finite(self.lower_bound, "lower_bound")
        require_finite(self.upper_bound, "upper_bound")
        if self.lower_bound > self.upper_bound:
            raise ValueError("lower_bound cannot exceed upper_bound")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        require_positive(self.tolerance, "tolerance")
        require_non_negative(self.turnover_limit, "turnover_limit")
        require_non_negative(self.cost_penalty, "cost_penalty")
        require_non_negative(self.min_trade_weight, "min_trade_weight")
        if not 0.5 < self.confidence < 1.0:
            raise ValueError("confidence must be in (0.5, 1.0)")


@dataclass(frozen=True)
class AllocationResult:
    """Typed optimizer output. ``weights`` is ``None`` on failure by design."""

    status: SolverStatus
    method: OptimizationMethod
    instrument_ids: tuple[str, ...]
    weights: tuple[float, ...] | None
    current_weights: tuple[float, ...]
    expected_return: float | None
    expected_volatility: float | None
    turnover: float | None
    estimated_cost: CostSummary | None
    constraint_results: tuple[ConstraintResult, ...]
    solver_message: str
    iterations: int
    input_fingerprint: str
    warnings: tuple[str, ...] = ()
    attempted_weights: tuple[float, ...] | None = None

    @property
    def successful(self) -> bool:
        return self.status in {SolverStatus.OPTIMAL, SolverStatus.APPROXIMATE} and self.weights is not None


@dataclass(frozen=True)
class AllocationTarget:
    instrument_id: str
    section: Section
    current_weight: float
    proposed_weight: float
    current_exposure: float
    proposed_exposure: float
    delta_exposure: float
    action: ProposalAction
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", as_section(self.section))


@dataclass(frozen=True)
class Explanation:
    """Structured, evidence-based explanation. No invented rationale."""

    observed_facts: tuple[str, ...]
    statistical_estimates: tuple[str, ...]
    optimization_results: tuple[str, ...]
    scenario_assumptions: tuple[str, ...]
    policy_limits: tuple[str, ...]
    missing_information: tuple[str, ...]
    model_limitations: tuple[str, ...]


@dataclass(frozen=True)
class AllocationProposal:
    proposal_id: str
    portfolio_snapshot_id: str
    exposure_snapshot_id: str
    risk_budget_proposal_id: str
    policy_version: str
    optimizer_version: str
    method: OptimizationMethod
    solver_status: SolverStatus
    proposed_budgets: Mapping[str, float]
    proposed_targets: tuple[AllocationTarget, ...]
    estimated_costs: CostSummary
    estimated_turnover: float
    risk_summary: RiskSummary
    stress_report_id: str | None
    stress_passed: bool
    constraint_results: tuple[ConstraintResult, ...]
    data_quality_status: DataQuality
    approval_state: ProposalState
    action: ProposalAction
    expires_at: datetime
    created_at: datetime
    input_cutoff_time: datetime
    explanation: Explanation
    known_limitations: tuple[str, ...] = ()
    requires_human_approval: bool = True
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        for name in (
            "proposal_id",
            "portfolio_snapshot_id",
            "exposure_snapshot_id",
            "risk_budget_proposal_id",
            "policy_version",
            "optimizer_version",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "expires_at", require_aware(self.expires_at, "expires_at"))
        object.__setattr__(self, "created_at", require_aware(self.created_at, "created_at"))
        object.__setattr__(
            self, "input_cutoff_time", require_aware(self.input_cutoff_time, "input_cutoff_time")
        )
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        require_non_negative(self.estimated_turnover, "estimated_turnover")

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= utc_now()

    def expired_at(self, now: datetime) -> bool:
        return self.expires_at <= require_aware(now, "now")

    @property
    def is_risk_increasing(self) -> bool:
        return any(t.action == ProposalAction.INCREASE for t in self.proposed_targets)


@dataclass(frozen=True)
class RebalanceChange:
    instrument_id: str
    section: Section
    current_exposure: float
    target_exposure: float
    delta_exposure: float
    delta_weight: float
    action: ProposalAction
    estimated_cost: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", as_section(self.section))


@dataclass(frozen=True)
class RebalanceProposal:
    """Proposed changes only. This type has no path to order submission."""

    rebalance_id: str
    allocation_proposal_id: str
    portfolio_snapshot_id: str
    changes: tuple[RebalanceChange, ...]
    estimated_costs: CostSummary
    required_capital: Mapping[str, float]
    preconditions: tuple[str, ...]
    validation_state: ProposalState
    approval_state: ProposalState
    action: ProposalAction
    expires_at: datetime
    created_at: datetime
    idempotency_key: str = ""
    known_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("rebalance_id", "allocation_proposal_id", "portfolio_snapshot_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "expires_at", require_aware(self.expires_at, "expires_at"))
        object.__setattr__(self, "created_at", require_aware(self.created_at, "created_at"))

    @property
    def is_risk_increasing(self) -> bool:
        return any(c.action == ProposalAction.INCREASE for c in self.changes)


@dataclass(frozen=True)
class ReconciliationIssue:
    """A discrepancy between AXEL's view and an authoritative source."""

    issue_id: str
    source_systems: tuple[str, ...]
    entity_type: str
    entity_ref: str
    expected_value: Mapping[str, Any]
    observed_value: Mapping[str, Any]
    unit: str
    detected_at: datetime
    severity: IssueSeverity
    status: IssueStatus = IssueStatus.OPEN
    difference: float | None = None
    resolution_ref: str | None = None
    responsible: str | None = None
    blocks_risk_increase: bool = True

    def __post_init__(self) -> None:
        if not self.issue_id or not self.entity_type or not self.entity_ref:
            raise ValueError("issue identity fields must be non-empty")
        if not self.source_systems:
            raise ValueError("source_systems must be non-empty")
        object.__setattr__(self, "detected_at", require_aware(self.detected_at, "detected_at"))
        if self.difference is not None:
            require_finite(self.difference, "difference")

    @property
    def is_open(self) -> bool:
        return self.status in {IssueStatus.OPEN, IssueStatus.INVESTIGATING}

    @property
    def blocks_new_risk(self) -> bool:
        return (
            self.is_open
            and self.blocks_risk_increase
            and self.severity in {IssueSeverity.CRITICAL, IssueSeverity.WARNING}
        )


@dataclass(frozen=True)
class ValidationCheck:
    code: str
    passed: bool
    detail: str
    hard: bool = True


@dataclass(frozen=True)
class BaselineComparison:
    baseline_method: OptimizationMethod
    weight_distance: float
    volatility_difference: float
    cost_difference: float
    turnover_difference: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StabilityResult:
    max_weight_distance: float
    mean_weight_distance: float
    perturbed_cases: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioAlert:
    alert_id: str
    severity: IssueSeverity
    code: str
    message: str
    detected_at: datetime
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "detected_at", require_aware(self.detected_at, "detected_at"))


__all__ = [
    "AccountCapital",
    "AllocationProposal",
    "AllocationResult",
    "AllocationTarget",
    "AssetClass",
    "BaselineComparison",
    "CapitalSummary",
    "CommitmentKind",
    "CommitmentRecord",
    "ConstraintResult",
    "ConstraintSeverity",
    "ConversionResult",
    "CostEstimate",
    "CostSummary",
    "DataQuality",
    "Explanation",
    "ExposureSnapshot",
    "FactorKind",
    "FactorLoading",
    "FreshnessPolicy",
    "FxRate",
    "InstrumentEligibility",
    "InstrumentSpec",
    "InstrumentType",
    "IssueSeverity",
    "IssueStatus",
    "NormalizationFailure",
    "NormalizedExposure",
    "OpenOrderRecord",
    "OptimizationConfig",
    "OptimizationMethod",
    "OptionRiskInput",
    "PortfolioAlert",
    "PortfolioSnapshot",
    "PositionRecord",
    "PredictionOutcome",
    "PredictionRiskInput",
    "ProposalAction",
    "ProposalState",
    "RebalanceChange",
    "RebalanceProposal",
    "ReconciliationIssue",
    "ReconciliationStatus",
    "RiskBudgetPolicy",
    "RiskBudgetProposal",
    "RiskFactor",
    "RiskSummary",
    "Section",
    "SectionBudget",
    "SectionExposure",
    "SectionReadiness",
    "SectionStatus",
    "SnapshotCompleteness",
    "SolverStatus",
    "SourceRef",
    "StabilityResult",
    "ValidationCheck",
    "ValidationDecision",
    "as_section",
    "require_aware",
    "require_finite",
    "require_non_negative",
    "require_positive",
    "utc_now",
]
