"""Alertmanager webhook relay.

Prometheus/Alertmanager own alert evaluation and deduplication. This relay is
the delivery edge: it turns Alertmanager webhook payloads into

* **PagerDuty Events API v2** events (the pager path), and/or
* **Telegram** operator messages, and/or
* a generic JSON webhook (Slack, Opsgenie, incident.io, ...).

Keeping delivery in AXEL rather than in Alertmanager config means empty
credentials are a no-op instead of an invalid Alertmanager config, and the
message format matches the existing ``axel.comms.alerts`` conventions.

The relay is intentionally side-effect-free with respect to trading: it only
performs outbound HTTP calls and never imports ``risk/`` or ``execution/``.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

from axel.observability.api_health import ApiOutcome, record_api_call, record_api_error

logger = logging.getLogger("axel")

PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

PostJson = Callable[[str, dict[str, Any], float], int]


@dataclass(frozen=True)
class PrometheusAlert:
    """One Alertmanager alert, normalised for delivery."""

    status: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: str | None = None
    ends_at: str | None = None
    fingerprint: str = ""
    generator_url: str = ""

    @property
    def is_firing(self) -> bool:
        return self.status == "firing"

    @property
    def name(self) -> str:
        return self.labels.get("alertname", "AXEL alert")

    @property
    def severity(self) -> str:
        return self.labels.get("severity", "warning")

    @property
    def summary(self) -> str:
        return self.annotations.get("summary") or self.name

    @property
    def description(self) -> str:
        return self.annotations.get("description", "")


@dataclass(frozen=True)
class DispatchResult:
    accepted: int = 0
    pagerduty_sent: int = 0
    telegram_sent: int = 0
    webhook_sent: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "pagerduty_sent": self.pagerduty_sent,
            "telegram_sent": self.telegram_sent,
            "webhook_sent": self.webhook_sent,
            "errors": list(self.errors),
        }


def parse_alertmanager_payload(payload: dict[str, Any]) -> list[PrometheusAlert]:
    """Extract alerts from an Alertmanager webhook v4 payload."""
    alerts: list[PrometheusAlert] = []
    for item in payload.get("alerts", []):
        if not isinstance(item, dict):
            continue
        alerts.append(
            PrometheusAlert(
                status=str(item.get("status", payload.get("status", "firing"))),
                labels={str(k): str(v) for k, v in (item.get("labels") or {}).items()},
                annotations={
                    str(k): str(v) for k, v in (item.get("annotations") or {}).items()
                },
                starts_at=item.get("startsAt"),
                ends_at=item.get("endsAt"),
                fingerprint=str(item.get("fingerprint", "")),
                generator_url=str(item.get("generatorURL", "")),
            )
        )
    return alerts


def alert_to_pagerduty_event(alert: PrometheusAlert, routing_key: str) -> dict[str, Any]:
    """Map an Alertmanager alert to a PagerDuty Events API v2 event."""
    return {
        "routing_key": routing_key,
        "event_action": "trigger" if alert.is_firing else "resolve",
        "dedup_key": alert.fingerprint or alert.name,
        "payload": {
            "summary": f"{alert.name}: {alert.summary}",
            "source": "axel",
            "severity": _pagerduty_severity(alert.severity),
            "timestamp": alert.starts_at,
            "custom_details": {
                "labels": alert.labels,
                "annotations": alert.annotations,
                "generator_url": alert.generator_url,
            },
        },
    }


def alert_to_telegram_text(alert: PrometheusAlert) -> str:
    """Render a compact plain-text message (no Markdown escaping traps)."""
    icon = "🔴" if alert.is_firing else "✅"
    lines = [
        f"{icon} {alert.name} [{alert.severity}]",
        alert.summary,
    ]
    if alert.description:
        lines.append(alert.description)
    interesting = {
        key: value
        for key, value in alert.labels.items()
        if key not in {"alertname", "severity"}
    }
    if interesting:
        lines.append(" ".join(f"{key}={value}" for key, value in sorted(interesting.items())))
    if alert.generator_url:
        lines.append(alert.generator_url)
    return "\n".join(lines)


def _pagerduty_severity(severity: str) -> str:
    return {
        "critical": "critical",
        "warning": "warning",
        "info": "info",
    }.get(severity.lower(), "error")


def post_json(url: str, payload: dict[str, Any], timeout: float) -> int:
    """POST JSON with the standard library. Returns the HTTP status code."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "axel-alert-relay/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status)


def dispatch_alerts(
    payload: dict[str, Any],
    *,
    pagerduty_routing_key: str = "",
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    webhook_url: str = "",
    dry_run: bool = False,
    timeout_seconds: float = 10.0,
    post: PostJson = post_json,
) -> DispatchResult:
    """Deliver every alert in an Alertmanager payload to configured channels.

    A channel with empty credentials is skipped. Delivery failures are collected
    in ``errors`` and never raise: Alertmanager must still get a 2xx so it does
    not retry forever against a permanently broken downstream.
    """
    alerts = parse_alertmanager_payload(payload)
    pagerduty_sent = telegram_sent = webhook_sent = 0
    errors: list[str] = []

    if dry_run:
        for alert in alerts:
            logger.info(
                "Alert relay dry run",
                extra={
                    "alert": alert.name,
                    "status": alert.status,
                    "severity": alert.severity,
                    "summary": alert.summary,
                },
            )
        return DispatchResult(accepted=len(alerts))

    for alert in alerts:
        if pagerduty_routing_key:
            event = alert_to_pagerduty_event(alert, pagerduty_routing_key)
            try:
                status = post(PAGERDUTY_EVENTS_URL, event, timeout_seconds)
                record_api_call(
                    "pagerduty",
                    "events_v2",
                    status_code=status,
                    detail=f"alert={alert.name}",
                )
                if 200 <= status < 300:
                    pagerduty_sent += 1
                else:
                    errors.append(f"pagerduty {alert.name}: HTTP {status}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                record_api_error(
                    "pagerduty",
                    "events_v2",
                    ApiOutcome.NETWORK_ERROR,
                    detail=f"alert={alert.name}: {exc}",
                )
                errors.append(f"pagerduty {alert.name}: {exc}")

        if telegram_bot_token and telegram_chat_id:
            message = {
                "chat_id": telegram_chat_id,
                "text": alert_to_telegram_text(alert),
                "disable_notification": alert.severity != "critical",
            }
            try:
                status = post(
                    _TELEGRAM_API.format(token=telegram_bot_token),
                    message,
                    timeout_seconds,
                )
                record_api_call(
                    "telegram",
                    "send_message",
                    status_code=status,
                    detail=f"alert={alert.name}",
                )
                if 200 <= status < 300:
                    telegram_sent += 1
                else:
                    errors.append(f"telegram {alert.name}: HTTP {status}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                record_api_error(
                    "telegram",
                    "send_message",
                    ApiOutcome.NETWORK_ERROR,
                    detail=f"alert={alert.name}: {exc}",
                )
                errors.append(f"telegram {alert.name}: {exc}")

        if webhook_url:
            try:
                status = post(webhook_url, payload, timeout_seconds)
                record_api_call(
                    "webhook",
                    "alert_delivery",
                    status_code=status,
                    detail=f"alert={alert.name}",
                )
                if 200 <= status < 300:
                    webhook_sent += 1
                else:
                    errors.append(f"webhook {alert.name}: HTTP {status}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                record_api_error(
                    "webhook",
                    "alert_delivery",
                    ApiOutcome.NETWORK_ERROR,
                    detail=f"alert={alert.name}: {exc}",
                )
                errors.append(f"webhook {alert.name}: {exc}")

    if errors:
        logger.error("Alert relay delivery errors", extra={"errors": errors})

    return DispatchResult(
        accepted=len(alerts),
        pagerduty_sent=pagerduty_sent,
        telegram_sent=telegram_sent,
        webhook_sent=webhook_sent,
        errors=tuple(errors),
    )


def make_handler(dispatcher: Callable[[dict[str, Any]], DispatchResult]) -> type[BaseHTTPRequestHandler]:
    """Build a stdlib request handler bound to a dispatch callable."""

    class AlertRelayHandler(BaseHTTPRequestHandler):
        server_version = "AXELAlertRelay/0.1"

        def _write(self, status: int, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/healthz":
                self._write(200, "ok\n", "text/plain; charset=utf-8")
            elif path == "/":
                self._write(
                    200,
                    (
                        "<!doctype html><title>AXEL alert relay</title>"
                        "<h1>AXEL alert relay</h1>"
                        "<p>POST Alertmanager webhook payloads to <code>/alert</code>.</p>"
                    ),
                    "text/html; charset=utf-8",
                )
            else:
                self._write(404, "not found\n", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/alert":
                self._write(404, "not found\n", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._write(400, "invalid Content-Length\n", "text/plain; charset=utf-8")
                return
            if length <= 0 or length > 1_000_000:
                self._write(413, "payload too large or empty\n", "text/plain; charset=utf-8")
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._write(400, "invalid JSON\n", "text/plain; charset=utf-8")
                return
            if not isinstance(payload, dict):
                self._write(400, "payload must be a JSON object\n", "text/plain; charset=utf-8")
                return

            result = dispatcher(payload)
            self._write(
                202,
                json.dumps(result.to_dict()) + "\n",
                "application/json; charset=utf-8",
            )

        def log_message(self, fmt: str, *args: object) -> None:
            logger.debug("Alert relay request", extra={"line": fmt % args})

    return AlertRelayHandler

