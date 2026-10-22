"""
Core domain types for the Bets / Prediction Markets section (Phase 9).

All types are frozen dataclasses or enums — pure data, no behavior, no I/O.
The Pydantic boundary contracts live in contracts.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class OddsFormat(str, Enum):
    DECIMAL = "decimal"      # e.g. 2.50 (payout per unit stake including stake)
    FRACTIONAL = "fractional"  # e.g. 3/2 (profit per unit stake)
    AMERICAN = "american"    # e.g. +150 / -200


class MarketStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    VOIDED = "voided"


class OutcomeType(str, Enum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"
    DEAD_HEAT = "dead_heat"


class SettlementOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"
    DEAD_HEAT = "dead_heat"


@dataclass(frozen=True)
class Event:
    """Canonical event (sports match, election, etc.) with identity metadata."""

    event_id: str
    sport: str
    league: str | None
    participants: tuple[str, ...]  # teams/players/candidates
    start_time: datetime  # UTC
    venue: str | None
    identity_confidence: float = 1.0  # 0-1, how confident we are this is the right event

    def __post_init__(self) -> None:
        if not 0.0 <= self.identity_confidence <= 1.0:
            raise ValueError(f"identity_confidence must be in [0,1], got {self.identity_confidence}")
        if not self.participants:
            raise ValueError("Event must have at least one participant")


@dataclass(frozen=True)
class Market:
    """A betting market on an event with rules and payout conventions."""

    market_id: str
    event_id: str
    provider: str
    market_type: str  # e.g. "moneyline", "spread", "total", "yes/no"
    rules_version: str
    outcomes: tuple[str, ...]  # outcome names, e.g. ("home", "away", "draw")
    status: MarketStatus = MarketStatus.ACTIVE
    fees: float = 0.0  # commission rate on net winnings (decimal, e.g. 0.02 = 2%)
    close_time: datetime | None = None  # when betting closes
    settlement_authority: str = ""  # who/what settles the market

    def __post_init__(self) -> None:
        if not 0.0 <= self.fees <= 1.0:
            raise ValueError(f"fees must be in [0,1], got {self.fees}")
        if not self.outcomes:
            raise ValueError("Market must have at least one outcome")


@dataclass(frozen=True)
class OddsSnapshot:
    """Point-in-time odds for one market outcome."""

    snapshot_id: str
    market_id: str
    outcome: str
    odds: dict  # format: {"decimal": 2.50, ...} or provider-specific
    odds_format: OddsFormat = OddsFormat.DECIMAL
    liquidity: float | None = None  # available volume at this price
    limits: dict | None = None  # min/max stake per provider
    status: MarketStatus = MarketStatus.ACTIVE
    event_time: datetime | None = None  # when odds were quoted on venue
    receive_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = ""
    quality_flags: tuple[str, ...] = ()  # e.g. ("STALE", "LOW_DEPTH")

    @property
    def decimal_odds(self) -> float | None:
        """Extract decimal odds from the odds dict."""
        if isinstance(self.odds, dict):
            return self.odds.get("decimal")
        return None

    def is_stale(self, max_age_seconds: float = 60.0, now: datetime | None = None) -> bool:
        check_now = now or datetime.now(UTC)
        age = (check_now - self.receive_time).total_seconds()
        return age > max_age_seconds


@dataclass(frozen=True)
class Forecast:
    """A probability forecast for a market's outcomes."""

    forecast_id: str
    market_id: str
    event_id: str
    probabilities: dict[str, float]  # outcome -> probability (0-1, sums to ~1)
    model: str
    model_version: str
    calibrator_version: str | None = None
    forecast_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    training_cutoff: datetime | None = None
    uncertainty: dict[str, float] | None = None  # outcome -> std dev of probability
    data_snapshot_ids: tuple[str, ...] = ()
    missingness: dict[str, bool] | None = None  # feature -> was missing
    ood_flags: tuple[str, ...] = ()  # out-of-distribution flags

    def __post_init__(self) -> None:
        total = sum(self.probabilities.values())
        if not 0.99 <= total <= 1.01:
            raise ValueError(f"Probabilities must sum to ~1.0, got {total:.4f}")
        for outcome, p in self.probabilities.items():
            if not 0.0 <= p <= 1.0:
                raise ValueError(f"Probability for {outcome} must be in [0,1], got {p}")


@dataclass(frozen=True)
class ValueAssessment:
    """Deterministic value engine output for a candidate bet."""

    assessment_id: str
    market_id: str
    outcome: str
    price: float  # decimal odds available at decision time
    market_implied_prob: float  # de-vigged market probability
    estimated_prob: float  # our forecast probability
    ev: float  # expected value per unit stake
    conservative_ev: float  # EV after uncertainty haircut
    costs: float  # commission + execution costs per unit stake
    edge_threshold: float  # minimum conservative EV required (versioned per market type)
    expiry: datetime  # when this assessment expires
    reason_codes: tuple[str, ...] = ()
    metadata: dict | None = None

    @property
    def has_edge(self) -> bool:
        return self.conservative_ev > self.edge_threshold


@dataclass(frozen=True)
class BetProposal:
    """An untrusted bet proposal emitted by an analyst agent (proposal-only)."""

    proposal_id: str
    event_id: str
    market_id: str
    outcome: str
    requested_stake: float  # currency units
    forecast_id: str
    price_limit: float | None = None  # max decimal odds to accept (worst price)
    rationale: str = ""
    valid_until: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.requested_stake <= 0:
            raise ValueError(f"requested_stake must be positive, got {self.requested_stake}")


@dataclass(frozen=True)
class BetIntent:
    """Deterministic intent to place a bet (output of the value engine + sizer)."""

    intent_id: str
    proposal_id: str
    event_id: str
    market_id: str
    outcome: str
    stake: float  # currency units, deterministic output
    price: float  # decimal odds at decision time
    ev: float
    conservative_ev: float
    kelly_fraction: float  # fraction of bankroll (may be 0 = no bet)
    bankroll_after: float  # projected bankroll if placed
    valid_until: datetime
    reason_codes: tuple[str, ...] = ()

    @property
    def is_no_bet(self) -> bool:
        return self.stake <= 0 or self.kelly_fraction <= 0


@dataclass(frozen=True)
class BetOrder:
    """An order submitted to the paper/execution adapter."""

    order_id: str
    intent_id: str
    event_id: str
    market_id: str
    outcome: str
    stake: float
    price: float
    side: str  # "back" or "lay" (for exchanges) — default "back"
    status: str = "submitted"  # submitted, accepted, rejected, filled, cancelled
    broker_order_id: str | None = None
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class BetFill:
    """An execution fill for a bet order."""

    fill_id: str
    order_id: str
    event_id: str
    outcome: str
    stake: float
    price: float
    commission: float = 0.0
    settled: bool = False
    filled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class OpenExposure:
    """Current open exposure for correlation and bankroll tracking."""

    event_id: str
    market_id: str
    outcome: str
    stake: float
    potential_payout: float
    potential_loss: float
    settled: bool = False
