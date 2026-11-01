"""Tests for the Alertmanager -> PagerDuty/Telegram/webhook relay."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from axel.observability.alert_relay import (
    DispatchResult,
    alert_to_pagerduty_event,
    alert_to_telegram_text,
    dispatch_alerts,
    make_handler,
    parse_alertmanager_payload,
)

PAYLOAD = {
    "version": "4",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "AxelKillSwitchActive",
                "severity": "critical",
                "component": "risk",
            },
            "annotations": {
                "summary": "AXEL global kill switch is tripped",
                "description": "All execution is halted.",
            },
            "startsAt": "2026-10-31T22:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": "abc123",
            "generatorURL": "http://prometheus:9090/graph?g0.expr=axel_kill_switch_active",
        }
    ],
}


def test_parse_alertmanager_payload():
    alerts = parse_alertmanager_payload(PAYLOAD)
    assert len(alerts) == 1
    assert alerts[0].name == "AxelKillSwitchActive"
    assert alerts[0].severity == "critical"
    assert alerts[0].is_firing is True


def test_pagerduty_event_trigger_and_resolve():
    alert = parse_alertmanager_payload(PAYLOAD)[0]
    trigger = alert_to_pagerduty_event(alert, "routing-key")
    assert trigger["event_action"] == "trigger"
    assert trigger["dedup_key"] == "abc123"
    assert trigger["payload"]["severity"] == "critical"
    assert trigger["payload"]["source"] == "axel"

    resolved_payload = json.loads(json.dumps(PAYLOAD))
    resolved_payload["alerts"][0]["status"] = "resolved"
    resolved = parse_alertmanager_payload(resolved_payload)[0]
    assert alert_to_pagerduty_event(resolved, "routing-key")["event_action"] == "resolve"


def test_telegram_text_contains_identity_and_labels():
    alert = parse_alertmanager_payload(PAYLOAD)[0]
    text = alert_to_telegram_text(alert)
    assert "AxelKillSwitchActive" in text
    assert "critical" in text
    assert "AXEL global kill switch is tripped" in text
    assert "component=risk" in text


def test_dispatch_sends_to_all_configured_channels():
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, payload: dict, timeout: float) -> int:
        calls.append((url, payload))
        return 202

    result = dispatch_alerts(
        PAYLOAD,
        pagerduty_routing_key="pd-key",
        telegram_bot_token="bot-token",
        telegram_chat_id="123",
        webhook_url="https://example.invalid/hook",
        post=fake_post,
    )
    assert result == DispatchResult(
        accepted=1,
        pagerduty_sent=1,
        telegram_sent=1,
        webhook_sent=1,
    )
    urls = [url for url, _ in calls]
    assert any("events.pagerduty.com" in url for url in urls)
    assert any("api.telegram.org" in url for url in urls)
    assert "https://example.invalid/hook" in urls


def test_dispatch_skips_unconfigured_channels():
    calls: list[str] = []

    def fake_post(url: str, payload: dict, timeout: float) -> int:
        calls.append(url)
        return 202

    result = dispatch_alerts(PAYLOAD, post=fake_post)
    assert result.accepted == 1
    assert result.pagerduty_sent == 0
    assert result.telegram_sent == 0
    assert result.webhook_sent == 0
    assert calls == []


def test_dispatch_dry_run_does_not_call_out():
    def fail_post(url: str, payload: dict, timeout: float) -> int:
        raise AssertionError("dry run must not post")

    result = dispatch_alerts(PAYLOAD, pagerduty_routing_key="pd-key", dry_run=True, post=fail_post)
    assert result == DispatchResult(accepted=1)


def test_dispatch_collects_delivery_errors():
    def broken_post(url: str, payload: dict, timeout: float) -> int:
        raise OSError("network down")

    result = dispatch_alerts(
        PAYLOAD,
        pagerduty_routing_key="pd-key",
        telegram_bot_token="bot-token",
        telegram_chat_id="123",
        post=broken_post,
    )
    assert result.pagerduty_sent == 0
    assert result.telegram_sent == 0
    assert len(result.errors) == 2
    assert any("pagerduty" in error for error in result.errors)
    assert any("telegram" in error for error in result.errors)


def test_http_handler_accepts_alertmanager_payload():
    received: list[dict] = []

    def dispatcher(payload: dict) -> DispatchResult:
        received.append(payload)
        return DispatchResult(accepted=len(payload.get("alerts", [])))

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(dispatcher))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        request = urllib.request.Request(
            f"http://{host}:{port}/alert",
            data=json.dumps(PAYLOAD).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 202
            body = json.loads(response.read())
        assert body["accepted"] == 1
        assert len(received) == 1
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=5) as response:
            assert response.read().decode().strip() == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
