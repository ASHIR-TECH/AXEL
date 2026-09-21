"""
Execution bot adapters, order state machines, approval gateway, and reconciliation.
Zero LLM imports permitted.
"""

from axel.execution.alpaca import AlpacaAdapter
from axel.execution.approval import ApprovalService, ApprovalTicket
from axel.execution.broker_base import BrokerAdapter, OrderResult, PositionInfo
from axel.execution.order_fsm import (
    InvalidOrderTransitionError,
    OrderStateMachine,
)
from axel.execution.reconcile import (
    PositionDrift,
    ReconciliationEngine,
    ReconciliationReport,
)

__all__ = [
    "BrokerAdapter",
    "OrderResult",
    "PositionInfo",
    "AlpacaAdapter",
    "OrderStateMachine",
    "InvalidOrderTransitionError",
    "ApprovalService",
    "ApprovalTicket",
    "ReconciliationEngine",
    "PositionDrift",
    "ReconciliationReport",
]
