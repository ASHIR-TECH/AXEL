"""Session-clock execution and calendar-time annualisation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from axel.data.ml.calendars import (
    DEFAULT_CALENDAR,
    TRADING_DAYS_PER_YEAR,
    US_EQUITY_CALENDAR,
    TradingCalendar,
    calendar_for_bars,
    calendar_for_venue,
    easter_sunday,
    good_friday,
    last_weekday,
    nth_weekday,
    observed,
    us_equity_holidays,
)
from axel.data.ml.metrics import (
    curve_years,
    observations_per_year,
    session_coverage,
    volatility,
)

CAL = US_EQUITY_CALENDAR


def _bar(symbol: str, event: date, *, venue: str = "alpaca-iex"):
    from axel.data.schemas import BarRecord, Provenance

    return BarRecord(
        symbol=symbol,
        venue=venue,
        event_time=datetime(event.year, event.month, event.day, tzinfo=UTC),
        available_at=datetime(event.year, event.month, event.day, tzinfo=UTC),
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1_000.0,
        adjustment_status="raw",
        provenance=Provenance(
            source="test", source_id=symbol, source_hash=symbol, ingestion_timestamp=datetime(2026, 1, 1, tzinfo=UTC)
        ),
    )


class TestHolidayGeneration:
    def test_new_year_observed_shift(self):
        # 2022-01-01 fell on a Saturday, so the closure was Friday 2021-12-31.
        # Asking for the 2021 calendar must still include it, hence the
        # one-year-wider generation window in us_equity_holidays.
        holidays_2021 = set(us_equity_holidays(2021, 2021))
        assert date(2021, 12, 31) in holidays_2021
        # 2021-01-01 was a Friday and a closure in its own right.
        assert date(2021, 1, 1) in holidays_2021
        assert date(2022, 1, 1) not in set(us_equity_holidays(2022, 2022))

    def test_independence_day_2026_observed_friday(self):
        # 2026-07-04 is a Saturday.
        assert observed(date(2026, 7, 4)) == date(2026, 7, 3)
        assert date(2026, 7, 3) in set(us_equity_holidays(2026, 2026))

    def test_known_2024_holidays(self):
        holidays = set(us_equity_holidays(2024, 2024))
        assert {date(2024, 1, 1), date(2024, 1, 15), date(2024, 3, 29),
                date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4),
                date(2024, 9, 2), date(2024, 11, 28), date(2024, 12, 25)} <= holidays

    def test_session_counts_match_exchange(self):
        # 2023 lost a session to the Jan 9 national day of mourning.
        assert CAL.annual_sessions(2024) == TRADING_DAYS_PER_YEAR
        assert CAL.annual_sessions(2025) == 251
        assert CAL.annual_sessions(2023) == 250

    def test_good_friday_matches_published_dates(self):
        assert good_friday(2024) == date(2024, 3, 29)
        assert good_friday(2025) == date(2025, 4, 18)
        assert good_friday(2026) == date(2026, 4, 3)

    def test_easter_is_a_sunday(self):
        for year in range(2020, 2035):
            assert easter_sunday(year).weekday() == 6

    def test_nth_and_last_weekday(self):
        assert nth_weekday(2026, 1, 0, 3) == date(2026, 1, 19)  # MLK
        assert last_weekday(2026, 5, 0) == date(2026, 5, 25)  # Memorial

    def test_juneteenth_not_a_holiday_before_2022(self):
        assert date(2021, 6, 18) not in set(us_equity_holidays(2021, 2021))
        assert date(2022, 6, 20) in set(us_equity_holidays(2022, 2022))  # Jun 19 = Sunday

    def test_holidays_only_contain_weekdays(self):
        for day in us_equity_holidays(2024, 2024):
            assert day.weekday() < 5


class TestSessionMath:
    def test_sessions_between_is_half_open(self):
        assert CAL.sessions_between(date(2026, 1, 5), date(2026, 1, 5)) == 0
        assert CAL.sessions_between(date(2026, 1, 5), date(2026, 1, 6)) == 1

    def test_weekend_spanning_counts_only_sessions(self):
        friday = date(2026, 10, 9)
        monday = date(2026, 10, 12)
        assert not CAL.is_trading_day(friday.weekday() and date(2026, 10, 10))
        assert CAL.is_trading_day(friday)
        assert CAL.is_trading_day(monday)
        assert CAL.sessions_between(friday, monday) == 1

    def test_holiday_span_skips_the_closure(self):
        # 2025-12-24 -> 2025-12-26 crosses Christmas.
        assert CAL.sessions_between(date(2025, 12, 24), date(2025, 12, 26)) == 1

    def test_next_and_previous_are_inverses(self):
        # Jul 3 2026 is the observed Independence Day closure.
        day = date(2026, 7, 2)
        assert CAL.next_trading_day(day) == date(2026, 7, 6)
        assert CAL.previous_trading_day(CAL.next_trading_day(day)) == day

    def test_sessions_per_year_uses_elapsed_time(self):
        rate = CAL.sessions_per_year(date(2020, 1, 1), date(2026, 1, 1))
        assert 250 <= rate <= 253

    def test_reversed_range_is_empty_not_negative(self):
        assert CAL.sessions_inclusive(date(2026, 1, 5), date(2026, 1, 1)) == 0
        assert CAL.sessions_between(date(2026, 1, 5), date(2026, 1, 1)) == 0

    def test_custom_holiday_overrides(self):
        cal = TradingCalendar(holidays=(date(2026, 3, 4),))
        assert not cal.is_trading_day(date(2026, 3, 4))
        assert cal.is_trading_day(date(2026, 3, 5))


class TestVenueResolution:
    def test_known_venue_resolves_to_its_calendar(self):
        assert calendar_for_venue("alpaca-iex") is US_EQUITY_CALENDAR

    def test_unknown_venue_falls_back_to_weekdays(self):
        assert calendar_for_venue("some-other-venue") is DEFAULT_CALENDAR

    def test_bars_infer_a_calendar_only_for_known_venues(self):
        equity = [_bar("AAA", date(2026, 7, 3), venue="alpaca-iex")]
        unknown = [_bar("BBB", date(2026, 7, 3), venue="synthetic")]
        assert calendar_for_bars(equity) is US_EQUITY_CALENDAR
        # Guessing rules for an unfamiliar venue would either drop real bars or
        # invent closures, so it stays None and the caller decides.
        assert calendar_for_bars(unknown) is None
        assert calendar_for_bars([*equity, *unknown]) is US_EQUITY_CALENDAR
        assert calendar_for_bars([]) is None


def _curve(values, start=datetime(2024, 1, 1, tzinfo=UTC), step=timedelta(days=1)):
    return [(start + step * index, value) for index, value in enumerate(values)]


class TestCalendarTimeAnnualisation:
    def test_dense_daily_curve_implies_365_not_252(self):
        curve = _curve([100.0] * 366)
        assert observations_per_year(curve) == pytest.approx(365.25, abs=0.5)

    def test_trading_session_curve_implies_252(self):
        days = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(366))
                if CAL.is_trading_day(d)]
        curve = [(datetime(d.year, d.month, d.day, tzinfo=UTC), 100.0 + i) for i, d in enumerate(days)]
        assert observations_per_year(curve) == pytest.approx(252, abs=2)

    def test_gappy_curve_annualises_below_252(self):
        """The bug this fixes: a 120-observation 2-year curve is 60/yr, not 252."""
        curve = _curve([100.0] * 121, step=timedelta(days=6))
        implied = observations_per_year(curve)
        assert implied == pytest.approx(60.8, abs=1)
        assert implied < 252

    def test_explicit_override_wins(self):
        curve = _curve([100.0] * 366)
        assert observations_per_year(curve, periods_per_year=252) == 252

    def test_volatility_scales_with_observed_density(self):
        dense = _curve([100.0 * (1.001**i) for i in range(366)])
        sparse = _curve([100.0 * (1.001**i) for i in range(121)], step=timedelta(days=6))
        # Same per-step return, but fewer steps per year -> lower annualised vol.
        assert volatility(sparse) < volatility(dense)

    def test_curve_years_uses_elapsed_time(self):
        curve = _curve([100.0, 110.0], step=timedelta(days=365))
        assert curve_years(curve) == pytest.approx(1.0, abs=0.01)

    def test_single_point_curve_has_no_elapsed_time(self):
        assert curve_years([(datetime(2024, 1, 1, tzinfo=UTC), 100.0)]) == 0.0

    def test_session_coverage_detects_gaps(self):
        days = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(366))
                if CAL.is_trading_day(d)]
        complete = [(datetime(d.year, d.month, d.day, tzinfo=UTC), 100.0) for d in days]
        half = complete[::2]
        assert session_coverage(complete, CAL) == pytest.approx(1.0)
        assert session_coverage(half, CAL) == pytest.approx(0.5, abs=0.02)