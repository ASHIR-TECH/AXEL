"""Operational observability helpers: metrics exporter, alert relay and API health."""

from __future__ import annotations

from typing import Any

__all__ = ["MetricsExporter", "make_handler", "run_server"]


def __getattr__(name: str) -> Any:
    """Import the exporter lazily.

    ``axel.observability.api_health`` is imported by the broker, LLM and data
    adapters. Eagerly importing the exporter here would drag the whole metrics
    stack into those hot paths (and risk an import cycle).
    """
    if name in __all__:
        from axel.observability import exporter

        return getattr(exporter, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
