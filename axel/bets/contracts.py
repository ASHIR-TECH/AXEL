"""
Versioned Pydantic contracts crossing the LLM / deterministic boundary for
the Bets / Prediction Markets section (Phase 9).

These are the ONLY data structures allowed to cross the trust threshold.
Frozen and strict: `extra="forbid"`. Agents emit `BetProposal`; the
deterministic validator emits `ValueAssessment`; the sizer emits `BetIntent`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from axel.core.ids import generate_id


class ContractBase(BaseModel):
    """Base model with strict validation and frozen defaults."""
    model_config = ConfigDict(extra="forbid", strict=True)


class BetProposal(ContractBase):
    """
    Emitted by an analyst agent (untrusted).
    Proposes a bet for deterministic evaluation — never places a bet directly.
    """

    id: str = Field(default_factory=lambda: generate_id("betprop"))
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_id: str
    market_id: str
    outcome: str
    requested_stake: float = Field(gt=0.0)
    price_limit: float | None = Field(default=None, gt=0.0)
    forecast_id: str
    rationale: str = Field(default="")
    valid_until: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("valid_until")
    @classmethod
    def _validate_valid_until(cls, v: datetime, info) -> datetime:
        data = info.data
        if "ts" in data and v <= data["ts"]:
            raise ValueError("valid_until must be strictly after ts")
        return v


class ValueAssessment(ContractBase):
    """
    Produced exclusively by the deterministic Value Engine.
    Recomputes implied probability, EV, conservative EV, and edge threshold.
    """

    id: str = Field(default_factory=lambda: generate_id("betval"))
    proposal_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    market_id: str
    outcome: str
    price: float = Field(gt=0.0)
    market_implied_prob: float = Field(ge=0.0, le=1.0)
    estimated_prob: float = Field(ge=0.0, le=1.0)
    ev: float
    conservative_ev: float
    costs: float = Field(ge=0.0)
    edge_threshold: float
    expiry: datetime
    reason_codes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None


class BetIntent(ContractBase):
    """
    Produced by the deterministic Sizer.
    Stake sizing output (fractional Kelly or flat stake) — never full Kelly
    by default, never martingale.
    """

    id: str = Field(default_factory=lambda: generate_id("betintent"))
    proposal_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_id: str
    market_id: str
    outcome: str
    stake: float = Field(ge=0.0)
    price: float = Field(gt=0.0)
    ev: float
    conservative_ev: float
    kelly_fraction: float = Field(ge=0.0)
    bankroll_after: float = Field(ge=0.0)
    valid_until: datetime
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("stake")
    @classmethod
    def _validate_no_full_kelly(cls, v: float, info) -> float:
        data = info.data
        if "kelly_fraction" in data and data["kelly_fraction"] >= 1.0 and v > 0:
            raise ValueError("Full Kelly (kelly_fraction >= 1.0) is not permitted in production")
        return v


class BetOrder(ContractBase):
    """Order submitted to the paper/execution adapter (deterministic output)."""

    id: str = Field(default_factory=lambda: generate_id("betorder"))
    intent_id: str
    event_id: str
    market_id: str
    outcome: str
    stake: float = Field(gt=0.0)
    price: float = Field(gt=0.0)
    side: str = Field(default="back")  # "back" or "lay"
    status: str = Field(default="submitted")
    broker_order_id: str | None = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BetFill(ContractBase):
    """Execution fill for a bet order."""

    id: str = Field(default_factory=lambda: generate_id("betfill"))
    order_id: str
    event_id: str
    outcome: str
    stake: float = Field(gt=0.0)
    price: float = Field(gt=0.0)
    commission: float = Field(default=0.0, ge=0.0)
    settled: bool = Field(default=False)
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
