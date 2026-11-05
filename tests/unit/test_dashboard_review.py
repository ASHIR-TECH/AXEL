"""Tests for the read-only dashboard review projection."""

from datetime import date

import pytest

from axel.dashboard.review import (
    DateRange,
    PerformanceRecord,
    build_review_report,
    review_date_range,
)


def _record(
    section: str,
    strategy: str,
    closed_at: date,
    pnl: float,
    notional: float = 100.0,
) -> PerformanceRecord:
    return PerformanceRecord(
        section=section,
        strategy_id=strategy,
        closed_at=closed_at,
        pnl=pnl,
        notional=notional,
    )


def test_qtd_and_ytd_ranges() -> None:
    today = date(2026, 5, 15)

    assert review_date_range("QTD", today=today) == DateRange(date(2026, 4, 1), today)
    assert review_date_range("YTD", today=today) == DateRange(date(2026, 1, 1), today)


def test_previous_quarter_range() -> None:
    assert review_date_range("PREV_QUARTER", today=date(2026, 5, 15)) == DateRange(
        date(2026, 1, 1), date(2026, 3, 31)
    )
    assert review_date_range("PREV_QUARTER", today=date(2026, 1, 5)) == DateRange(
        date(2025, 10, 1), date(2025, 12, 31)
    )


def test_one_year_range_clamps_leap_day() -> None:
    assert review_date_range("1Y", today=date(2026, 5, 15)) == DateRange(
        date(2025, 5, 16), date(2026, 5, 15)
    )
    assert review_date_range("1Y", today=date(2024, 2, 29)) == DateRange(
        date(2023, 3, 1), date(2024, 2, 29)
    )


def test_custom_range_requires_both_dates() -> None:
    custom = review_date_range(
        "CUSTOM", start=date(2026, 1, 1), end=date(2026, 2, 1)
    )
    assert custom == DateRange(date(2026, 1, 1), date(2026, 2, 1))

    with pytest.raises(ValueError):
        review_date_range("CUSTOM", start=date(2026, 1, 1))
    with pytest.raises(ValueError):
        review_date_range("CUSTOM", end=date(2026, 2, 1))


def test_invalid_preset_and_range() -> None:
    with pytest.raises(ValueError):
        review_date_range("LAST_DECADE", today=date(2026, 1, 1))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DateRange(date(2026, 2, 1), date(2026, 1, 1))


def test_summary_arithmetic() -> None:
    records = [
        _record("stocks", "momentum", date(2026, 4, 2), 10.0),
        _record("stocks", "momentum", date(2026, 4, 3), -5.0),
        _record("stocks", "momentum", date(2026, 4, 4), 20.0),
        _record("stocks", "momentum", date(2026, 4, 5), 0.0),
    ]

    report = build_review_report(records, preset="QTD", today=date(2026, 5, 15))
    summary = report.overall

    assert summary.label == "ALL"
    assert summary.trades == 4
    assert summary.wins == 2
    assert summary.losses == 1
    assert summary.breakeven == 1
    assert summary.win_rate == pytest.approx(0.5)
    assert summary.total_pnl == pytest.approx(25.0)
    assert summary.average_pnl == pytest.approx(6.25)
    assert summary.gross_profit == pytest.approx(30.0)
    assert summary.gross_loss == pytest.approx(5.0)
    assert summary.profit_factor == pytest.approx(6.0)
    assert summary.return_on_notional == pytest.approx(25.0 / 400.0)
    assert summary.max_drawdown == pytest.approx(5.0)
    assert summary.best_trade == pytest.approx(20.0)
    assert summary.worst_trade == pytest.approx(-5.0)


def test_empty_period_has_zero_summary() -> None:
    report = build_review_report([], preset="QTD", today=date(2026, 5, 15))

    summary = report.overall
    assert summary.trades == 0
    assert summary.win_rate == 0.0
    assert summary.total_pnl == 0.0
    assert summary.average_pnl == 0.0
    assert summary.profit_factor is None
    assert summary.return_on_notional is None
    assert summary.best_trade is None
    assert summary.worst_trade is None
    assert report.by_section == ()
    assert report.by_strategy == ()


def test_no_losses_yields_no_profit_factor() -> None:
    report = build_review_report(
        [_record("stocks", "a", date(2026, 4, 2), 5.0)],
        preset="QTD",
        today=date(2026, 5, 15),
    )

    assert report.overall.profit_factor is None


def test_zero_notional_yields_no_return() -> None:
    report = build_review_report(
        [_record("stocks", "a", date(2026, 4, 2), 5.0, notional=0.0)],
        preset="QTD",
        today=date(2026, 5, 15),
    )

    assert report.overall.return_on_notional is None


def test_period_filtering_and_boundaries() -> None:
    records = [
        _record("stocks", "a", date(2026, 3, 31), 1.0),  # excluded from QTD
        _record("stocks", "a", date(2026, 4, 1), 2.0),  # first day included
        _record("stocks", "a", date(2026, 5, 15), 3.0),  # today included
        _record("stocks", "a", date(2026, 5, 16), 4.0),  # future, excluded
    ]

    report = build_review_report(records, preset="QTD", today=date(2026, 5, 15))

    assert report.records_in_period == 2
    assert report.excluded_records == 2
    assert report.overall.total_pnl == pytest.approx(5.0)


def test_section_and_strategy_cuts_are_sorted() -> None:
    records = [
        _record("stocks", "zeta", date(2026, 4, 1), 3.0),
        _record("crypto", "alpha", date(2026, 4, 2), 1.0),
        _record("crypto", "beta", date(2026, 4, 3), -2.0),
    ]

    report = build_review_report(records, preset="QTD", today=date(2026, 5, 15))

    assert [summary.label for summary in report.by_section] == ["crypto", "stocks"]
    assert [summary.label for summary in report.by_strategy] == ["alpha", "beta", "zeta"]
    assert report.by_section[0].total_pnl == pytest.approx(-1.0)
    assert report.by_strategy[0].total_pnl == pytest.approx(1.0)


def test_max_drawdown_uses_chronological_order() -> None:
    records = [
        _record("stocks", "a", date(2026, 4, 3), 10.0),
        _record("stocks", "a", date(2026, 4, 1), 10.0),
        _record("stocks", "a", date(2026, 4, 2), -15.0),
    ]

    report = build_review_report(records, preset="QTD", today=date(2026, 5, 15))

    # Chronological cumulative: 10, -5, 5 -> peak 10, trough -5 -> drawdown 15.
    assert report.overall.max_drawdown == pytest.approx(15.0)


def test_record_validation() -> None:
    with pytest.raises(ValueError):
        PerformanceRecord("", "a", date(2026, 1, 1), 1.0)
    with pytest.raises(ValueError):
        PerformanceRecord("stocks", "", date(2026, 1, 1), 1.0)
    with pytest.raises(ValueError):
        PerformanceRecord("stocks", "a", date(2026, 1, 1), 1.0, notional=-1.0)


def test_report_is_pure() -> None:
    records = [_record("stocks", "a", date(2026, 4, 1), 1.0)]
    snapshot = list(records)

    first = build_review_report(records, preset="QTD", today=date(2026, 5, 15))
    second = build_review_report(records, preset="QTD", today=date(2026, 5, 15))

    assert first == second
    assert records == snapshot
