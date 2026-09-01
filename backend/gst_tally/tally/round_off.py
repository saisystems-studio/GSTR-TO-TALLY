"""Two distinct Round Off rules, chosen by an explicit ``source_type`` flag.

* **uploaded** (Excel / CSV / JSON invoices, any of GSTR-1 / GSTR-2A / GSTR-2B):
  the source ``Invoice Value`` is the authoritative final amount. Round Off is
  only ever *detected* from it -- never recalculated -- and only within a fixed
  tolerance; a larger gap is a validation failure, not a rounding difference.
* **manual** (a new invoice with no authoritative uploaded total): there is
  nothing to detect against, so Round Off is *calculated* fresh, to the
  nearest whole rupee.

Both rules read only the already-grouped invoice-level totals (every GST-rate
row summed first), so the same code serves GSTR-1, GSTR-2A and GSTR-2B alike,
and Round Off is always invoice-level, never per rate row.

:func:`resolve_round_off` is the single source of truth: the Round Off it
returns is exactly what Voucher Preview shows and exactly what gets sent to
Tally. Neither caller recomputes it differently.
"""
from decimal import Decimal, ROUND_HALF_UP

from .validators import ZERO, PAISE, INVOICE_ROUNDING_TOLERANCE, dec, money

RUPEE = Decimal("1")

UPLOADED = "uploaded"
MANUAL = "manual"

ROUND_OFF_SOURCE_NONE = "None"
ROUND_OFF_SOURCE_DETECTED = "Source"
ROUND_OFF_SOURCE_CALCULATED = "Calculated"


def voucher_source_type(voucher):
    """The explicit uploaded/manual flag. Never inferred from UI state."""
    return MANUAL if str(voucher.get("source_type") or "").strip().casefold() == MANUAL else UPLOADED


def component_total(voucher):
    """Taxable + CGST + SGST + IGST + Cess, once at invoice level."""
    return (money(voucher.get("taxable_total")) + money(voucher.get("cgst")) + money(voucher.get("sgst"))
            + money(voucher.get("igst")) + money(voucher.get("cess")))


def nearest_rupee(value):
    """HALF_UP to the nearest whole rupee.

    Never Python's built-in ``round()``: it uses banker's rounding, which
    rounds an exact .50 to the nearest *even* rupee instead of always up.
    """
    return money(dec(value).quantize(RUPEE, rounding=ROUND_HALF_UP))


def resolve_round_off(voucher, tolerance=INVOICE_ROUNDING_TOLERANCE):
    """Round Off breakdown for one invoice (rate rows already grouped).

    Returns ``component_total``, ``source_invoice_value``, ``difference``,
    ``round_off``, ``final_voucher_total``, ``round_off_source``, ``status``
    and ``reason`` -- the exact Voucher Preview columns.
    """
    source_type = voucher_source_type(voucher)
    total = component_total(voucher)
    if source_type == MANUAL:
        final_total = nearest_rupee(total)
        round_off = money(final_total - total)
        return {"source_type": MANUAL, "component_total": str(total), "source_invoice_value": "",
                "difference": "", "round_off": str(round_off), "final_voucher_total": str(final_total),
                "round_off_source": ROUND_OFF_SOURCE_CALCULATED if round_off != ZERO else ROUND_OFF_SOURCE_NONE,
                "status": "Ready", "reason": ""}

    source_invoice_value = money(voucher.get("invoice_total"))
    difference = money(source_invoice_value - total)
    source_round_off = money(voucher.get("source_round_off"))
    explicit_round_off_valid = (bool(source_round_off) and abs(source_round_off) <= tolerance
                                and abs(money(total + source_round_off - source_invoice_value)) <= PAISE)
    if explicit_round_off_valid:
        round_off, round_off_source, status, reason = source_round_off, ROUND_OFF_SOURCE_DETECTED, "Ready", ""
    elif difference == ZERO:
        round_off, round_off_source, status, reason = money(ZERO), ROUND_OFF_SOURCE_NONE, "Ready", ""
    elif abs(difference) <= tolerance:
        round_off, round_off_source, status, reason = difference, ROUND_OFF_SOURCE_DETECTED, "Ready", ""
    else:
        round_off, round_off_source, status = money(ZERO), ROUND_OFF_SOURCE_NONE, "Review Required"
        reason = f"Invoice total mismatch. Difference ₹{abs(difference)} is too large to be treated as Round Off."
    return {"source_type": UPLOADED, "component_total": str(total), "source_invoice_value": str(source_invoice_value),
            "difference": str(difference),
            "suggested_round_off": str(difference if abs(difference) <= tolerance else ZERO),
            "round_off": str(round_off),
            "final_voucher_total": str(money(total + round_off)),
            "round_off_source": round_off_source, "status": status, "reason": reason}
