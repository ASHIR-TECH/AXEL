"""
Performance metrics for equity curves and round-trip trades.

Annualisation is derived from *actual elapsed time* on the curve rather than
assumed from a bar count. A curve with 250 observations that spans two years is
not a 250-observation-per-year series, and scaling its per-observation
volatility by sqrt(252) silently overstates annual risk. Passing a
``TradingCalendar`` additionally lets the caller cross-check the implied rate
against the exchange's real session count.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from math import inf, sqrt
from typing import Protocol

from axel.data.ml.calendars import TRADING_DAYS_PER_YEAR, TradingCalendar
from axel.util.stats import elapsed_years, implied_periods_per_year, mean, sample_std

TRADING_DAYS = TRADING_DAYS_PER_YEAR


class HasPnl(Protocol):
    pnl: float
    r_multiple: float


def _values(curve: Sequence[tuple[datetime, float]]) -> list[float]:
    return [value for _, value in curve]


def _timestamps(curve: Sequence[tuple[datetime, float]]) -> list[datetime]:
    return [stamp for stamp, _ in curve]


def curve_years(curve: Sequence[tuple[datetime, float]]) -> float:
    """Elapsed years between the first and last point of the curve."""
    stamps = _timestamps(curve)
    if len(stamps) < 2:
        return 0.0
    return elapsed_years(stamps[0].timestamp(), stamps[-1].timestamp())


def observations_per_year(
    curve: Sequence[tuple[datetime, float]],
    *,
    periods_per_year: float | None = None,
    calendar: TradingCalendar | None = None,
) -> float:
    """
    Periods-per-year to annualise with.

    Precedence: an explicit ``periods_per_year`` wins (needed for intraday data
    and for reproducing older results); otherwise the count is implied from the
    curve's real elapsed time. ``calendar`` is advisory: when supplied, it
    reports how many sessions the exchange actually had, so a caller can detect
    a series whose density disagrees with the calendar.
    """
    if periods_per_year is not None:
        return float(periods_per_year)
    periods = len(curve) - 1
    years = curve_years(curve)
    return implied_periods_per_year(periods, years, float(TRADING_DAYS))


def expected_sessions(
    curve: Sequence[tuple[datetime, float]], calendar: TradingCalendar
) -> int:
    """Sessions the exchange held across the curve's span (gap diagnostic)."""
    stamps = _timestamps(curve)
    if len(stamps) < 2:
        return 0
    return calendar.sessions_inclusive(stamps[0].date(), stamps[-1].date())


def session_coverage(
    curve: Sequence[tuple[datetime, float]], calendar: TradingCalendar
) -> float:
    """Fraction of expected sessions actually present in the curve (0.0-1.0)."""
    stamps = _timestamps(curve)
    if len(stamps) < 2:
        return 0.0
    expected = calendar.sessions_inclusive(stamps[0].date(), stamps[-1].date())
    if expected == 0:
        return 0.0
    return len(curve) / expected


def equity_returns(curve: Sequence[tuple[datetime, float]]) -> list[float]:
    values = _values(curve)
    return [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] != 0
    ]


def total_return(curve: Sequence[tuple[datetime, float]]) -> float:
    values = _values(curve)
    if len(values) < 2 or values[0] == 0:
        return 0.0
    return values[-1] / values[0] - 1.0


def cagr(
    curve: Sequence[tuple[datetime, float]], *, periods_per_year: float | None = None
) -> float:
    """
    Compound annual growth rate.

    Uses elapsed calendar time by default. With ``periods_per_year`` set, the
    legacy count-based exponent is retained for explicit reproducibility.
    """
    values = _values(curve)
    periods = len(values) - 1
    if periods <= 0 or values[0] <= 0 or values[-1] <= 0:
        return 0.0
    if periods_per_year is None:
        years = curve_years(curve)
        if years <= 0:
            return 0.0
        return (values[-1] / values[0]) ** (1.0 / years) - 1.0
    return (values[-1] / values[0]) ** (periods_per_year / periods) - 1.0


def volatility(
    curve: Sequence[tuple[datetime, float]], *, periods_per_year: float | None = None
) -> float:
    scale = observations_per_year(curve, periods_per_year=periods_per_year)
    return sample_std(equity_returns(curve)) * sqrt(scale)


def sharpe(
    curve: Sequence[tuple[datetime, float]],
    *,
    risk_free: float = 0.0,
    periods_per_year: float | None = None,
) -> float:
    series_returns = equity_returns(curve)
    deviation = sample_std(series_returns)
    if deviation == 0:
        return 0.0
    scale = observations_per_year(curve, periods_per_year=periods_per_year)
    excess = mean(series_returns) - risk_free / scale
    return excess / deviation * sqrt(scale)


def sortino(
    curve: Sequence[tuple[datetime, float]],
    *,
    target: float = 0.0,
    periods_per_year: float | None = None,
) -> float:
    series_returns = equity_returns(curve)
    downside = [value for value in series_returns if value < target]
    if not downside:
        return 0.0
    downside_deviation = (sum((value - target) ** 2 for value in downside) / len(downside)) ** 0.5
    if downside_deviation == 0:
        return 0.0
    scale = observations_per_year(curve, periods_per_year=periods_per_year)
    excess = mean(series_returns) - target
    return excess / downside_deviation * sqrt(scale)


def max_drawdown(curve: Sequence[tuple[datetime, float]]) -> float:
    """Worst peak-to-trough decline as a negative fraction."""
    values = _values(curve)
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def hit_rate(trades: Sequence[HasPnl]) -> float:
    if not trades:
        return 0.0
    return sum(1 for trade in trades if trade.pnl > 0) / len(trades)


def profit_factor(trades: Sequence[HasPnl]) -> float:
    gains = sum(trade.pnl for trade in trades if trade.pnl > 0)
    losses = -sum(trade.pnl for trade in trades if trade.pnl < 0)
    if losses == 0:
        return inf if gains > 0 else 0.0
    return gains / losses


def expectancy(trades: Sequence[HasPnl]) -> float:
    """Mean R-multiple across closed trades."""
    if not trades:
        return 0.0
    return mean([trade.r_multiple for trade in trades])


def summarize(
    curve: Sequence[tuple[datetime, float]],
    trades: Sequence[HasPnl],
    *,
    periods_per_year: float | None = None,
    calendar: TradingCalendar | None = None,
) -> dict[str, float]:
    result = {
        "total_return": total_return(curve),
        "cagr": cagr(curve, periods_per_year=periods_per_year),
        "volatility": volatility(curve, periods_per_year=periods_per_year),
        "sharpe": sharpe(curve, periods_per_year=periods_per_year),
        "sortino": sortino(curve, periods_per_year=periods_per_year),
        "max_drawdown": max_drawdown(curve),
        "trades": float(len(trades)),
        "hit_rate": hit_rate(trades),
        "profit_factor": profit_factor(trades),
        "expectancy_r": expectancy(trades),
        "elapsed_years": curve_years(curve),
        "observations_per_year": observations_per_year(
            curve, periods_per_year=periods_per_year
        ),
    }
    if calendar is not None:
        result["session_coverage"] = session_coverage(curve, calendar)
        result["expected_sessions"] = float(expected_sessions(curve, calendar))
    return result


__all__ = [
    "TRADING_DAYS",
    "HasPnl",
    "cagr",
    "curve_years",
    "equity_returns",
    "expectancy",
    "expected_sessions",
    "hit_rate",
    "max_drawdown",
    "observations_per_year",
    "profit_factor",
    "session_coverage",
    "sharpe",
    "sortino",
    "summarize",
    "total_return",
    "volatility",
]