"""Baseline strategies: the minimum bar Phase 2 must clear."""

from __future__ import annotations

from axel.strategies.baseline.mean_reversion import MeanReversion
from axel.strategies.baseline.trend import TrendFollowing

__all__ = ["MeanReversion", "TrendFollowing"]