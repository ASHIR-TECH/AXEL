"""Tests for structured JSON logging, redaction and rotating file output."""

from __future__ import annotations

import io
import json
import logging

from axel.core.logging import JSONFormatter, log_event, setup_logging


def _capture(message: str, **extra: object) -> dict:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    log = logging.getLogger("axel.test.capture")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.info(message, extra=extra)
    return json.loads(stream.getvalue())


def test_formatter_emits_one_json_object_with_event():
    record = _capture(
        "order rejected",
        event="order_rejected",
        client_order_id="coid-1",
        status_code=403,
    )
    assert record["level"] == "INFO"
    assert record["event"] == "order_rejected"
    assert record["message"] == "order rejected"
    assert record["extra"]["client_order_id"] == "coid-1"
    assert record["extra"]["status_code"] == 403


def test_formatter_redacts_secret_named_fields():
    record = _capture(
        "calling provider",
        api_key="sk-live-abcdefghijklmnop",
        secret_key="very-secret",
        authorization="Bearer abc.def.ghi",
        password="hunter2",
        routing_key="pd-routing",
        prompt_tokens=42,
    )
    assert record["extra"]["api_key"] == "***REDACTED***"
    assert record["extra"]["secret_key"] == "***REDACTED***"
    assert record["extra"]["authorization"] == "***REDACTED***"
    assert record["extra"]["password"] == "***REDACTED***"
    assert record["extra"]["routing_key"] == "***REDACTED***"
    # Token *counts* are operational data, not secrets.
    assert record["extra"]["prompt_tokens"] == 42


def test_formatter_redacts_nested_and_credential_shaped_values():
    record = _capture(
        "provider payload",
        request={"headers": {"Authorization": "Bearer top-secret-token"}, "body": "ok"},
        note="key sk-live-abcdefghijklmnop was used",
        webhook_url="https://hooks.example.com/services/T000/B000/SECRET",
    )
    assert record["extra"]["request"]["headers"]["Authorization"] == "***REDACTED***"
    assert "sk-live" not in record["extra"]["note"]
    assert record["extra"]["webhook_url"] == "***REDACTED***"


def test_log_event_uses_stable_event_name():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    logger = logging.getLogger("axel")
    original_handlers = list(logger.handlers)
    logger.handlers = [handler]
    try:
        log_event("warning", "api_quota_exhausted", "groq is out of credit", provider="groq")
        payload = json.loads(stream.getvalue())
    finally:
        logger.handlers = original_handlers

    assert payload["level"] == "WARNING"
    assert payload["event"] == "api_quota_exhausted"
    assert payload["extra"]["provider"] == "groq"


def test_setup_logging_writes_rotating_file(tmp_path):
    log_path = tmp_path / "logs" / "axel.log"
    setup_logging(
        "INFO",
        log_file=str(log_path),
        max_bytes=1_000_000,
        backup_count=2,
        force=True,
    )
    try:
        log_event("info", "test_event", "written to disk", value=7)
        for handler in logging.getLogger("axel").handlers:
            handler.flush()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert lines
        payload = json.loads(lines[-1])
        assert payload["event"] == "test_event"
        assert payload["extra"]["value"] == 7
    finally:
        setup_logging("INFO", log_file="", force=True)


def test_empty_log_file_keeps_stdout_only(tmp_path):
    setup_logging("INFO", log_file="", force=True)
    try:
        handlers = logging.getLogger("axel").handlers
        assert handlers
        assert not any(
            isinstance(handler, logging.FileHandler) for handler in handlers
        )
    finally:
        setup_logging("INFO", log_file="", force=True)
