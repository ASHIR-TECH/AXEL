"""
Deterministic Risk Engine package for AXEL.
Zero LLM imports permitted. All safety limits are immutable.
"""

from axel.risk.allocator import AllocationDecision, CapitalAllocator
from axel.risk.engine import (
    AccountState,
    OpenPositionInfo,
    SectionRiskAgent,
    SectionRiskState,
)
from axel.risk.killswitch import KillSwitch
from axel.risk.limits import LIMITS, RiskLimits
from axel.risk.loss_manager import DailyPnLState, LossEvaluationResult, LossManager
from axel.risk.sizer import KellySizer, SizingResult
from axel.risk.tail import (
    SCENARIOS,
    Position,
    ProposedTrade,
    RiskBudget,
    SizingDecision,
    StressScenario,
    TailRiskEngine,
    TailRiskReport,
    expected_shortfall,
    historical_var,
    marginal_var,
    stress_loss,
)
from axel.risk.turbulence import (
    covariance_matrix,
    invert_matrix,
    is_turbulent,
    mean_vector,
    rolling_turbulence,
    turbulence_index,
)

__all__ = [
    "LIMITS",
    "SCENARIOS",
    "AccountState",
    "AllocationDecision",
    "CapitalAllocator",
    "DailyPnLState",
    "KellySizer",
    "KillSwitch",
    "LossEvaluationResult",
    "LossManager",
    "OpenPositionInfo",
    "Position",
    "ProposedTrade",
    "RiskBudget",
    "RiskLimits",
    "SectionRiskAgent",
    "SectionRiskState",
    "SizingDecision",
    "SizingResult",
    "StressScenario",
    "TailRiskEngine",
    "TailRiskReport",
    "covariance_matrix",
    "expected_shortfall",
    "historical_var",
    "invert_matrix",
    "is_turbulent",
    "marginal_var",
    "mean_vector",
    "rolling_turbulence",
    "stress_loss",
    "turbulence_index",
]
