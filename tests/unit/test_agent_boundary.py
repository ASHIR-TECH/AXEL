from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from axel.agents.analysts import StocksAnalyst
from axel.agents.budget import LlmBudgetGuard
from axel.agents.openai_compatible import gateway_from_settings
from axel.agents.panel import build_stock_proposal
from axel.agents.runtime import validate_agent_configuration
from axel.core.config import AxelSettings


def test_budget_guard_fails_closed() -> None:
    guard = LlmBudgetGuard(hourly_cap_usd=1, daily_cap_usd=2)
    guard.charge(1)
    with pytest.raises(PermissionError):
        guard.charge(0.01)


def test_panel_rejects_unvalidated_strategy() -> None:
    signal = StocksAnalyst().signal("AAPL", 101, 100, datetime.now(UTC))
    with pytest.raises(PermissionError):
        build_stock_proposal([signal], strategy_id="trend-v1", entry=101, stop=99, target=105)


def test_selected_provider_requires_its_own_key() -> None:
    settings = AxelSettings(llm_provider="qwen", qwen_api_key=None, llm_model="qwen-test")
    with pytest.raises(ValueError, match="QWEN_API_KEY"):
        gateway_from_settings(settings)


def test_qwen_configuration_is_linked_to_agent_runtime() -> None:
    settings = AxelSettings(
        llm_provider="qwen", qwen_api_key=SecretStr("test-key"),
        llm_base_url="https://example.test/compatible-mode/v1", llm_model="qwen-test",
    )
    assert "provider=qwen" in validate_agent_configuration(settings)
