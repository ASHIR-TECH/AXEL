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
from axel.data.features.microstructure import (
    depth_imbalance,
    depth_imbalance_within_bps,
    microprice_deviation_bps,
    quote_imbalance,
    spread_bps,
    trade_flow_imbalance,
)
from axel.data.features.momentum import average_true_momentum, momentum, rsi
from axel.data.features.price import close_price, log_return, overnight_gap, simple_return
from axel.data.features.sentiment import keyword_sentiment, news_count
from axel.data.features.technical import (
    average_directional_index,
    bollinger_bandwidth,
    bollinger_percent_b,
    donchian_position,
    ema,
    macd,
    on_balance_volume,
    stochastic_oscillator,
)
from axel.data.features.volatility import (
    average_true_range,
    ewma_volatility,
    garman_klass_volatility,
    parkinson_volatility,
    realized_volatility,
    rogers_satchell_volatility,
    yang_zhang_volatility,
)
from axel.data.features.volume import dollar_volume, volume, volume_ratio, volume_zscore

__all__ = [
    "FEATURE_VERSION",
    "FeatureRecord",
    "average_directional_index",
    "average_true_momentum",
    "average_true_range",
    "bollinger_bandwidth",
    "bollinger_percent_b",
    "close_price",
    "demean_by_time",
    "depth_imbalance",
    "depth_imbalance_within_bps",
    "dollar_volume",
    "donchian_position",
    "ema",
    "ewma_volatility",
    "feature_for",
    "feature_params",
    "filing_change",
    "filing_value",
    "garman_klass_volatility",
    "keyword_sentiment",
    "log_return",
    "macd",
    "macro_change",
    "macro_value",
    "make_feature",
    "max_available",
    "microprice_deviation_bps",
    "momentum",
    "news_count",
    "on_balance_volume",
    "overnight_gap",
    "parkinson_volatility",
    "quote_imbalance",
    "rank_by_time",
    "realized_volatility",
    "rogers_satchell_volatility",
    "rsi",
    "simple_return",
    "spread_bps",
    "stochastic_oscillator",
    "trade_flow_imbalance",
    "volume",
    "volume_ratio",
    "volume_zscore",
    "yang_zhang_volatility",
    "zscore_by_time",
]
