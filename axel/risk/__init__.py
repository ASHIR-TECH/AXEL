"""
Deterministic Risk Engine package for AXEL.
Zero LLM imports permitted. All safety limits are immutable.
"""

from axel.risk.advanced import (
    DEFAULT_ADVANCED_SCENARIOS,
    AdvancedRiskAssessment,
    AdvancedRiskEngine,
    AssetPosition,
    CorrelationBreakdownResult,
    GARCHParameters,
    RiskAlertSeverity,
    ScenarioEvaluationResult,
    StressScenarioSpec,
    StressScenarioType,
    detect_correlation_breakdown,
    evaluate_scenario_stress,
    ewma_variance,
    ewma_volatility,
    garch_forecast,
    garch_variance,
    rolling_correlation,
    stress_correlation_matrix,
)
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

__all__ = [
    "DEFAULT_ADVANCED_SCENARIOS",
    "LIMITS",
    "SCENARIOS",
    "AccountState",
    "AdvancedRiskAssessment",
    "AdvancedRiskEngine",
    "AllocationDecision",
    "AssetPosition",
    "CapitalAllocator",
    "CorrelationBreakdownResult",
    "DailyPnLState",
    "GARCHParameters",
    "KellySizer",
    "KillSwitch",
    "LossEvaluationResult",
    "LossManager",
    "OpenPositionInfo",
    "Position",
    "ProposedTrade",
    "RiskAlertSeverity",
    "RiskBudget",
    "RiskLimits",
    "ScenarioEvaluationResult",
    "SectionRiskAgent",
    "SectionRiskState",
    "SizingDecision",
    "SizingResult",
    "StressScenario",
    "StressScenarioSpec",
    "StressScenarioType",
    "TailRiskEngine",
    "TailRiskReport",
    "detect_correlation_breakdown",
    "evaluate_scenario_stress",
    "ewma_variance",
    "ewma_volatility",
    "expected_shortfall",
    "garch_forecast",
    "garch_variance",
    "historical_var",
    "marginal_var",
    "rolling_correlation",
    "stress_correlation_matrix",
    "stress_loss",
]
