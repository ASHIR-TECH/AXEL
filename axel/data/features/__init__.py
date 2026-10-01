"""Deterministic feature pipeline: pure functions over normalized records."""

from __future__ import annotations

from axel.data.features.base import (
    FEATURE_VERSION,
    FeatureRecord,
    feature_for,
    feature_params,
    make_feature,
    max_available,
)
from axel.data.features.cross_sectional import (
    demean_by_time,
    rank_by_time,
    zscore_by_time,
)
from axel.data.features.fundamentals import filing_change, filing_value
from axel.data.features.macro import macro_change, macro_value
from axel.data.features.momentum import average_true_momentum, momentum, rsi
from axel.data.features.price import close_price, log_return, overnight_gap, simple_return
from axel.data.features.sentiment import keyword_sentiment, news_count
from axel.data.features.volatility import (
    average_true_range,
    parkinson_volatility,
    realized_volatility,
)
from axel.data.features.volume import dollar_volume, volume, volume_ratio, volume_zscore

__all__ = [
    "FEATURE_VERSION",
    "FeatureRecord",
    "average_true_momentum",
    "average_true_range",
    "close_price",
    "demean_by_time",
    "dollar_volume",
    "feature_for",
    "feature_params",
    "filing_change",
    "filing_value",
    "keyword_sentiment",
    "log_return",
    "macro_change",
    "macro_value",
    "make_feature",
    "max_available",
    "momentum",
    "news_count",
    "overnight_gap",
    "parkinson_volatility",
    "rank_by_time",
    "realized_volatility",
    "rsi",
    "simple_return",
    "volume",
    "volume_ratio",
    "volume_zscore",
    "zscore_by_time",
]
