"""
Core foundational utilities, contracts, types, and configurations.
"""

from axel.core.clock import Clock, RealClock, SimulatedClock
from axel.core.config import AxelSettings, settings
from axel.core.contracts import (
    AllocationProposal,
    Order,
    Proposal,
    RiskDecision,
    Signal,
)
from axel.core.ids import generate_client_order_id, generate_id
from axel.core.logging import logger, setup_logging
from axel.core.types import (
    Direction,
    OrderSide,
    OrderState,
    OrderType,
    ProposalStatus,
    RiskVerdict,
    Section,
    StrategyStatus,
    TimeInForce,
    TradingMode,
)

__all__ = [
    "Clock",
    "RealClock",
    "SimulatedClock",
    "AxelSettings",
    "settings",
    "Signal",
    "Proposal",
    "RiskDecision",
    "Order",
    "AllocationProposal",
    "generate_id",
    "generate_client_order_id",
    "logger",
    "setup_logging",
    "Section",
    "Direction",
    "OrderSide",
    "OrderType",
    "TimeInForce",
    "OrderState",
    "ProposalStatus",
    "RiskVerdict",
    "TradingMode",
    "StrategyStatus",
]
