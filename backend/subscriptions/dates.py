"""Calendar-year date arithmetic for subscription periods.

Deliberately NOT `timedelta(days=365)` -- that silently drifts by a day
across a leap year (see spec: "Do NOT hardcode 365 days if that creates
leap-year date errors. Use calendar-year addition."). `date.replace(year=...)`
raises only for Feb 29 landing on a non-leap target year, which is the one
case handled explicitly below (clamped to Feb 28, the same convention every
mainstream calendar library uses for a 1-year add off a leap day).
"""


def add_one_year(value):
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + 1)
