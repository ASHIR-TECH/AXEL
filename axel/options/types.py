"""
Core domain types for the Options section (Phase 8).

All types are frozen dataclasses or enums — pure data, no behavior, no I/O.
These are the internal domain models used by pricing, payoff, risk, and
validation. The Pydantic boundary contracts live in contracts.py.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Self


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


class OptionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class ExerciseStyle(str, Enum):
    AMERICAN = "american"
    EUROPEAN = "european"


class SettlementType(str, Enum):
    PHYSICAL = "physical"
    CASH = "cash"


@dataclass(frozen=True)
class Greeks:
    """Option Greeks in standard units.

    delta:      dPrice / dUnderlying (unitless)
    gamma:      dDelta / dUnderlying (1/price)
    vega:       dPrice / dVolatility (price per 1.0 vol, i.e. per 100 vol points)
    theta:      dPrice / dTime (price per year, typically quoted per day)
    rho:        dPrice / dRiskFreeRate (price per 1.0 rate)
    iv:         implied volatility (annualized, decimal e.g. 0.35 = 35%)
    model:      pricing model name/version used to derive these values
    """

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    iv: float
    model: str = "black_scholes"
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class OptionContract:
    """Immutable option contract reference data.

    Stable contract ID is venue + underlying + right + strike + expiry.
    Never fills missing metadata with guesses — unknown contract terms
    mean fail closed.
    """

    contract_id: str
    venue: str
    underlying_id: str
    underlying_symbol: str
    right: OptionRight
    strike: float
    expiry: datetime  # UTC expiry / last trading cutoff
    multiplier: float = 100.0  # contract multiplier (shares per contract)
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN
    settlement_type: SettlementType = SettlementType.PHYSICAL
    currency: str = "USD"
    last_trading_cutoff: datetime | None = None
    exercise_cutoff: datetime | None = None
    is_active: bool = True

    def is_expired(self, now: datetime | None = None) -> bool:
        """True if the contract is past its last trading cutoff."""
        check_now = now or datetime.now(UTC)
        cutoff = self.last_trading_cutoff or self.expiry
        return check_now >= cutoff

    def with_venue(self, venue: str) -> Self:
        """Return a copy with a different venue (for venue-adapter mapping)."""
        return OptionContract(
            contract_id=self.contract_id,
            venue=venue,
            underlying_id=self.underlying_id,
            underlying_symbol=self.underlying_symbol,
            right=self.right,
            strike=self.strike,
            expiry=self.expiry,
            multiplier=self.multiplier,
            exercise_style=self.exercise_style,
            settlement_type=self.settlement_type,
            currency=self.currency,
            last_trading_cutoff=self.last_trading_cutoff,
            exercise_cutoff=self.exercise_cutoff,
            is_active=self.is_active,
        )


@dataclass(frozen=True)
class OptionQuote:
    """A point-in-time option quote with quality metadata.

    event_time: when the quote occurred on the venue
    receive_time: when AXEL ingested it (>= event_time, never look-ahead)
    bid/ask: may be None if only last trade is available
    quality: one of VALID, STALE, CROSSED, WIDE_SPREAD, LOW_DEPTH, ...
    """

    contract_id: str
    event_time: datetime
    receive_time: datetime
    bid: float | None = None
    bid_size: float | None = None
    ask: float | None = None
    ask_size: float | None = None
    last_price: float | None = None
    last_size: float | None = None
    venue: str = ""
    quality: str = "VALID"
    source: str = ""
    schema_version: str = "canonical-1"

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        if self.last_price is not None:
            return self.last_price
        return None

    @property
    def spread(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.ask > self.bid:
            return self.ask - self.bid
        return None

    @property
    def is_crossed(self) -> bool:
        return (
            self.bid is not None
            and self.ask is not None
            and self.bid >= self.ask
        )

    def is_stale(self, max_age_seconds: float = 60.0, now: datetime | None = None) -> bool:
        check_now = now or datetime.now(UTC)
        age = (check_now - self.receive_time).total_seconds()
        return age > max_age_seconds


@dataclass(frozen=True)
class OptionLeg:
    """A single leg of a multi-leg options structure.

    side: LONG (buy) or SHORT (sell) the option
    ratio: number of contracts (positive integer)
    entry_price: limit price used for entry (may be None for market orders)
    """

    contract: OptionContract
    side: OptionSide
    ratio: int = 1
    entry_price: float | None = None

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError(f"Leg ratio must be positive, got {self.ratio}")

    @property
    def notional_per_contract(self) -> float | None:
        if self.entry_price is not None:
            return self.entry_price * self.contract.multiplier
        if self.contract.last_trading_cutoff is not None:
            # Fall back to quote mid if available via external lookup; here None signals unknown.
            return None
        return None


@dataclass(frozen=True)
class OptionsProposal:
    """An untrusted options trade proposal emitted by an Options Analyst /
    Expert Panel. Crosses the LLM/deterministic boundary via contracts.py.

    This is the internal domain mirror of the Pydantic contract.
    """

    proposal_id: str
    strategy_id: str
    underlying_id: str
    underlying_symbol: str
    legs: tuple[OptionLeg, ...]
    created_at: datetime
    valid_until: datetime
    forecast_version: str
    data_snapshot_id: str
    limit_policy: str = "limit_only"  # "limit_only" | "market_allowed"
    max_loss: float | None = None
    max_gain: float | None = None
    break_even_points: tuple[float, ...] = ()
    greeks: Greeks | None = None
    liquidity_snapshot: dict | None = None
    stress_summary: dict | None = None
    thesis: str = ""
    uncertainty: str = ""
    agent_trace_id: str | None = None

    @property
    def is_defined_risk(self) -> bool:
        """True if the structure has a calculable, bounded worst-case loss."""
        return self.max_loss is not None and self.max_loss >= 0 and self.max_gain is not None

    @property
    def net_debit_or_credit(self) -> float | None:
        """Net premium paid (positive) or received (negative) per contract set."""
        if not self.legs:
            return None
        total = 0.0
        for leg in self.legs:
            if leg.entry_price is None:
                return None
            sign = 1.0 if leg.side == OptionSide.LONG else -1.0
            total += sign * leg.entry_price * leg.ratio * leg.contract.multiplier
        return total

    def is_expired(self, now: datetime | None = None) -> bool:
        check_now = now or datetime.now(UTC)
        return check_now >= self.valid_until


@dataclass(frozen=True)
class OptionsRiskDecision:
    """Deterministic validator output for an options proposal."""

    proposal_id: str
    verdict: str  # "ACCEPT" | "REJECT" | "REQUIRES_REVIEW"
    reasons: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    max_loss: float | None = None
    max_gain: float | None = None
    break_even_points: tuple[float, ...] = ()
    margin_buffer_usd: float = 0.0
    config_hash: str = ""
    validated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def accepted(self) -> bool:
        return self.verdict == "ACCEPT"

    @property
    def rejected(self) -> bool:
        return self.verdict == "REJECT"

    @property
    def requires_review(self) -> bool:
        return self.verdict == "REQUIRES_REVIEW"
