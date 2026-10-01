"""Execution-simulation primitives: costs, slippage, calendar, splits, metrics."""

from __future__ import annotations

from axel.data.ml.backtester import (
    Backtester,
    BacktestResult,
    DividendMode,
    ExitReason,
    Fill,
    TargetStrategy,
    Trade,
)
from axel.data.ml.calendars import (
    DEFAULT_CALENDAR,
    TRADING_DAYS_PER_YEAR,
    US_EQUITY_CALENDAR,
    TradingCalendar,
    us_equity_holidays,
)
from axel.data.ml.corporate_actions import (
    Delisting,
    Dividend,
    ListingRecord,
    PointInTimeUniverse,
    TerminalPricePolicy,
    apply_corporate_actions,
    apply_dividends,
)
from axel.data.ml.costs import ZERO_COSTS, CostModel
from axel.data.ml.metrics import (
    cagr,
    curve_years,
    equity_returns,
    expectancy,
    hit_rate,
    max_drawdown,
    observations_per_year,
    profit_factor,
    session_coverage,
    sharpe,
    sortino,
    summarize,
    total_return,
    volatility,
)
from axel.data.ml.risk_model import (
    FixedFractionalRiskModel,
    RiskModel,
    StopDistanceRiskModel,
    VolatilityRiskModel,
    realized_volatility,
)
from axel.data.ml.slippage import (
    NO_SLIPPAGE,
    FixedBpsSlippage,
    SlippageModel,
    VolumeImpactSlippage,
)
from axel.data.ml.splits import Split, apply_splits

__all__ = [
    "DEFAULT_CALENDAR",
    "NO_SLIPPAGE",
    "TRADING_DAYS_PER_YEAR",
    "US_EQUITY_CALENDAR",
    "ZERO_COSTS",
    "BacktestResult",
    "Backtester",
    "CostModel",
    "Delisting",
    "Dividend",
    "DividendMode",
    "ExitReason",
    "Fill",
    "FixedBpsSlippage",
    "FixedFractionalRiskModel",
    "ListingRecord",
    "PointInTimeUniverse",
    "RiskModel",
    "SlippageModel",
    "Split",
    "StopDistanceRiskModel",
    "TargetStrategy",
    "TerminalPricePolicy",
    "Trade",
    "TradingCalendar",
    "VolatilityRiskModel",
    "VolumeImpactSlippage",
    "apply_corporate_actions",
    "apply_dividends",
    "apply_splits",
    "cagr",
    "curve_years",
    "equity_returns",
    "expectancy",
    "hit_rate",
    "max_drawdown",
    "observations_per_year",
    "profit_factor",
    "realized_volatility",
    "session_coverage",
    "sharpe",
    "sortino",
    "summarize",
    "total_return",
    "us_equity_holidays",
    "volatility",
]
