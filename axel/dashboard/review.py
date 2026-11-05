"""
Read-only performance review projections for the dashboard.

Adapted from the quarterly review view-model in stock-portfolio-manager
(https://github.com/Contractor-x/stock-portfolio-manager,
src/pages/Review/stockOperationReviewViewModel.ts). The TypeScript version
computes QTD / previous-quarter / YTD / 1Y date ranges; this module keeps that
period logic and adds deterministic per-section and per-strategy summaries.

Everything here is a projection over supplied records. It opens no database
session, writes nothing, and cannot submit an order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

PeriodPreset = Literal["QTD", "PREV_QUARTER", "YTD", "1Y", "CUSTOM"]
_PRESETS = ("QTD", "PREV_QUARTER", "YTD", "1Y", "CUSTOM")


@dataclass(frozen=True)
class DateRange:
    """Inclusive calendar date range."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start must be on or before end")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


@dataclass(frozen=True)
class PerformanceRecord:
    """One closed trade, as supplied by a read-only query."""

    section: str
    strategy_id: str
    closed_at: date
    pnl: float
    notional: float = 0.0

    def __post_init__(self) -> None:
        if not self.section.strip():
            raise ValueError("section must not be empty")
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if self.notional < 0.0:
            raise ValueError("notional must be non-negative")


@dataclass(frozen=True)
class PerformanceSummary:
    """Deterministic performance statistics for one grouping."""

    label: str
    trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    total_pnl: float
    average_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    return_on_notional: float | None
    max_drawdown: float
    best_trade: float | None
    worst_trade: float | None


@dataclass(frozen=True)
class ReviewReport:
    """Quarterly review projection: overall plus section and strategy cuts."""

    period: DateRange
    overall: PerformanceSummary
    by_section: tuple[PerformanceSummary, ...]
    by_strategy: tuple[PerformanceSummary, ...]
    records_in_period: int
    excluded_records: int


def _shift_months(value: date, months: int) -> date:
    month_index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    # Clamp to the last valid day of the target month.
    day = value.day
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1


def _quarter_start(value: date) -> date:
    month = ((value.month - 1) // 3) * 3 + 1
    return date(value.year, month, 1)


def _prior_quarter_end(value: date) -> date:
    return _quarter_start(value) - timedelta(days=1)


def _prior_quarter_start(value: date) -> date:
    return _quarter_start(_prior_quarter_end(value))


def review_date_range(
    preset: PeriodPreset,
    *,
    today: date | None = None,
    start: date | None = None,
    end: date | None = None,
) -> DateRange:
    """Resolve a review preset into an inclusive calendar range."""
    if preset not in _PRESETS:
        raise ValueError(f"unknown period preset: {preset!r}")
    current = today or date.today()

    if preset == "CUSTOM":
        if start is None or end is None:
            raise ValueError("CUSTOM requires both start and end")
        return DateRange(start, end)
    if preset == "QTD":
        return DateRange(_quarter_start(current), current)
    if preset == "PREV_QUARTER":
        return DateRange(_prior_quarter_start(current), _prior_quarter_end(current))
    if preset == "YTD":
        return DateRange(date(current.year, 1, 1), current)

    # 1Y: prior anniversary plus one day, clamped for leap days.
    prior = _shift_months(current, -12) + timedelta(days=1)
    return DateRange(prior, current)


def _summary(label: str, records: Sequence[PerformanceRecord]) -> PerformanceSummary:
    ordered = sorted(records, key=lambda record: (record.closed_at, record.strategy_id))
    pnls = [record.pnl for record in ordered]
    wins = sum(1 for pnl in pnls if pnl > 0.0)
    losses = sum(1 for pnl in pnls if pnl < 0.0)
    breakeven = len(pnls) - wins - losses
    total = sum(pnls)
    gross_profit = sum(pnl for pnl in pnls if pnl > 0.0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0.0))
    notional = sum(record.notional for record in ordered)

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    trades = len(pnls)
    return PerformanceSummary(
        label=label,
        trades=trades,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate=wins / trades if trades else 0.0,
        total_pnl=total,
        average_pnl=total / trades if trades else 0.0,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=gross_profit / gross_loss if gross_loss > 0.0 else None,
        return_on_notional=total / notional if notional > 0.0 else None,
        max_drawdown=max_drawdown,
        best_trade=max(pnls) if pnls else None,
        worst_trade=min(pnls) if pnls else None,
    )


def _grouped(
    records: Sequence[PerformanceRecord],
    key: str,
) -> tuple[PerformanceSummary, ...]:
    groups: dict[str, list[PerformanceRecord]] = {}
    for record in records:
        label = record.section if key == "section" else record.strategy_id
        groups.setdefault(label, []).append(record)
    return tuple(_summary(label, groups[label]) for label in sorted(groups))


def build_review_report(
    records: Sequence[PerformanceRecord],
    *,
    preset: PeriodPreset = "QTD",
    today: date | None = None,
    start: date | None = None,
    end: date | None = None,
) -> ReviewReport:
    """Filter records to the period, then summarise overall/by section/by strategy."""
    period = review_date_range(preset, today=today, start=start, end=end)
    selected = [record for record in records if period.contains(record.closed_at)]

    return ReviewReport(
        period=period,
        overall=_summary("ALL", selected),
        by_section=_grouped(selected, "section"),
        by_strategy=_grouped(selected, "strategy"),
        records_in_period=len(selected),
        excluded_records=len(records) - len(selected),
    )
