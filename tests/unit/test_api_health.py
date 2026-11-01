"""Tests for API/transaction health classification and persistence."""

from __future__ import annotations

import httpx
import pytest

from axel.db.models import ApiProviderStateModel, OperationalEventModel
from axel.observability.api_health import (
    ApiOutcome,
    OperationalCategory,
    classify_http_status,
    configure_event_sink,
    extract_rate_limit_remaining,
    extract_retry_after,
    load_provider_states,
    record_api_call,
    record_api_error,
    record_http_call,
    record_operational_event,
    record_token_usage,
    severity_for,
)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, "", ApiOutcome.SUCCESS),
        (204, "", ApiOutcome.SUCCESS),
        (400, "bad request", ApiOutcome.REJECTED),
        (401, "invalid api key", ApiOutcome.AUTH_FAILED),
        (402, "payment required", ApiOutcome.QUOTA_EXHAUSTED),
        (403, "forbidden", ApiOutcome.AUTH_FAILED),
        (403, "quota exceeded", ApiOutcome.QUOTA_EXHAUSTED),
        (404, "not found", ApiOutcome.NOT_FOUND),
        (408, "timeout", ApiOutcome.TIMEOUT),
        (429, "slow down", ApiOutcome.RATE_LIMITED),
        (429, "insufficient credit", ApiOutcome.QUOTA_EXHAUSTED),
        (500, "boom", ApiOutcome.SERVER_ERROR),
        (503, "unavailable", ApiOutcome.SERVER_ERROR),
    ],
)
def test_classify_http_status(status, body, expected):
    assert classify_http_status(status, body) is expected


def test_severity_is_critical_for_quota_and_auth():
    assert severity_for(ApiOutcome.QUOTA_EXHAUSTED) == "critical"
    assert severity_for(ApiOutcome.AUTH_FAILED) == "critical"
    assert severity_for(ApiOutcome.RATE_LIMITED) == "warning"
    assert severity_for(ApiOutcome.SUCCESS) == "info"


def test_extract_retry_and_rate_limit_headers():
    response = httpx.Response(
        429,
        headers={"Retry-After": "30", "X-RateLimit-Remaining": "0"},
    )
    assert extract_retry_after(response.headers) == 30.0
    assert extract_rate_limit_remaining(response.headers) == 0


def test_quota_failure_persists_provider_state_and_event(in_memory_db):
    call = record_api_call(
        "groq",
        "chat_completion",
        outcome=ApiOutcome.QUOTA_EXHAUSTED,
        status_code=429,
        detail="insufficient credit",
        retry_after_seconds=60,
        session=in_memory_db,
    )

    assert call.is_failure
    state = in_memory_db.get(ApiProviderStateModel, "groq")
    assert state is not None
    assert state.quota_exhausted is True
    assert state.consecutive_failures == 1
    assert state.last_status_code == 429
    assert state.retry_after_seconds == 60
    assert state.quota_exhausted_at is not None

    events = in_memory_db.query(OperationalEventModel).all()
    assert len(events) == 1
    assert events[0].category == "api"
    assert events[0].component == "groq"
    assert events[0].outcome == "quota_exhausted"
    assert events[0].severity == "critical"


def test_success_clears_quota_and_failure_streak(in_memory_db):
    record_api_call(
        "alpaca",
        "get_account",
        outcome=ApiOutcome.AUTH_FAILED,
        status_code=401,
        session=in_memory_db,
    )
    record_api_call(
        "alpaca",
        "get_account",
        outcome=ApiOutcome.SUCCESS,
        status_code=200,
        session=in_memory_db,
    )

    state = in_memory_db.get(ApiProviderStateModel, "alpaca")
    assert state is not None
    assert state.consecutive_failures == 0
    assert state.quota_exhausted is False
    assert state.last_success_at is not None


def test_record_http_call_reads_body_and_headers(in_memory_db):
    response = httpx.Response(
        429,
        headers={"Retry-After": "15", "X-RateLimit-Remaining": "3"},
        text='{"error":"rate limit exceeded"}',
    )
    call = record_http_call("fred", "fetch_observations", response, session=in_memory_db)
    assert call.outcome is ApiOutcome.RATE_LIMITED
    assert call.status_code == 429
    assert call.retry_after_seconds == 15.0
    assert call.rate_limit_remaining == 3
    assert "rate limit" in call.detail.lower()


def test_transport_error_has_no_status(in_memory_db):
    call = record_api_error(
        "telegram",
        "send_message",
        ApiOutcome.NETWORK_ERROR,
        detail="connection reset",
        session=in_memory_db,
    )
    assert call.status_code is None
    state = in_memory_db.get(ApiProviderStateModel, "telegram")
    assert state is not None
    assert state.last_outcome == "network_error"


def test_order_event_persists_with_payload(in_memory_db):
    record_operational_event(
        OperationalCategory.ORDER,
        "order_service",
        "order_rejected",
        outcome="rejected",
        severity="critical",
        detail="broker rejected coid-1",
        payload={"client_order_id": "coid-1", "symbol": "AAPL"},
        session=in_memory_db,
    )
    event = in_memory_db.query(OperationalEventModel).one()
    assert event.category == "order"
    assert event.event == "order_rejected"
    assert event.severity == "critical"
    assert event.payload["client_order_id"] == "coid-1"


def test_token_usage_accumulates(in_memory_db):
    record_token_usage(
        "groq",
        prompt_tokens=100,
        completion_tokens=25,
        cost_usd=0.001,
        model="llama",
        session=in_memory_db,
    )
    record_token_usage(
        "groq",
        prompt_tokens=50,
        completion_tokens=10,
        cost_usd=0.0005,
        model="llama",
        session=in_memory_db,
    )
    state = in_memory_db.get(ApiProviderStateModel, "groq")
    assert state is not None
    assert state.prompt_tokens_total == 150
    assert state.completion_tokens_total == 35
    assert state.estimated_cost_usd_total == pytest.approx(0.0015)


def test_load_provider_states_is_ordered(in_memory_db):
    record_api_call("z-provider", "x", outcome=ApiOutcome.SUCCESS, status_code=200, session=in_memory_db)
    record_api_call("a-provider", "x", outcome=ApiOutcome.SUCCESS, status_code=200, session=in_memory_db)
    assert [row.provider for row in load_provider_states(in_memory_db)] == [
        "a-provider",
        "z-provider",
    ]


def test_custom_sink_receives_failures():
    captured = []

    def sink(event, call):
        captured.append((event, call))

    configure_event_sink(sink)

    record_api_call(
        "pagerduty",
        "events_v2",
        outcome=ApiOutcome.QUOTA_EXHAUSTED,
        status_code=402,
        detail="quota",
    )

    assert len(captured) == 1
    event, call = captured[0]
    assert event.category == OperationalCategory.API
    assert event.outcome == "quota_exhausted"
    assert call is not None and call.provider == "pagerduty"
