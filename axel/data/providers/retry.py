"""
Deterministic retry and backoff planning for read-only data providers.

Adapted from the retry helper in AI-Trader
(https://github.com/TauricResearch/AI-Trader, service/server/price_fetcher.py),
split into a pure policy object so tests and backtests can reason about retry
timing without sleeping or touching the network.

This module plans retries only. It performs no I/O and never executes a request.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

DEFAULT_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with a cap and an optional symmetric jitter band."""

    max_attempts: int = 4
    base_delay_s: float = 0.5
    multiplier: float = 2.0
    max_delay_s: float = 30.0
    jitter_ratio: float = 0.25
    retryable_status_codes: frozenset[int] = field(
        default_factory=lambda: DEFAULT_RETRYABLE_STATUS_CODES
    )
    retryable_exceptions: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_s < 0.0:
            raise ValueError("base_delay_s must be non-negative")
        if self.multiplier < 1.0:
            raise ValueError("multiplier must be at least 1.0")
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("max_delay_s must be >= base_delay_s")
        if not 0.0 <= self.jitter_ratio <= 1.0:
            raise ValueError("jitter_ratio must be in [0,1]")
        if any(code < 100 or code > 599 for code in self.retryable_status_codes):
            raise ValueError("retryable_status_codes must contain HTTP status codes")


DEFAULT_RETRY_POLICY = RetryPolicy()


@dataclass(frozen=True)
class RetryDecision:
    """What to do after one failed attempt."""

    should_retry: bool
    attempt: int
    delay_s: float
    reason: str


def delay_for_attempt(
    attempt: int,
    policy: RetryPolicy,
    *,
    jitter: float = 0.0,
) -> float:
    """Backoff delay before the next attempt (``attempt`` is zero-based).

    ``jitter`` is a deterministic caller-supplied value in [-1, 1]. Passing 0
    (the default) disables jitter, which keeps tests and replays reproducible.
    """
    if attempt < 0:
        raise ValueError("attempt must be non-negative")
    if not -1.0 <= jitter <= 1.0:
        raise ValueError("jitter must be in [-1,1]")

    delay = min(policy.base_delay_s * policy.multiplier**attempt, policy.max_delay_s)
    factor = 1.0 + policy.jitter_ratio * jitter
    return max(0.0, delay * factor)


def is_retryable_status(status_code: int, policy: RetryPolicy) -> bool:
    """Whether an HTTP status code is worth retrying under the policy."""
    return status_code in policy.retryable_status_codes


def is_retryable_exception(exception: BaseException, policy: RetryPolicy) -> bool:
    """Whether an exception type is worth retrying under the policy."""
    return isinstance(exception, policy.retryable_exceptions)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a numeric ``Retry-After`` header; returns None for absent/invalid values."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if seconds < 0.0:
        return None
    return seconds


def plan_retry(
    *,
    attempt: int,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    status_code: int | None = None,
    exception: BaseException | None = None,
    retry_after: str | None = None,
    jitter: float = 0.0,
) -> RetryDecision:
    """Decide whether another attempt is allowed, and how long to wait."""
    if attempt < 0:
        raise ValueError("attempt must be non-negative")
    if status_code is None and exception is None:
        raise ValueError("provide a status_code or an exception to classify")

    retryable = False
    if status_code is not None:
        retryable = is_retryable_status(status_code, policy)
    if exception is not None and is_retryable_exception(exception, policy):
        retryable = True

    if not retryable:
        return RetryDecision(False, attempt, 0.0, "error is not retryable")
    if attempt + 1 >= policy.max_attempts:
        return RetryDecision(False, attempt, 0.0, "max attempts reached")

    header_delay = parse_retry_after(retry_after)
    if header_delay is not None:
        delay = min(header_delay, policy.max_delay_s)
        reason = "retrying after Retry-After header"
    else:
        delay = delay_for_attempt(attempt, policy, jitter=jitter)
        reason = "retrying with exponential backoff"
    return RetryDecision(True, attempt, delay, reason)


def backoff_schedule(
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    *,
    attempts: int | None = None,
    jitter: float = 0.0,
) -> tuple[float, ...]:
    """Delay before each retry, for the first ``attempts`` failures."""
    count = policy.max_attempts - 1 if attempts is None else attempts
    if count < 0:
        raise ValueError("attempts must be non-negative")
    return tuple(delay_for_attempt(index, policy, jitter=jitter) for index in range(count))


def retryable_statuses(policy: RetryPolicy = DEFAULT_RETRY_POLICY) -> Sequence[int]:
    """Sorted retryable status codes, for logging and documentation."""
    return tuple(sorted(policy.retryable_status_codes))
