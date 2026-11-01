"""Tests for LLM quota/auth classification and structured failure events."""

from __future__ import annotations

import json

import httpx
import pytest

from axel.agents.budget import LlmBudgetGuard
from axel.agents.openai_compatible import (
    LlmAuthError,
    LlmGatewayError,
    LlmQuotaExhaustedError,
    LlmRateLimitedError,
    OpenAICompatibleGateway,
)
from axel.observability.api_health import configure_event_sink


def _gateway(handler, *, hourly: float = 10.0, daily: float = 10.0) -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(
        api_key="test-key",
        base_url="https://llm.example/v1",
        model="test-model",
        budget=LlmBudgetGuard(hourly, daily),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        provider="groq",
    )


def _capture_events() -> list:
    captured: list = []
    configure_event_sink(lambda event, call: captured.append(event))
    return captured


def test_successful_completion_returns_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
        )

    completion = _gateway(handler).complete_json("system", "user", 0.002)
    assert completion.content == '{"ok": true}'
    assert completion.prompt_tokens == 120
    assert completion.completion_tokens == 30
    assert completion.latency_ms >= 0


def test_auth_failure_is_typed_and_logged():
    events = _capture_events()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(LlmAuthError):
        _gateway(handler).complete_json("system", "user", 0.001)

    assert events
    assert events[0].outcome == "auth_failed"
    assert events[0].severity == "critical"


def test_quota_failure_is_typed_and_logged():
    events = _capture_events()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "insufficient credit"}})

    with pytest.raises(LlmQuotaExhaustedError):
        _gateway(handler).complete_json("system", "user", 0.001)

    assert events
    assert events[0].outcome == "quota_exhausted"
    assert events[0].category.value == "api"


def test_rate_limit_is_typed_and_logged():
    events = _capture_events()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "20"}, json={"error": "slow down"})

    with pytest.raises(LlmRateLimitedError):
        _gateway(handler).complete_json("system", "user", 0.001)

    assert events
    assert events[0].outcome == "rate_limited"


def test_server_error_is_typed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    with pytest.raises(LlmGatewayError):
        _gateway(handler).complete_json("system", "user", 0.001)


def test_timeout_is_typed_and_logged():
    events = _capture_events()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(LlmGatewayError):
        _gateway(handler).complete_json("system", "user", 0.001)

    assert events
    assert events[0].outcome == "timeout"


def test_local_budget_exhaustion_emits_quota_event():
    events = _capture_events()

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("budget must be checked before the network call")

    gateway = _gateway(handler, hourly=0.0, daily=0.0)
    with pytest.raises(LlmQuotaExhaustedError):
        gateway.complete_json("system", "user", 0.01)

    assert len(events) == 1
    assert events[0].category.value == "llm"
    assert events[0].event == "llm_budget_exceeded"
    assert events[0].outcome == "quota_exhausted"


def test_request_body_uses_json_object_mode():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    _gateway(handler).complete_json("system", "user", 0.0)
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["temperature"] == 0
