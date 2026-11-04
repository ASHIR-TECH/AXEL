"""
Core domain types and enumerations for AXEL.
"""

from enum import Enum


class Section(str, Enum):
    STOCKS = "stocks"
    CRYPTO = "crypto"
    OPTIONS = "options"
    FOREX = "forex"
    PREDICTIONS = "predictions"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderState(str, Enum):
    # Canonical Phase 6 lifecycle (see docs/AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md §7).
    CREATED = "created"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    UNKNOWN = "unknown"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    CANCEL_PENDING = "cancel_pending"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    # ERROR is non-recoverable without a controlled reconciliation path; it is never
    # treated as terminal so that broker facts can still repair it.
    ERROR = "error"
    # Legacy paper-era aliases retained so Phase 1-5 code paths keep working.
    PENDING_SUBMIT = "pending_submit"
    ACCEPTED = "accepted"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FILLED = "filled"
    FAILED = "failed"


class RiskVerdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    HALTED = "halted"


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class StrategyStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PAPER_APPROVED = "paper_approved"
    LIVE_APPROVED = "live_approved"
    DEPRECATED = "deprecated"
