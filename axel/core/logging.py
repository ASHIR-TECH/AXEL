"""
Structured JSON logging for AXEL.

Every external interaction, risk decision, order transition and alert delivery
is emitted as one JSON object per line. Logs are the durable, human-readable
record of *why* something happened; Prometheus metrics are the aggregate view.

Two sinks are supported:

* stdout (always) — collected by Docker / systemd / journald;
* an optional rotating file (``LOG_FILE``) — useful when the process is not
  containerised or when logs must be retained locally for incident review.

Secret values are redacted before serialisation. Never log a raw API key,
bot token, PagerDuty routing key or credential-bearing webhook URL.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_REDACTED = "***REDACTED***"

# Only exact, sensitive key names are redacted. Do not match ``prompt_tokens``
# or ``completion_tokens``: those are operational data, not secrets.
_SECRET_KEY = re.compile(
    r"^("
    r"api[_-]?key|secret|secret[_-]?key|"
    r"access[_-]?token|auth[_-]?token|bot[_-]?token|refresh[_-]?token|"
    r"password|authorization|routing[_-]?key|integration[_-]?key|"
    r"private[_-]?key|webhook[_-]?url"
    r")$",
    re.IGNORECASE,
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAPCA-[A-Z0-9-]{8,}\b"),
)

_STANDARD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname",
    "filename", "module", "exc_info", "exc_text", "stack_info",
    "lineno", "funcName", "created", "msecs", "relativeCreated",
    "thread", "threadName", "processName", "process", "message",
    "taskName",
}


def _redact_string(value: str) -> str:
    for pattern in _SECRET_VALUE_PATTERNS:
        value = pattern.sub(_REDACTED, value)
    return value


def _redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact secret-named keys and credential-shaped values."""
    if key is not None and _SECRET_KEY.match(key):
        return _REDACTED
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact(value: Any) -> Any:
    """Public redaction helper for values persisted outside the log formatter."""
    return _redact(value)


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_string(record.getMessage()),
        }

        event = getattr(record, "event", None)
        if event:
            log_obj["event"] = event

        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and not k.startswith("_") and k != "event"
        }
        if extras:
            log_obj["extra"] = _redact(extras)

        if record.exc_info:
            log_obj["exception"] = _redact(self.formatException(record.exc_info))

        return json.dumps(log_obj, default=str, ensure_ascii=False)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def setup_logging(
    level: str | int | None = None,
    *,
    log_file: str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the ``axel`` logger with stdout and optional rotating file output.

    ``log_file=""`` (the default) keeps stdout-only behaviour. Pass an explicit
    path, or set ``LOG_FILE``, to also write JSON lines to disk.
    """
    root_logger = logging.getLogger("axel")
    resolved_level = level if level is not None else os.getenv("LOG_LEVEL", "INFO")
    if isinstance(resolved_level, str):
        resolved_level = getattr(logging, resolved_level.upper(), logging.INFO)
    root_logger.setLevel(resolved_level)

    if root_logger.handlers and not force:
        return root_logger

    if force:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = JSONFormatter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    path = log_file if log_file is not None else os.getenv("LOG_FILE", "")
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            destination,
            maxBytes=max_bytes if max_bytes is not None else _env_int("LOG_MAX_BYTES", 10_000_000),
            backupCount=(
                backup_count if backup_count is not None else _env_int("LOG_BACKUP_COUNT", 5)
            ),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Prevent duplicate records if the application root logger also has handlers.
    root_logger.propagate = False
    return root_logger


def log_event(
    level: str | int,
    event: str,
    message: str,
    **fields: Any,
) -> None:
    """Emit one structured event.

    ``event`` is a stable, machine-searchable name such as
    ``order_rejected`` or ``api_quota_exhausted``. Fields are redacted by the
    formatter before they reach any sink.
    """
    resolved_level = (
        getattr(logging, level.upper(), logging.INFO) if isinstance(level, str) else level
    )
    logger.log(resolved_level, message, extra={"event": event, **fields})


logger = setup_logging()
