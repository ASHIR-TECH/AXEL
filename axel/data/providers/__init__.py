"""Read-only data providers for Phase 2 ingestion and cross-asset market data.

Public surface kept stable for existing callers (``axel.data.providers``).
"""

from __future__ import annotations

from axel.data.providers.alpaca import AlpacaMarketData, parse_bars
from axel.data.providers.base import (
    Capability,
    CapabilityAwareProvider,
    CapabilityError,
    MarketDataProvider,
    Provider,
    ProviderCapabilities,
    ProviderDataError,
    ProviderHealth,
    ProviderRequestError,
    ProviderResult,
    now_utc,
    require_api_key,
)
from axel.data.providers.binance import BinancePublicMarketData
from axel.data.providers.deribit import DeribitOptionsMarketData
from axel.data.providers.fred import FredData, parse_observations
from axel.data.providers.news import GdeltNews, parse_news
from axel.data.providers.oanda import OandaMarketData
from axel.data.providers.sec import SecEdgar, parse_filings

__all__ = [
    "AlpacaMarketData",
    "BinancePublicMarketData",
    "Capability",
    "CapabilityAwareProvider",
    "CapabilityError",
    "DeribitOptionsMarketData",
    "FredData",
    "GdeltNews",
    "MarketDataProvider",
    "OandaMarketData",
    "Provider",
    "ProviderCapabilities",
    "ProviderDataError",
    "ProviderHealth",
    "ProviderRequestError",
    "ProviderResult",
    "SecEdgar",
    "now_utc",
    "parse_bars",
    "parse_filings",
    "parse_news",
    "parse_observations",
    "require_api_key",
]
