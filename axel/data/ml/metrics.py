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
from math import ceil, inf, sqrt
from typing import Protocol

from axel.data.ml.calendars import TRADING_DAYS_PER_YEAR, TradingCalendar
from axel.util.stats import (
    elapsed_years,
    implied_periods_per_year,
    mean,
    quantile,
    sample_std,
)
from axel.util.stats import (
    excess_kurtosis as sample_excess_kurtosis,
)
from axel.util.stats import (
    skewness as sample_skewness,
)

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
    if not series_returns:
        return 0.0
    # Standard Sortino: root-mean-square shortfall over the full sample, not
    # an average over only the downside observations.  Dividing by the number
    # of downside observations would overstate risk and disagree with
    # ``downside_deviation``.
    squared_shortfall = [min(value - target, 0.0) ** 2 for value in series_returns]
    downside_deviation = sqrt(sum(squared_shortfall) / len(squared_shortfall))
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


def average_win(trades: Sequence[HasPnl]) -> float:
    """Mean profit of winning trades (0.0 when there are none)."""
    return mean([trade.pnl for trade in trades if trade.pnl > 0])


def average_loss(trades: Sequence[HasPnl]) -> float:
    """Mean loss of losing trades as a positive magnitude (0.0 when none)."""
    return mean([-trade.pnl for trade in trades if trade.pnl < 0])


def payoff_ratio(trades: Sequence[HasPnl]) -> float:
    """Average win divided by average loss; 0.0 when either side is empty."""
    loss = average_loss(trades)
    if loss == 0:
        return 0.0
    return average_win(trades) / loss


def downside_deviation(
    curve: Sequence[tuple[datetime, float]],
    *,
    target: float = 0.0,
    periods_per_year: float | None = None,
) -> float:
    """Annualised root-mean-square return shortfall below ``target``.

    Only returns below the target contribute, so an equity curve that gains
    steadily but unevenly is not penalised for its upside. ``target`` is a
    per-period return, matching the units of ``equity_returns``.
    """
    series_returns = equity_returns(curve)
    if not series_returns:
        return 0.0
    squared = [min(value - target, 0.0) ** 2 for value in series_returns]
    rms = sqrt(sum(squared) / len(squared))
    scale = observations_per_year(curve, periods_per_year=periods_per_year)
    return rms * sqrt(scale)


def calmar(curve: Sequence[tuple[datetime, float]]) -> float:
    """CAGR divided by the absolute maximum drawdown (0.0 if no drawdown)."""
    drawdown = abs(max_drawdown(curve))
    if drawdown == 0:
        return 0.0
    return cagr(curve) / drawdown


def omega_ratio(
    curve: Sequence[tuple[datetime, float]],
    *,
    required_return: float = 0.0,
    periods_per_year: float | None = None,
) -> float:
    """Omega ratio: gains above a threshold over losses below it.

    ``required_return`` is annual; it is converted to a per-period threshold
    with the same annualisation basis as the rest of this module. Returns
    ``inf`` when there is upside and no downside, and 0.0 when neither exists.
    """
    series_returns = equity_returns(curve)
    if not series_returns:
        return 0.0
    scale = observations_per_year(curve, periods_per_year=periods_per_year)
    if scale <= 0:
        return 0.0
    threshold = (1.0 + required_return) ** (1.0 / scale) - 1.0
    excess = [value - threshold for value in series_returns]
    gains = sum(value for value in excess if value > 0)
    losses = -sum(value for value in excess if value < 0)
    if losses == 0:
        return inf if gains > 0 else 0.0
    return gains / losses


def tail_ratio(
    curve: Sequence[tuple[datetime, float]],
    *,
    upper: float = 0.95,
    lower: float = 0.05,
) -> float:
    """Absolute upper-tail return divided by the absolute lower-tail return.

    Values above 1 mean the right tail is larger than the left. The quantile
    probability convention matches ``axel.util.stats.quantile`` (type 7).
    """
    if not 0.0 < lower < upper < 1.0:
        raise ValueError("quantiles must satisfy 0 < lower < upper < 1")
    series_returns = equity_returns(curve)
    if not series_returns:
        return 0.0
    lower_value = quantile(series_returns, lower)
    if lower_value == 0:
        return 0.0
    return abs(quantile(series_returns, upper)) / abs(lower_value)


def return_skewness(curve: Sequence[tuple[datetime, float]]) -> float:
    """Adjusted sample skewness of the curve's per-period returns."""
    return sample_skewness(equity_returns(curve))


def return_excess_kurtosis(curve: Sequence[tuple[datetime, float]]) -> float:
    """Unbiased excess kurtosis of the curve's per-period returns (normal = 0)."""
    return sample_excess_kurtosis(equity_returns(curve))


def ulcer_index(curve: Sequence[tuple[datetime, float]]) -> float:
    """Root-mean-square percentage drawdown, in percentage points.

    Unlike maximum drawdown it accounts for depth *and* duration, so a curve
    that sits 10% underwater for a year scores worse than one that touches the
    same depth and recovers immediately.
    """
    values = _values(curve)
    if not values:
        return 0.0
    peak = values[0]
    squared: list[float] = []
    for value in values:
        peak = max(peak, value)
        drawdown_pct = (value / peak - 1.0) * 100.0 if peak > 0 else 0.0
        squared.append(drawdown_pct * drawdown_pct)
    return sqrt(sum(squared) / len(squared))


def recovery_factor(curve: Sequence[tuple[datetime, float]]) -> float:
    """Total return divided by the absolute maximum drawdown."""
    drawdown = abs(max_drawdown(curve))
    if drawdown == 0:
        return 0.0
    return total_return(curve) / drawdown


def value_at_risk(
    curve: Sequence[tuple[datetime, float]], *, confidence: float = 0.95
) -> float:
    """Historical one-period VaR as a positive loss fraction.

    ``confidence=0.95`` means "the loss the portfolio is expected to exceed on
    only 5% of periods". A non-negative number is returned so callers cannot
    accidentally add a loss to equity.
    """
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1.0)")
    series_returns = equity_returns(curve)
    if not series_returns:
        return 0.0
    losses = [-value for value in series_returns]
    return max(0.0, quantile(losses, confidence))


def conditional_value_at_risk(
    curve: Sequence[tuple[datetime, float]], *, confidence: float = 0.95
) -> float:
    """Empirical expected shortfall as a positive loss fraction.

    Uses the worst ``ceil((1 - confidence) * n)`` observations, matching the
    discrete-tail convention in ``axel.portfolio.risk``.
    """
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1.0)")
    series_returns = equity_returns(curve)
    if not series_returns:
        return 0.0
    ordered = sorted((-value for value in series_returns), reverse=True)
    tail_count = max(1, ceil((1.0 - confidence) * len(ordered)))
    tail = ordered[:tail_count]
    return max(0.0, sum(tail) / len(tail))


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
        "calmar": calmar(curve),
        "omega": omega_ratio(curve, periods_per_year=periods_per_year),
        "tail_ratio": tail_ratio(curve),
        "downside_deviation": downside_deviation(
            curve, periods_per_year=periods_per_year
        ),
        "ulcer_index": ulcer_index(curve),
        "recovery_factor": recovery_factor(curve),
        "var_95": value_at_risk(curve),
        "cvar_95": conditional_value_at_risk(curve),
        "skewness": return_skewness(curve),
        "excess_kurtosis": return_excess_kurtosis(curve),
        "trades": float(len(trades)),
        "hit_rate": hit_rate(trades),
        "profit_factor": profit_factor(trades),
        "average_win": average_win(trades),
        "average_loss": average_loss(trades),
        "payoff_ratio": payoff_ratio(trades),
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
    "average_loss",
    "average_win",
    "cagr",
    "calmar",
    "conditional_value_at_risk",
    "curve_years",
    "downside_deviation",
    "equity_returns",
    "expectancy",
    "expected_sessions",
    "hit_rate",
    "max_drawdown",
    "observations_per_year",
    "omega_ratio",
    "payoff_ratio",
    "profit_factor",
    "recovery_factor",
    "return_excess_kurtosis",
    "return_skewness",
    "session_coverage",
    "sharpe",
    "sortino",
    "summarize",
    "tail_ratio",
    "total_return",
    "ulcer_index",
    "value_at_risk",
    "volatility",
]