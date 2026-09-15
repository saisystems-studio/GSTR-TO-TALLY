"""Posting-date rules for prior-period work.

Periods from GST portals vary (042026, 04/2026, Apr-2026), so comparison is
deliberately based on a parsed month/year and falls back to normal posting
when a period is not unambiguous.
"""
import re
from datetime import date


def _period(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 6:
        month, year = int(digits[:2]), int(digits[2:])
        if 1 <= month <= 12 and 2000 <= year <= 9999:
            return year, month
    return None


def posting_date_for(invoice_date, original_period, posting_period):
    """Return (voucher_date, is_carry_forward), safely including Dec->Jan."""
    source = _period(original_period) or (invoice_date.year, invoice_date.month)
    target = _period(posting_period)
    if not target or target <= source:
        return invoice_date, False
    return date(target[0], target[1], 1), True
