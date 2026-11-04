"""Optional OpenAI-compatible gateway for Groq/Qwen endpoints; no execution access."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import httpx

from axel.agents.budget import LlmBudgetGuard
from axel.core.config import AxelSettings
from axel.observability.api_health import (
    ApiOutcome,
    OperationalCategory,
    record_api_error,
    record_http_call,
    record_operational_event,
    record_token_usage,
)


class LlmGatewayError(RuntimeError):
    """Base class for a failed LLM call."""


class LlmQuotaExhaustedError(LlmGatewayError):
    """The provider account is out of quota/credit or the local budget is spent."""


class LlmAuthError(LlmGatewayError):
    """The provider rejected the API key (revoked, wrong project, ...)."""


class LlmRateLimitedError(LlmGatewayError):
    """The provider is rate limiting requests; retry later."""


@dataclass(frozen=True)
class Completion:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class OpenAICompatibleGateway:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        budget: LlmBudgetGuard,
        client: httpx.Client | None = None,
        provider: str = "llm",
    ) -> None:
        if not api_key or not model:
            raise ValueError("API key and model are required")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._url, self._model, self._budget = f"{base_url.rstrip('/')}/chat/completions", model, budget
        self._client = client or httpx.Client(timeout=30)
        self._provider = provider

    def complete_json(self, system: str, user_data: str, estimated_cost_usd: float) -> Completion:
        try:
            self._budget.charge(estimated_cost_usd)
        except PermissionError as exc:
            record_operational_event(
                OperationalCategory.LLM,
                self._provider,
                "llm_budget_exceeded",
                outcome=ApiOutcome.QUOTA_EXHAUSTED.value,
                severity="critical",
                detail=str(exc),
                payload={
                    "model": self._model,
                    "estimated_cost_usd": estimated_cost_usd,
                },
            )
            raise LlmQuotaExhaustedError(str(exc)) from exc

        started = monotonic()
        try:
            response = self._client.post(
                self._url,
                headers=self._headers,
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_data},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                },
            )
        except httpx.TimeoutException as exc:
            record_api_error(
                self._provider,
                "chat_completion",
                ApiOutcome.TIMEOUT,
                detail=f"LLM request timed out after {type(exc).__name__}",
                payload={"model": self._model},
            )
            raise LlmGatewayError("LLM request timed out") from exc
        except httpx.HTTPError as exc:
            record_api_error(
                self._provider,
                "chat_completion",
                ApiOutcome.NETWORK_ERROR,
                detail=type(exc).__name__,
                payload={"model": self._model},
            )
            raise LlmGatewayError(f"LLM transport error: {type(exc).__name__}") from exc

        outcome = record_http_call(
            self._provider,
            "chat_completion",
            response,
            payload={"model": self._model},
        ).outcome

        if outcome is ApiOutcome.AUTH_FAILED:
            raise LlmAuthError(f"{self._provider} rejected the API key (HTTP {response.status_code})")
        if outcome is ApiOutcome.QUOTA_EXHAUSTED:
            raise LlmQuotaExhaustedError(
                f"{self._provider} quota/credit exhausted (HTTP {response.status_code})"
            )
        if outcome is ApiOutcome.RATE_LIMITED:
            raise LlmRateLimitedError(f"{self._provider} rate limited (HTTP {response.status_code})")
        if outcome.is_failure:
            raise LlmGatewayError(
                f"{self._provider} completion failed: {outcome.value} "
                f"(HTTP {response.status_code})"
            )

        payload = response.json()
        usage = payload.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        latency_ms = round((monotonic() - started) * 1000)
        record_token_usage(
            self._provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimated_cost_usd,
            model=self._model,
        )
        return Completion(
            content=payload["choices"][0]["message"]["content"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )


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
        provider=settings.llm_provider,
    )


__all__ = [
    "Completion",
    "LlmAuthError",
    "LlmGatewayError",
    "LlmQuotaExhaustedError",
    "LlmRateLimitedError",
    "OpenAICompatibleGateway",
    "gateway_from_settings",
]
