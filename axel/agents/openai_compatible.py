"""Optional OpenAI-compatible gateway for Groq/Qwen endpoints; no execution access."""

from dataclasses import dataclass
from time import monotonic

import httpx

from axel.agents.budget import LlmBudgetGuard
from axel.core.config import AxelSettings


@dataclass(frozen=True)
class Completion:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class OpenAICompatibleGateway:
    def __init__(self, api_key: str, base_url: str, model: str, budget: LlmBudgetGuard, client: httpx.Client | None = None) -> None:
        if not api_key or not model:
            raise ValueError("API key and model are required")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._url, self._model, self._budget = f"{base_url.rstrip('/')}/chat/completions", model, budget
        self._client = client or httpx.Client(timeout=30)

    def complete_json(self, system: str, user_data: str, estimated_cost_usd: float) -> Completion:
        self._budget.charge(estimated_cost_usd)
        started = monotonic()
        response = self._client.post(self._url, headers=self._headers, json={"model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_data}],
            "response_format": {"type": "json_object"}, "temperature": 0})
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage", {})
        return Completion(content=payload["choices"][0]["message"]["content"],
            prompt_tokens=int(usage.get("prompt_tokens", 0)), completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=round((monotonic() - started) * 1000))


def gateway_from_settings(
    settings: AxelSettings, client: httpx.Client | None = None
) -> OpenAICompatibleGateway:
    """Resolve only the selected provider's key; provider keys are never interchangeable."""
    if settings.llm_provider == "groq":
        key = settings.groq_api_key
        base_url = settings.llm_base_url or "https://api.groq.com/openai/v1"
    elif settings.llm_provider == "qwen":
        key = settings.qwen_api_key
        base_url = settings.llm_base_url
    else:
        raise ValueError("Set LLM_PROVIDER to groq or qwen before creating a gateway")
    if key is None:
        raise ValueError(f"{settings.llm_provider.upper()}_API_KEY is required for the selected provider")
    return OpenAICompatibleGateway(
        key.get_secret_value(), base_url, settings.llm_model,
        LlmBudgetGuard(settings.llm_hourly_budget_usd, settings.llm_daily_budget_usd), client,
    )
