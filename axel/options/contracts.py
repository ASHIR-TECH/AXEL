"""
Versioned Pydantic contracts crossing the LLM / deterministic boundary for
the Options section (Phase 8).

These are the ONLY data structures allowed to cross the trust threshold.
Frozen and strict: `extra="forbid"`. Agents emit `OptionsProposal`; the
deterministic validator emits `OptionsRiskDecision`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from axel.core.ids import generate_id
from axel.core.types import Section


class ContractBase(BaseModel):
    """Base model with strict validation and frozen defaults."""
    model_config = ConfigDict(extra="forbid", strict=True)


class OptionLegContract(ContractBase):
    """A single leg of a multi-leg options structure (untrusted input)."""

    contract_id: str
    venue: str
    underlying_id: str
    underlying_symbol: str
    right: str  # "call" | "put"
    strike: float = Field(gt=0.0)
    expiry: datetime
    multiplier: float = Field(default=100.0, gt=0.0)
    side: str  # "long" | "short"
    ratio: int = Field(default=1, gt=0)
    entry_price: float | None = Field(default=None, gt=0.0)
    exercise_style: str = "american"
    settlement_type: str = "cash"
    currency: str = "USD"

    @field_validator("right")
    @classmethod
    def _validate_right(cls, v: str) -> str:
        if v.lower() not in ("call", "put"):
            raise ValueError(f"right must be 'call' or 'put', got {v!r}")
        return v.lower()

    @field_validator("side")
    @classmethod
    def _validate_side(cls, v: str) -> str:
        if v.lower() not in ("long", "short"):
            raise ValueError(f"side must be 'long' or 'short', got {v!r}")
        return v.lower()

    @field_validator("exercise_style")
    @classmethod
    def _validate_exercise_style(cls, v: str) -> str:
        if v.lower() not in ("american", "european"):
            raise ValueError(f"exercise_style must be 'american' or 'european', got {v!r}")
        return v.lower()

    @field_validator("settlement_type")
    @classmethod
    def _validate_settlement_type(cls, v: str) -> str:
        if v.lower() not in ("physical", "cash"):
            raise ValueError(f"settlement_type must be 'physical' or 'cash', got {v!r}")
        return v.lower()


class OptionsProposal(ContractBase):
    """
    Emitted by an Options Analyst / Expert Panel (untrusted).
    Proposes a concrete multi-leg options trade for deterministic evaluation.
    """

    id: str = Field(default_factory=lambda: generate_id("optprop"))
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    section: Section = Field(default=Section.OPTIONS)
    strategy_id: str
    underlying_id: str
    underlying_symbol: str
    legs: list[OptionLegContract] = Field(min_length=1, max_length=8)
    valid_until: datetime
    forecast_version: str
    data_snapshot_id: str
    limit_policy: str = Field(default="limit_only")
    max_loss: float | None = Field(default=None, ge=0.0)
    max_gain: float | None = Field(default=None, ge=0.0)
    break_even_points: list[float] = Field(default_factory=list)
    greeks: dict[str, Any] | None = None
    liquidity_snapshot: dict[str, Any] | None = None
    stress_summary: dict[str, Any] | None = None
    thesis: str = Field(default="")
    uncertainty: str = Field(default="")
    agent_trace_id: str | None = None
    mode: str = Field(default="paper")
    ttl_seconds: int = Field(default=300, gt=0)

    @field_validator("valid_until")
    @classmethod
    def _validate_valid_until(cls, v: datetime, info) -> datetime:
        data = info.data
        if "ts" in data and v <= data["ts"]:
            raise ValueError("valid_until must be strictly after ts")
        return v

    @field_validator("limit_policy")
    @classmethod
    def _validate_limit_policy(cls, v: str) -> str:
        if v not in ("limit_only", "market_allowed"):
            raise ValueError("limit_policy must be 'limit_only' or 'market_allowed'")
        return v

    @field_validator("legs")
    @classmethod
    def _validate_unique_contracts(cls, v: list[OptionLegContract]) -> list[OptionLegContract]:
        ids = [leg.contract_id for leg in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate contract_id in legs")
        return v


class OptionsRiskDecision(ContractBase):
    """
    Produced exclusively by the deterministic Options Validator.
    ACCEPT / REJECT / REQUIRES_REVIEW with stable reason codes and bound
    risk figures. Unlocks execution only when ACCEPT.
    """

    id: str = Field(default_factory=lambda: generate_id("optrisk"))
    proposal_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    verdict: str  # "ACCEPT" | "REJECT" | "REQUIRES_REVIEW"
    reasons: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    max_loss: float | None = Field(default=None, ge=0.0)
    max_gain: float | None = Field(default=None, ge=0.0)
    break_even_points: list[float] = Field(default_factory=list)
    margin_buffer_usd: float = Field(default=0.0, ge=0.0)
    config_hash: str
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    greeks: dict[str, Any] | None = None

    @field_validator("verdict")
    @classmethod
    def _validate_verdict(cls, v: str) -> str:
        if v not in ("ACCEPT", "REJECT", "REQUIRES_REVIEW"):
            raise ValueError("verdict must be ACCEPT, REJECT, or REQUIRES_REVIEW")
        return v
