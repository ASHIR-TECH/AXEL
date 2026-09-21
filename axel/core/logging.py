"""
Structured JSON logging for AXEL.
Outputs machine-readable JSON logs for auditability and observability.
"""

from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include custom attributes if present
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "message",
        }
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in standard_attrs and not k.startswith("_")
        }
        if extras:
            log_obj["extra"] = extras

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configures root logger with JSON formatting."""
    root_logger = logging.getLogger("axel")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers if called multiple times
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        root_logger.addHandler(handler)

    return root_logger


logger = setup_logging()
