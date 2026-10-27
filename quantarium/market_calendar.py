"""Offline NYSE full-day closures, for checking hand-maintained price files.

Rules cover regular holidays from 1998 (when Martin Luther King Jr. Day was
added) plus known unscheduled closures. Future unscheduled closures cannot be
known in advance, so a file that skips such a day will look complete only
after this list is updated. Early closes are trading sessions and are not
distinguished.
"""

from __future__ import annotations

from datetime import date, timedelta

FIRST_YEAR = 1998
# Full-day closures outside the regular rules.
SPECIAL_CLOSURES = frozenset({
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),   # President Reagan's funeral
    date(2007, 1, 2),    # President Ford's funeral
    date(2012, 10, 29), date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),   # President G. H. W. Bush's funeral
    date(2025, 1, 9),    # President Carter's funeral
})


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    day = date(year, month, 1)
    day += timedelta(days=(weekday - day.weekday()) % 7)
    return day + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    day = date(year, month + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return date(year, month, (h + l - 7 * m + 33 * month + 19) % 32)


def _observed(day: date) -> date | None:
    """Saturday holidays close the Friday before, Sunday ones the Monday after."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def holidays(year: int) -> set[date]:
    if year < FIRST_YEAR:
        raise ValueError(f"The offline calendar starts in {FIRST_YEAR}")
    days = {
        _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),   # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),     # Memorial Day
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }
    # New Year's Day on a Saturday is not moved into the previous year.
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:
        days.add(_observed(new_year))
    for month, day in ((7, 4), (12, 25)) + (((6, 19),) if year >= 2022 else ()):
        days.add(_observed(date(year, month, day)))
    return days


def sessions(start: date, through: date) -> set[date]:
    """NYSE trading dates between ``start`` and ``through``, inclusive."""
    if start > through:
        return set()
    closed: set[date] = set()
    for year in range(start.year, through.year + 1):
        closed |= holidays(year)
    result = set()
    day = start
    while day <= through:
        if day.weekday() < 5 and day not in closed and day not in SPECIAL_CLOSURES:
            result.add(day)
        day += timedelta(days=1)
    return result
