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
from axel.data.providers.equibles import EquiblesMarketData, parse_prices
from axel.data.providers.fred import FredData, parse_observations
from axel.data.providers.kalshi import KalshiPredictionMarketData
from axel.data.providers.massive import MassiveMarketData, parse_aggregates
from axel.data.providers.news import GdeltNews, parse_news
from axel.data.providers.oanda import OandaMarketData
from axel.data.providers.retry import (
    DEFAULT_RETRY_POLICY,
    DEFAULT_RETRYABLE_STATUS_CODES,
    RetryDecision,
    RetryPolicy,
    backoff_schedule,
    delay_for_attempt,
    is_retryable_exception,
    is_retryable_status,
    parse_retry_after,
    plan_retry,
    retryable_statuses,
)
from axel.data.providers.sec import SecEdgar, parse_filings

__all__ = [
    "DEFAULT_RETRYABLE_STATUS_CODES",
    "DEFAULT_RETRY_POLICY",
    "AlpacaMarketData",
    "BinancePublicMarketData",
    "Capability",
    "CapabilityAwareProvider",
    "CapabilityError",
    "DeribitOptionsMarketData",
    "EquiblesMarketData",
    "FredData",
    "GdeltNews",
    "KalshiPredictionMarketData",
    "MarketDataProvider",
    "MassiveMarketData",
    "OandaMarketData",
    "Provider",
    "ProviderCapabilities",
    "ProviderDataError",
    "ProviderHealth",
    "ProviderRequestError",
    "ProviderResult",
    "RetryDecision",
    "RetryPolicy",
    "SecEdgar",
    "backoff_schedule",
    "delay_for_attempt",
    "is_retryable_exception",
    "is_retryable_status",
    "now_utc",
    "parse_aggregates",
    "parse_bars",
    "parse_filings",
    "parse_news",
    "parse_observations",
    "parse_prices",
    "parse_retry_after",
    "plan_retry",
    "require_api_key",
    "retryable_statuses",
]
