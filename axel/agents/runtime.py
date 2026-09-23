"""Agent-service composition root; this is the only place provider selection is resolved."""

from axel.agents.openai_compatible import OpenAICompatibleGateway, gateway_from_settings
from axel.core.config import AxelSettings, settings


def configured_gateway(config: AxelSettings = settings) -> OpenAICompatibleGateway:
    """Build the proposal-only LLM gateway from `.env` provider settings.

    Calling this creates an HTTP client but performs no model request. The agent service,
    not the risk/execution core, is the sole consumer of this function.
    """
    return gateway_from_settings(config)


def validate_agent_configuration(config: AxelSettings = settings) -> str:
    """Validate configuration without sending a request or returning any secret."""
    configured_gateway(config)
    return f"Agent gateway configured for provider={config.llm_provider}, model={config.llm_model}"
