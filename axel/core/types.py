"""
Core domain types and enumerations for AXEL.
"""

from enum import Enum


class Section(str, Enum):
    STOCKS = "stocks"
    CRYPTO = "crypto"
    OPTIONS = "options"
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
    PENDING_SUBMIT = "pending_submit"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"


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
