"""Strategy registry, metadata and baseline implementations."""

from __future__ import annotations

from axel.strategies.baseline import MeanReversion, TrendFollowing
from axel.strategies.metadata import StrategyMetadata, StrategyStatus
from axel.strategies.registry import ALLOWED_TRANSITIONS, StrategyRegistry

__all__ = [
    "ALLOWED_TRANSITIONS",
    "MeanReversion",
    "StrategyMetadata",
    "StrategyRegistry",
    "StrategyStatus",
    "TrendFollowing",
]