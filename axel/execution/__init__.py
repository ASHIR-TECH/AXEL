"""
Execution bot adapters, order state machines, approval gateway, and reconciliation.
Zero LLM imports permitted.
"""

from axel.execution.alpaca import AlpacaAdapter
from axel.execution.approval import ApprovalService, ApprovalTicket
from axel.execution.broker_base import BrokerAdapter, OrderResult, PositionInfo
from axel.execution.intelligence import (
    ExecutionSchedule,
    ExecutionStep,
    ImplementationShortfallBreakdown,
    ScheduleComparisonReport,
    ScheduleType,
    calculate_implementation_shortfall,
    compare_execution_policies,
    estimate_square_root_impact,
    generate_almgren_chriss_schedule,
    generate_twap_schedule,
    generate_vwap_schedule,
)
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
    "AlpacaAdapter",
    "ApprovalService",
    "ApprovalTicket",
    "BrokerAdapter",
    "ExecutionSchedule",
    "ExecutionStep",
    "ImplementationShortfallBreakdown",
    "InvalidOrderTransitionError",
    "OrderResult",
    "OrderStateMachine",
    "PositionDrift",
    "PositionInfo",
    "ReconciliationEngine",
    "ReconciliationReport",
    "ScheduleComparisonReport",
    "ScheduleType",
    "calculate_implementation_shortfall",
    "compare_execution_policies",
    "estimate_square_root_impact",
    "generate_almgren_chriss_schedule",
    "generate_twap_schedule",
    "generate_vwap_schedule",
]
