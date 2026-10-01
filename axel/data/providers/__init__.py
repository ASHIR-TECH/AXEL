"""Read-only data providers for Phase 2 ingestion.

Public surface kept stable for existing callers (``axel.data.providers``).
"""

from __future__ import annotations

from axel.data.providers.alpaca import AlpacaMarketData, parse_bars
from axel.data.providers.base import Provider, ProviderResult, now_utc
from axel.data.providers.fred import FredData, parse_observations
from axel.data.providers.news import GdeltNews, parse_news
from axel.data.providers.sec import SecEdgar, parse_filings

__all__ = [
    "AlpacaMarketData",
    "FredData",
    "GdeltNews",
    "Provider",
    "ProviderResult",
    "SecEdgar",
    "now_utc",
    "parse_bars",
    "parse_filings",
    "parse_news",
    "parse_observations",
]
