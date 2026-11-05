"""Tests for deterministic provider retry/backoff planning."""

import pytest

from axel.data.providers.retry import (
    DEFAULT_RETRYABLE_STATUS_CODES,
    RetryPolicy,
    backoff_schedule,
    delay_for_attempt,
    is_retryable_exception,
    is_retryable_status,
    parse_retry_after,
    plan_retry,
    retryable_statuses,
)


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(
        max_attempts=6, base_delay_s=0.5, multiplier=2.0, max_delay_s=3.0, jitter_ratio=0.0
    )

    assert backoff_schedule(policy) == (0.5, 1.0, 2.0, 3.0, 3.0)
    assert delay_for_attempt(0, policy) == 0.5
    assert delay_for_attempt(10, policy) == 3.0


def test_jitter_band_is_symmetric_and_bounded() -> None:
    policy = RetryPolicy(base_delay_s=1.0, jitter_ratio=0.25, multiplier=1.0)

    assert delay_for_attempt(0, policy, jitter=-1.0) == pytest.approx(0.75)
    assert delay_for_attempt(0, policy, jitter=0.0) == pytest.approx(1.0)
    assert delay_for_attempt(0, policy, jitter=1.0) == pytest.approx(1.25)
    with pytest.raises(ValueError):
        delay_for_attempt(0, policy, jitter=2.0)
    with pytest.raises(ValueError):
        delay_for_attempt(-1, policy)


def test_zero_base_delay_disables_waiting() -> None:
    policy = RetryPolicy(base_delay_s=0.0, max_delay_s=0.0)

    assert backoff_schedule(policy, attempts=3) == (0.0, 0.0, 0.0)


def test_retryable_statuses() -> None:
    policy = RetryPolicy()

    assert is_retryable_status(429, policy)
    assert is_retryable_status(503, policy)
    assert not is_retryable_status(400, policy)
    assert 500 in DEFAULT_RETRYABLE_STATUS_CODES
    assert retryable_statuses(policy) == tuple(sorted(policy.retryable_status_codes))


def test_retryable_exceptions() -> None:
    policy = RetryPolicy()

    assert is_retryable_exception(TimeoutError("slow"), policy)
    assert is_retryable_exception(ConnectionError("reset"), policy)
    assert not is_retryable_exception(ValueError("bad input"), policy)


def test_plan_retry_for_retryable_status() -> None:
    policy = RetryPolicy(max_attempts=3, base_delay_s=1.0, jitter_ratio=0.0)

    decision = plan_retry(attempt=0, policy=policy, status_code=503)

    assert decision.should_retry
    assert decision.attempt == 0
    assert decision.delay_s == 1.0
    assert decision.reason == "retrying with exponential backoff"


def test_plan_retry_stops_at_max_attempts() -> None:
    policy = RetryPolicy(max_attempts=3, base_delay_s=1.0, jitter_ratio=0.0)

    decision = plan_retry(attempt=2, policy=policy, status_code=503)

    assert not decision.should_retry
    assert decision.delay_s == 0.0
    assert decision.reason == "max attempts reached"


def test_plan_retry_rejects_non_retryable_errors() -> None:
    policy = RetryPolicy()

    status_decision = plan_retry(attempt=0, policy=policy, status_code=400)
    exception_decision = plan_retry(attempt=0, policy=policy, exception=ValueError("bad"))

    assert not status_decision.should_retry
    assert not exception_decision.should_retry
    assert status_decision.reason == "error is not retryable"


def test_plan_retry_uses_retry_after_header_and_caps_it() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=10.0, jitter_ratio=0.0)

    header = plan_retry(attempt=0, policy=policy, status_code=429, retry_after="4")
    capped = plan_retry(attempt=0, policy=policy, status_code=429, retry_after="99")

    assert header.delay_s == pytest.approx(4.0)
    assert header.reason == "retrying after Retry-After header"
    assert capped.delay_s == pytest.approx(10.0)


def test_plan_retry_requires_a_failure_to_classify() -> None:
    with pytest.raises(ValueError):
        plan_retry(attempt=0)
    with pytest.raises(ValueError):
        plan_retry(attempt=-1, status_code=500)


def test_parse_retry_after() -> None:
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after(" 2.5 ") == 2.5
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    assert parse_retry_after("-1") is None
    assert parse_retry_after(None) is None


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_s=-1.0)
    with pytest.raises(ValueError):
        RetryPolicy(multiplier=0.5)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_s=5.0, max_delay_s=1.0)
    with pytest.raises(ValueError):
        RetryPolicy(jitter_ratio=1.5)
    with pytest.raises(ValueError):
        RetryPolicy(retryable_status_codes=frozenset({42}))
    with pytest.raises(ValueError):
        backoff_schedule(RetryPolicy(), attempts=-1)
