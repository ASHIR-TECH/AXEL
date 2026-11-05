"""Volatility deterministic features (single series)."""

from __future__ import annotations

from collections.abc import Sequence
from math import log, sqrt

from axel.data.features.base import (
    FeatureRecord,
    by_time,
    feature_for,
    max_available,
    mean,
    param,
    returns,
    sample_std,
)
from axel.data.schemas import BarRecord

_TRADING_DAYS = 252


def realized_volatility(
    bars: Sequence[BarRecord], *, window: int = 20, annualize: bool = True
) -> list[FeatureRecord]:
    """Sample standard deviation of simple returns over ``window`` bars."""
    if window < 2:
        raise ValueError("window must be >= 2")
    series = by_time(bars)
    series_returns = returns([bar.close for bar in series])
    params = param(window=window, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        window_returns = series_returns[index - window : index]
        features.append(
            feature_for(
                f"realized_volatility_{window}",
                series[index],
                sample_std(window_returns) * scale,
                params=params,
                available_at=max_available(series[index - window : index + 1]),
            )
        )
    return features


def average_true_range(bars: Sequence[BarRecord], *, window: int = 14) -> list[FeatureRecord]:
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    true_ranges = _true_ranges(series)
    params = param(window=window)
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        addr = max_available(series[index - window : index + 1])
        features.append(
            feature_for(
                f"atr_{window}",
                series[index],
                mean(true_ranges[index - window : index]),
                params=params,
                available_at=addr,
            )
        )
    return features


def parkinson_volatility(
    bars: Sequence[BarRecord], *, window: int = 20, annualize: bool = True
) -> list[FeatureRecord]:
    """Range-based volatility estimator using only high/low."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    denominator = 4.0 * log(2.0)
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        squared = [(log(bar.high / bar.low)) ** 2 for bar in used]
        features.append(
            feature_for(
                f"parkinson_volatility_{window}",
                series[index],
                sqrt(sum(squared) / (denominator * len(used))) * scale,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def _true_ranges(series: Sequence[BarRecord]) -> list[float]:
    ranges: list[float] = []
    for index, bar in enumerate(series):
        if index == 0:
            ranges.append(bar.high - bar.low)
            continue
        previous_close = series[index - 1].close
        ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return ranges


def garman_klass_volatility(
    bars: Sequence[BarRecord], *, window: int = 20, annualize: bool = True
) -> list[FeatureRecord]:
    """Garman-Klass range estimator using open, high, low and close."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        variance = sum(_garman_klass_variance(bar) for bar in used) / len(used)
        features.append(
            feature_for(
                f"garman_klass_volatility_{window}",
                series[index],
                sqrt(max(variance, 0.0)) * scale,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def rogers_satchell_volatility(
    bars: Sequence[BarRecord], *, window: int = 20, annualize: bool = True
) -> list[FeatureRecord]:
    """Rogers-Satchell estimator, which tolerates a non-zero drift."""
    if window < 1:
        raise ValueError("window must be >= 1")
    series = by_time(bars)
    params = param(window=window, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    features: list[FeatureRecord] = []
    for index in range(window - 1, len(series)):
        used = series[index - window + 1 : index + 1]
        variance = sum(_rogers_satchell_variance(bar) for bar in used) / len(used)
        features.append(
            feature_for(
                f"rogers_satchell_volatility_{window}",
                series[index],
                sqrt(max(variance, 0.0)) * scale,
                params=params,
                available_at=max_available(used),
            )
        )
    return features


def yang_zhang_volatility(
    bars: Sequence[BarRecord], *, window: int = 20, annualize: bool = True
) -> list[FeatureRecord]:
    """Yang-Zhang estimator: overnight jumps, open-to-close and range terms."""
    if window < 2:
        raise ValueError("window must be >= 2")
    series = by_time(bars)
    params = param(window=window, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    features: list[FeatureRecord] = []
    for index in range(window, len(series)):
        used = series[index - window + 1 : index + 1]
        overnight = [
            log(bar.open / series[index - window + offset].close)
            for offset, bar in enumerate(used)
        ]
        open_close = [log(bar.close / bar.open) for bar in used]
        rs_variance = sum(_rogers_satchell_variance(bar) for bar in used) / len(used)
        weight = 0.34 / (1.34 + (window + 1) / (window - 1))
        variance = (
            sample_std(overnight) ** 2
            + weight * sample_std(open_close) ** 2
            + (1.0 - weight) * rs_variance
        )
        features.append(
            feature_for(
                f"yang_zhang_volatility_{window}",
                series[index],
                sqrt(max(variance, 0.0)) * scale,
                params=params,
                available_at=max_available(series[index - window : index + 1]),
            )
        )
    return features


def ewma_volatility(
    bars: Sequence[BarRecord],
    *,
    window: int = 20,
    decay: float = 0.94,
    annualize: bool = True,
) -> list[FeatureRecord]:
    """RiskMetrics-style exponentially weighted volatility of simple returns."""
    if window < 2:
        raise ValueError("window must be >= 2")
    if not 0.0 < decay < 1.0:
        raise ValueError("decay must be in (0,1)")
    series = by_time(bars)
    series_returns = returns([bar.close for bar in series])
    params = param(window=window, decay=decay, annualize=annualize)
    scale = sqrt(_TRADING_DAYS) if annualize else 1.0
    features: list[FeatureRecord] = []
    if len(series_returns) < window:
        return features
    variance = sample_std(series_returns[:window]) ** 2
    features.append(
        feature_for(
            f"ewma_volatility_{window}",
            series[window],
            sqrt(max(variance, 0.0)) * scale,
            params=params,
            available_at=max_available(series[: window + 1]),
        )
    )
    for index in range(window + 1, len(series)):
        variance = decay * variance + (1.0 - decay) * series_returns[index - 1] ** 2
        features.append(
            feature_for(
                f"ewma_volatility_{window}",
                series[index],
                sqrt(max(variance, 0.0)) * scale,
                params=params,
                available_at=max_available(series[index - window : index + 1]),
            )
        )
    return features


def _garman_klass_variance(bar: BarRecord) -> float:
    return 0.5 * log(bar.high / bar.low) ** 2 - (2.0 * log(2.0) - 1.0) * log(
        bar.close / bar.open
    ) ** 2


def _rogers_satchell_variance(bar: BarRecord) -> float:
    return log(bar.high / bar.close) * log(bar.high / bar.open) + log(
        bar.low / bar.close
    ) * log(bar.low / bar.open)
