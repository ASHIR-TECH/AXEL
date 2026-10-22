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
from axel.agents.manager import StocksSectionManager
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
    "FundamentalAnalyst",
    "LlmBudgetGuard",
    "MacroAnalyst",
    "OpenAICompatibleGateway",
    "Phase3ExitReport",
    "PromptAuditLog",
    "PromptAuditRecord",
    "PromptSanitizer",
    "SentimentAnalyst",
    "StocksAnalyst",
    "StocksExpertPanel",
    "StocksSectionManager",
    "TechnicalAnalyst",
    "UntrustedDocument",
    "build_stock_proposal",
    "configured_gateway",
    "gateway_from_settings",
    "sanitize_document",
    "simulate_stocks_agent_week",
    "validate_agent_configuration",
]
