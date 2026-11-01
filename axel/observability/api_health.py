"""
External API and transaction health tracking.

AXEL depends on several credentialed APIs (Alpaca, FRED, SEC, Groq/Qwen,
Telegram, PagerDuty). When one of them stops working the failure is often
silent: a stale token returns ``401``, a free tier returns ``429``/``402``, a
broker rejects an order with a ``4xx`` body, or a request simply times out.

This module gives every one of those outcomes:

* a stable structured log event (``api_call_failed``, ``api_quota_exhausted``,
  ``order_rejected`` ...), written to stdout and the rotating file;
* a durable ``api_provider_state`` row so the exporter can tell whether a
  token is exhausted and when the provider last succeeded;
* an append-only ``operational_events`` row for failures, which the exporter
  aggregates into Prometheus counters and alert rules.

Persistence is best-effort. Observability must never take down the trading
path: a database error here is logged and swallowed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.core.logging import log_event, logger, redact
from axel.db.models import ApiProviderStateModel, OperationalEventModel

# ── Outcomes ─────────────────────────────────────────────────────────────────


class ApiOutcome(StrEnum):
    """Normalised result of one external API call."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    AUTH_FAILED = "auth_failed"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    REJECTED = "rejected"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"

    @property
    def is_failure(self) -> bool:
        return self not in {ApiOutcome.SUCCESS, ApiOutcome.NOT_FOUND}


class OperationalCategory(StrEnum):
    """High-level source of an operational event."""

    API = "api"
    ORDER = "order"
    LLM = "llm"
    NOTIFICATION = "notification"
    DATA = "data"
    RISK = "risk"


_QUOTA_MARKERS = (
    "quota",
    "insufficient",
    "credit",
    "billing",
    "exhaust",
    "out of tokens",
    "token limit",
    "usage limit",
    "payment required",
    # Deliberately specific: a bare "limit exceeded" usually means a rate limit,
    # not an empty account.
    "daily limit",
    "monthly limit",
    "spend limit",
    "billing limit",
)


def classify_http_status(status_code: int, body: str = "") -> ApiOutcome:
    """Map an HTTP status (and optional error body) to an :class:`ApiOutcome`.

    A ``403`` is normally an auth failure, but providers also use it for
    "quota exceeded"; the body disambiguates. ``429`` is rate limiting unless
    the body clearly says the account is out of quota/credit.
    """
    lowered = body.lower()
    quota_signal = any(marker in lowered for marker in _QUOTA_MARKERS)

    if 200 <= status_code < 300:
        return ApiOutcome.SUCCESS
    if status_code == 404:
        return ApiOutcome.NOT_FOUND
    if status_code == 402:
        return ApiOutcome.QUOTA_EXHAUSTED
    if status_code in (401, 403):
        return ApiOutcome.QUOTA_EXHAUSTED if quota_signal else ApiOutcome.AUTH_FAILED
    if status_code == 429:
        return ApiOutcome.QUOTA_EXHAUSTED if quota_signal else ApiOutcome.RATE_LIMITED
    if status_code == 408:
        return ApiOutcome.TIMEOUT
    if 400 <= status_code < 500:
        return ApiOutcome.REJECTED
    if 500 <= status_code < 600:
        return ApiOutcome.SERVER_ERROR
    return ApiOutcome.UNKNOWN


def severity_for(outcome: ApiOutcome) -> str:
    if outcome in (ApiOutcome.AUTH_FAILED, ApiOutcome.QUOTA_EXHAUSTED):
        return "critical"
    if outcome is ApiOutcome.SUCCESS:
        return "info"
    return "warning"


def _header_float(headers: Any, name: str) -> float | None:
    value = headers.get(name) if headers is not None else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _header_int(headers: Any, *names: str) -> int | None:
    for name in names:
        value = headers.get(name) if headers is not None else None
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def extract_retry_after(headers: Any) -> float | None:
    """Parse ``Retry-After`` in seconds. HTTP-date form is ignored."""
    return _header_float(headers, "Retry-After")


def extract_rate_limit_remaining(headers: Any) -> int | None:
    return _header_int(
        headers,
        "X-RateLimit-Remaining",
        "X-RateLimit-Remaining-Requests",
        "RateLimit-Remaining",
    )


# ── Records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ApiCall:
    provider: str
    operation: str
    outcome: ApiOutcome
    status_code: int | None = None
    detail: str = ""
    retry_after_seconds: float | None = None
    rate_limit_remaining: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_failure(self) -> bool:
        return self.outcome.is_failure


@dataclass(frozen=True)
class OperationalEvent:
    category: OperationalCategory
    component: str
    event: str
    outcome: str
    severity: str = "warning"
    status_code: int | None = None
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


EventSink = Callable[[OperationalEvent, ApiCall | None], None]

_sink: EventSink | None = None
_sink_configured = False


def configure_event_sink(sink: EventSink | None) -> None:
    """Replace the persistence sink.

    ``None`` disables durable writes (used by unit tests). Passing a callable
    lets a caller own persistence, for example an in-memory test database.
    """
    global _sink, _sink_configured
    _sink = sink
    _sink_configured = True


def reset_event_sink() -> None:
    """Restore the default database sink (mainly for test teardown)."""
    global _sink, _sink_configured
    _sink = None
    _sink_configured = False


# ── Persistence ──────────────────────────────────────────────────────────────


def _apply_api_call(session: Session, call: ApiCall) -> None:
    state = session.get(ApiProviderStateModel, call.provider)
    if state is None:
        state = ApiProviderStateModel(provider=call.provider)
        session.add(state)

    state.last_operation = call.operation
    state.last_outcome = call.outcome.value
    state.last_status_code = call.status_code
    state.last_detail = redact(call.detail)[:2000] if call.detail else None
    if call.rate_limit_remaining is not None:
        state.rate_limit_remaining = call.rate_limit_remaining

    if call.outcome is ApiOutcome.SUCCESS:
        state.last_success_at = call.occurred_at
        state.consecutive_failures = 0
        state.quota_exhausted = False
        state.quota_exhausted_at = None
        state.retry_after_seconds = None
    elif call.outcome is ApiOutcome.NOT_FOUND:
        # A 404 is an expected answer (order not found), not a provider outage.
        state.last_success_at = call.occurred_at
        state.consecutive_failures = 0
    else:
        state.last_failure_at = call.occurred_at
        state.consecutive_failures = int(state.consecutive_failures or 0) + 1
        state.retry_after_seconds = call.retry_after_seconds
        if call.outcome is ApiOutcome.QUOTA_EXHAUSTED:
            state.quota_exhausted = True
            state.quota_exhausted_at = call.occurred_at

    if call.is_failure:
        session.add(
            OperationalEventModel(
                id=f"op-{uuid.uuid4().hex}",
                category=OperationalCategory.API.value,
                component=call.provider,
                event=f"api_{call.outcome.value}",
                outcome=call.outcome.value,
                severity=severity_for(call.outcome),
                status_code=call.status_code,
                detail=redact(call.detail)[:4000] if call.detail else None,
                payload=redact(
                    {
                        "operation": call.operation,
                        "retry_after_seconds": call.retry_after_seconds,
                        "rate_limit_remaining": call.rate_limit_remaining,
                        **call.payload,
                    }
                ),
                occurred_at=call.occurred_at,
            )
        )
    session.flush()


def _apply_operational_event(session: Session, event: OperationalEvent) -> None:
    session.add(
        OperationalEventModel(
            id=f"op-{uuid.uuid4().hex}",
            category=event.category.value,
            component=event.component,
            event=event.event,
            outcome=event.outcome,
            severity=event.severity,
            status_code=event.status_code,
            detail=redact(event.detail)[:4000] if event.detail else None,
            payload=redact(event.payload or {}),
            occurred_at=event.occurred_at,
        )
    )
    session.flush()


def _apply_token_usage(
    session: Session,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> None:
    state = session.get(ApiProviderStateModel, provider)
    if state is None:
        state = ApiProviderStateModel(provider=provider)
        session.add(state)
    state.prompt_tokens_total = int(state.prompt_tokens_total or 0) + prompt_tokens
    state.completion_tokens_total = int(state.completion_tokens_total or 0) + completion_tokens
    state.estimated_cost_usd_total = float(state.estimated_cost_usd_total or 0.0) + cost_usd
    session.flush()


def _default_session():
    from axel.db.session import SessionLocal

    return SessionLocal()


def _persist(
    apply: Callable[[Session], None],
    *,
    session: Session | None,
    event: OperationalEvent | None = None,
    call: ApiCall | None = None,
) -> None:
    # An explicit session always wins: the caller owns the transaction and may
    # be an in-memory test database.
    if session is not None:
        try:
            apply(session)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Operational event persistence failed.",
                extra={"error": str(exc), "provider": call.provider if call else None},
            )
        return

    if _sink_configured:
        if _sink is not None and event is not None:
            _sink(event, call)
        return
    if not settings.persist_operational_events:
        return

    db = _default_session()
    try:
        apply(db)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - observability must not break trading
        db.rollback()
        logger.debug(
            "Operational event persistence failed.",
            extra={"error": str(exc), "provider": call.provider if call else None},
        )
    finally:
        db.close()


# ── Public recording API ─────────────────────────────────────────────────────


def record_api_call(
    provider: str,
    operation: str,
    *,
    outcome: ApiOutcome | None = None,
    status_code: int | None = None,
    detail: str = "",
    retry_after_seconds: float | None = None,
    rate_limit_remaining: int | None = None,
    payload: dict[str, Any] | None = None,
    session: Session | None = None,
) -> ApiCall:
    """Record one API call, log it, and update durable provider health."""
    resolved = outcome or classify_http_status(status_code or 0, detail)
    call = ApiCall(
        provider=provider,
        operation=operation,
        outcome=resolved,
        status_code=status_code,
        detail=detail,
        retry_after_seconds=retry_after_seconds,
        rate_limit_remaining=rate_limit_remaining,
        payload=payload or {},
    )

    if call.is_failure:
        log_event(
            "critical" if resolved in (ApiOutcome.AUTH_FAILED, ApiOutcome.QUOTA_EXHAUSTED) else "warning",
            f"api_{resolved.value}",
            f"{provider} {operation} failed: {resolved.value}",
            provider=provider,
            operation=operation,
            outcome=resolved.value,
            status_code=status_code,
            detail=detail[:1000] if detail else "",
            retry_after_seconds=retry_after_seconds,
            rate_limit_remaining=rate_limit_remaining,
        )
    else:
        log_event(
            "info",
            "api_call_succeeded",
            f"{provider} {operation} succeeded",
            provider=provider,
            operation=operation,
            outcome=resolved.value,
            status_code=status_code,
            rate_limit_remaining=rate_limit_remaining,
        )

    _persist(
        lambda db: _apply_api_call(db, call),
        session=session,
        call=call,
        event=OperationalEvent(
            category=OperationalCategory.API,
            component=provider,
            event=f"api_{resolved.value}",
            outcome=resolved.value,
            severity=severity_for(resolved),
            status_code=status_code,
            detail=detail,
            payload={"operation": operation, **(payload or {})},
            occurred_at=call.occurred_at,
        ),
    )
    return call


def record_http_call(
    provider: str,
    operation: str,
    response: Any,
    *,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
    session: Session | None = None,
) -> ApiCall:
    """Record an ``httpx``-style response without consuming its body twice."""
    status_code = int(getattr(response, "status_code", 0) or 0)
    body = ""
    if status_code >= 400:
        try:
            body = response.text[:2000]
        except Exception:  # noqa: BLE001 - body is best-effort context
            body = ""
    resolved = classify_http_status(status_code, body)
    return record_api_call(
        provider,
        operation,
        outcome=resolved,
        status_code=status_code,
        detail=detail if detail is not None else body[:1000],
        retry_after_seconds=extract_retry_after(getattr(response, "headers", None)),
        rate_limit_remaining=extract_rate_limit_remaining(getattr(response, "headers", None)),
        payload=payload,
        session=session,
    )


def record_api_error(
    provider: str,
    operation: str,
    outcome: ApiOutcome,
    *,
    detail: str = "",
    payload: dict[str, Any] | None = None,
    session: Session | None = None,
) -> ApiCall:
    """Record a transport-level failure that has no HTTP response."""
    return record_api_call(
        provider,
        operation,
        outcome=outcome,
        detail=detail,
        payload=payload,
        session=session,
    )


def record_operational_event(
    category: OperationalCategory,
    component: str,
    event: str,
    *,
    outcome: str,
    severity: str = "warning",
    status_code: int | None = None,
    detail: str = "",
    payload: dict[str, Any] | None = None,
    session: Session | None = None,
) -> OperationalEvent:
    """Log and persist a non-API operational event (order, LLM, notification)."""
    record = OperationalEvent(
        category=category,
        component=component,
        event=event,
        outcome=outcome,
        severity=severity,
        status_code=status_code,
        detail=detail,
        payload=payload or {},
    )
    log_event(
        severity if severity in {"debug", "info", "warning", "error", "critical"} else "warning",
        event,
        detail or f"{component}: {event}",
        category=category.value,
        component=component,
        outcome=outcome,
        status_code=status_code,
        **(payload or {}),
    )
    _persist(
        lambda db: _apply_operational_event(db, record),
        session=session,
        event=record,
    )
    return record


def record_token_usage(
    provider: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float = 0.0,
    model: str = "",
    session: Session | None = None,
) -> None:
    """Log and accumulate LLM token/cost usage for quota and spend visibility."""
    log_event(
        "info",
        "llm_token_usage",
        f"{provider} completion used {prompt_tokens}+{completion_tokens} tokens",
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cost_usd=round(cost_usd, 8),
    )
    _persist(
        lambda db: _apply_token_usage(db, provider, prompt_tokens, completion_tokens, cost_usd),
        session=session,
        call=ApiCall(
            provider=provider,
            operation="token_usage",
            outcome=ApiOutcome.SUCCESS,
        ),
    )


def load_provider_states(session: Session) -> list[ApiProviderStateModel]:
    """All known provider rows, ordered for stable metric output."""
    from sqlalchemy import select

    return list(
        session.execute(
            select(ApiProviderStateModel).order_by(ApiProviderStateModel.provider)
        ).scalars()
    )


__all__ = [
    "ApiCall",
    "ApiOutcome",
    "OperationalCategory",
    "OperationalEvent",
    "classify_http_status",
    "configure_event_sink",
    "extract_rate_limit_remaining",
    "extract_retry_after",
    "load_provider_states",
    "record_api_call",
    "record_api_error",
    "record_http_call",
    "record_operational_event",
    "record_token_usage",
    "reset_event_sink",
    "severity_for",
]
