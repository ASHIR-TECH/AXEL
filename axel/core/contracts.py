"""
Versioned Pydantic contracts crossing the LLM / Deterministic boundary.
These models represent the ONLY data structures allowed to cross the trust threshold.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from axel.core.ids import generate_id
from axel.core.types import (
    Direction,
    OrderSide,
    OrderState,
    OrderType,
    RiskVerdict,
    Section,
    TimeInForce,
    TradingMode,
)


class ContractBase(BaseModel):
    """Base model with strict validation and frozen defaults."""
    model_config = ConfigDict(extra="forbid", strict=True)


class Signal(ContractBase):
    """
    Emitted by Analyst agents (Fundamental, Technical, Sentiment, Macro).
    Represents raw analysis, NEVER an execution instruction.
    """
    id: str = Field(default_factory=lambda: generate_id("sig"))
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    section: Section
    symbol: str
    analyst: str
    direction: Direction
    strength: float = Field(ge=0.0, le=1.0)
    horizon: str = "1d"
    features_ref: str | None = None


class Proposal(ContractBase):
    """
    Emitted by Expert Panel / Section Manager.
    Proposes a concrete trade for deterministic evaluation by the Risk Engine.
    """
    id: str = Field(default_factory=lambda: generate_id("prop"))
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    section: Section
    symbol: str
    side: OrderSide
    entry: float = Field(gt=0.0)
    stop: float = Field(gt=0.0)
    target: float = Field(gt=0.0)
    strategy_id: str
    panel_confidence: float = Field(ge=0.0, le=1.0)
    win_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    avg_win_loss_ratio: float = Field(default=1.5, gt=0.0)
    rationale_ref: str | None = None
    mode: TradingMode = TradingMode.PAPER
    ttl_seconds: int = Field(default=300, gt=0)

    @field_validator("stop")
    @classmethod
    def validate_stop_logic(cls, stop: float, info) -> float:
        data = info.data
        if "side" in data and "entry" in data:
            side = data["side"]
            entry = data["entry"]
            if side == OrderSide.BUY and stop >= entry:
                raise ValueError(f"For BUY, stop ({stop}) must be strictly below entry ({entry})")
            if side == OrderSide.SELL and stop <= entry:
                raise ValueError(f"For SELL, stop ({stop}) must be strictly above entry ({entry})")
        return stop


class RiskDecision(ContractBase):
    """
    Produced exclusively by the deterministic Risk Engine.
    Records the final veto/approval, sizing clamp, and binding limits.
    """
    id: str = Field(default_factory=lambda: generate_id("risk"))
    proposal_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved: bool
    verdict: RiskVerdict
    approved_qty: float = Field(default=0.0, ge=0.0)
    approved_notional_usd: float = Field(default=0.0, ge=0.0)
    binding_limit: str | None = None
    reasons: list[str] = Field(default_factory=list)
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)


class Order(ContractBase):
    """
    Executable order model managed by the Execution Bot and Order FSM.
    """
    client_order_id: str
    proposal_id: str
    symbol: str
    side: OrderSide
    qty: float = Field(gt=0.0)
    type: OrderType = OrderType.LIMIT
    limit_price: float | None = Field(default=None, gt=0.0)
    stop_price: float | None = Field(default=None, gt=0.0)
    time_in_force: TimeInForce = TimeInForce.DAY
    state: OrderState = OrderState.PENDING_SUBMIT
    broker_order_id: str | None = None
    filled_qty: float = Field(default=0.0, ge=0.0)
    filled_avg_price: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AllocationProposal(ContractBase):
    """
    Emitted by Axel Prime Overseer to shift capital between sections.
    Subject to deterministic clamp (max 5% per cycle).
    """
    id: str = Field(default_factory=lambda: generate_id("alloc"))
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    from_section: Section
    to_section: Section
    pct_of_total: float = Field(gt=0.0, le=1.0)
    rationale: str | None = None
