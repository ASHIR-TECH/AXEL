"""
Trading-calendar session math.

Two distinct jobs, both pure functions of a date:

1. ``is_trading_day`` / ``next_trading_day`` drive the backtester's execution
   clock, so a target decided on Friday fills at the next *session* open rather
   than the next calendar day.
2. ``sessions_between`` / ``annual_sessions`` count sessions, so annualisation
   can be derived from the calendar instead of assumed.

The US equity holiday set is generated from published rules (fixed dates with
Saturday/Sunday observance, nth-weekday floats, and Good Friday from the
anonymous Gregorian Easter algorithm) rather than being a hand-maintained
lookup table, so it stays correct for any year without edits.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

TRADING_DAYS_PER_YEAR = 252
_WEEKEND = (5, 6)


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The ``nth`` ``weekday`` (Mon=0) of a month, e.g. 3rd Monday of January."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` (Mon=0) of a month, e.g. last Monday of May."""
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def easter_sunday(year: int) -> date:
    """Anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def good_friday(year: int) -> date:
    return easter_sunday(year) - timedelta(days=2)


def observed(day: date) -> date:
    """Shift Saturday holidays to Friday and Sunday holidays to Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_equity_holidays(start_year: int = 1970, end_year: int = 2100) -> tuple[date, ...]:
    """NYSE/Nasdaq full-closure holidays for a year range, sorted and unique."""
    days: set[date] = set()
    # One year either side, so a holiday whose observance spills across the year
    # boundary (New Year 2022 on a Saturday -> closure Friday 2021-12-31) is still
    # present when the caller asked for 2021.
    for year in range(start_year - 1, end_year + 2):
        days.add(observed(date(year, 1, 1)))  # New Year's Day
        days.add(nth_weekday(year, 1, 0, 3))  # MLK Jr Day
        days.add(nth_weekday(year, 2, 0, 3))  # Washington's Birthday
        days.add(good_friday(year))
        days.add(last_weekday(year, 5, 0))  # Memorial Day
        if year >= 2022:  # Juneteenth became a market holiday in 2022
            days.add(observed(date(year, 6, 19)))
        days.add(observed(date(year, 7, 4)))  # Independence Day
        days.add(nth_weekday(year, 9, 0, 1))  # Labor Day
        days.add(nth_weekday(year, 11, 3, 4))  # Thanksgiving
        days.add(observed(date(year, 12, 25)))  # Christmas
    return tuple(sorted(day for day in days if start_year <= day.year <= end_year))


class TradingCalendar:
    """Session arithmetic over weekends plus an explicit holiday set."""

    def __init__(self, holidays: Iterable[date] = (), *, name: str = "custom") -> None:
        self._holidays = frozenset(holidays)
        self.name = name

    @property
    def holidays(self) -> frozenset[date]:
        return self._holidays

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() not in _WEEKEND and day not in self._holidays

    def trading_days(self, start: date, end: date) -> list[date]:
        if end < start:
            raise ValueError("end must not precede start")
        days: list[date] = []
        current = start
        while current <= end:
            if self.is_trading_day(current):
                days.append(current)
            current += timedelta(days=1)
        return days

    def next_trading_day(self, day: date) -> date:
        candidate = day + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def previous_trading_day(self, day: date) -> date:
        candidate = day - timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def sessions_inclusive(self, start: date, end: date) -> int:
        """Sessions in the closed interval ``[start, end]``."""
        if end < start:
            return 0
        return len(self.trading_days(start, end))

    def sessions_between(self, start: date, end: date) -> int:
        """
        Sessions in the half-open interval ``(start, end]``.

        This is the "sessions elapsed" measure used for execution delay: a
        decision made on a Friday filled on the next Monday has a delay of 1.
        """
        if end <= start:
            return 0
        return len(self.trading_days(start + timedelta(days=1), end))

    def annual_sessions(self, year: int) -> int:
        return self.sessions_inclusive(date(year, 1, 1), date(year, 12, 31))

    def sessions_per_year(self, start: date, end: date) -> float:
        """
        Sessions per calendar year over a span.

        Uses elapsed time rather than a hardcoded 252 so that a 6-year span
        containing 6 short years annualises correctly.
        """
        years = (end - start).days / 365.25
        if years <= 0:
            return float(TRADING_DAYS_PER_YEAR)
        return self.sessions_inclusive(start, end) / years

    def expected_observations_per_year(
        self, *, observations: int, start: date, end: date, fallback: float = TRADING_DAYS_PER_YEAR
    ) -> float:
        """
        Observations-per-year implied by the calendar span.

        ``observations`` is how many return observations you actually hold over
        ``[start, end]``. If a series skips sessions, this is below 252, which is
        exactly the factor that must *not* be ignored when annualising.
        """
        years = (end - start).days / 365.25
        if observations <= 0 or years <= 0:
            return fallback
        return observations / years


DEFAULT_CALENDAR = TradingCalendar(name="weekdays-only")
US_EQUITY_CALENDAR = TradingCalendar(
    us_equity_holidays(1970, 2100), name="us-equity"
)

# Session rules per venue. A calendar is a property of the exchange, not of the
# data format, so this is keyed by the venue string carried on every BarRecord.
# Unlisted venues resolve to weekdays-only, which never invents a holiday it
# cannot justify but also never skips a real one -- callers on a known venue
# should pass the resolved calendar explicitly.
VENUE_CALENDARS: dict[str, TradingCalendar] = {
    "alpaca-iex": US_EQUITY_CALENDAR,
    "us-equity": US_EQUITY_CALENDAR,
}


def calendar_for_venue(venue: str) -> TradingCalendar:
    """Session calendar for a venue, falling back to weekdays-only."""
    return VENUE_CALENDARS.get(venue, DEFAULT_CALENDAR)


def calendar_for_bars(bars: Iterable[object]) -> TradingCalendar | None:
    """
    The strictest calendar covering every venue present in ``bars``.

    Returns ``None`` when no venue has known session rules. Guessing a calendar
    for an unfamiliar venue would silently drop bars or invent holidays, so an
    unknown venue stays uncalendared and the caller decides. When several known
    venues disagree, the most restrictive wins: skipping a bar another venue
    would have traded is the safe direction, since it never manufactures a fill
    that could not have happened.
    """
    venues = {
        getattr(bar, "venue", None) for bar in bars if getattr(bar, "venue", None)
    }
    resolved = [
        calendar for calendar in (VENUE_CALENDARS.get(venue) for venue in sorted(venues))
        if calendar is not None
    ]
    if not resolved:
        return None
    return min(resolved, key=lambda calendar: len(calendar.holidays))


__all__ = [
    "DEFAULT_CALENDAR",
    "TRADING_DAYS_PER_YEAR",
    "US_EQUITY_CALENDAR",
    "VENUE_CALENDARS",
    "TradingCalendar",
    "calendar_for_bars",
    "calendar_for_venue",
    "easter_sunday",
    "good_friday",
    "last_weekday",
    "nth_weekday",
    "observed",
    "us_equity_holidays",
]