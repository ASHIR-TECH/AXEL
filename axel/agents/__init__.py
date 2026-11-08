"""
AXEL Phase 3 — Stocks Agent Layer package exports.
"""

from axel.agents.analysts import (
    FundamentalAnalyst,
    MacroAnalyst,
    SentimentAnalyst,
    StocksAnalyst,
    TechnicalAnalyst,
    sanitize_document,
)
from axel.agents.audit import PromptAuditLog, PromptAuditRecord
from axel.agents.budget import LlmBudgetGuard
from axel.agents.kronos import (
    Forecast,
    KronosForecastAnalyst,
    Predictor,
    StubPredictor,
    load_analyst,
)
from axel.agents.manager import StocksSectionManager
from axel.agents.memory import (
    DecisionEntry,
    DecisionMemory,
    MemoryEntry,
    MemoryFormatError,
    ReflectionEntry,
    decision_id,
    reflection_id,
)
from axel.agents.openai_compatible import (
    Completion,
    OpenAICompatibleGateway,
    gateway_from_settings,
)
from axel.agents.overseer import AxelOverseer
from axel.agents.panel import (
    DebateArgument,
    DebateResult,
    StocksExpertPanel,
    build_stock_proposal,
)
from axel.agents.runtime import configured_gateway, validate_agent_configuration
from axel.agents.simulation import Phase3ExitReport, simulate_stocks_agent_week
from axel.agents.triggers import AgentEventDispatcher
from axel.agents.untrusted import PromptSanitizer, UntrustedDocument

__all__ = [
    "AgentEventDispatcher",
    "AxelOverseer",
    "Completion",
    "DebateArgument",
    "DebateResult",
    "DecisionEntry",
    "DecisionMemory",
    "Forecast",
    "FundamentalAnalyst",
    "KronosForecastAnalyst",
    "LlmBudgetGuard",
    "MacroAnalyst",
    "MemoryEntry",
    "MemoryFormatError",
    "OpenAICompatibleGateway",
    "Phase3ExitReport",
    "Predictor",
    "PromptAuditLog",
    "PromptAuditRecord",
    "PromptSanitizer",
    "ReflectionEntry",
    "SentimentAnalyst",
    "StocksAnalyst",
    "StocksExpertPanel",
    "StocksSectionManager",
    "StubPredictor",
    "TechnicalAnalyst",
    "UntrustedDocument",
    "build_stock_proposal",
    "configured_gateway",
    "decision_id",
    "gateway_from_settings",
    "load_analyst",
    "reflection_id",
    "sanitize_document",
    "simulate_stocks_agent_week",
    "validate_agent_configuration",
]
