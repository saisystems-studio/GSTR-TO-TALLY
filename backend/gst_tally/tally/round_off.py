"""Round Off, resolved through an explicit SCENARIO RESOLVER rather than one
formula for every voucher:

    scenario detection -> matching rule -> tolerance validation
        -> at most one Round Off posting -> the scenario is reported back

The two underlying rules are unchanged from before this resolver was added:

* **uploaded** (Excel / CSV / JSON invoices, any of GSTR-1 / GSTR-2A / GSTR-2B)
  *with* a genuine source Invoice Total: that total is authoritative. Round
  Off is only ever *detected* from it -- never recalculated -- and only
  within a fixed tolerance; a larger gap is a validation failure, not a
  rounding difference. (Scenario 1 / IMPORTED_*.)
* **manual**, OR an uploaded row with no genuine source Invoice Total at all
  (never inferred from ``source_type`` alone -- see
  :func:`has_source_invoice_total`): there is nothing to detect against, so
  Round Off is *calculated* fresh, to the nearest whole rupee. (Scenario 2 /
  GENERATED_*.)

Both rules read only the already-grouped invoice-level totals (every GST-rate
row / source line summed first, by mappings.normalized_vouchers before this
module ever runs -- see the ``multi_line`` detection below), so the same code
serves GSTR-1, GSTR-2A and GSTR-2B alike, and Round Off is always
invoice-level, never per rate row or per source line (Scenario 3: one invoice
never produces more than one Round Off posting, regardless of how many rows
or GST-rate lines it was assembled from).

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

# Scenario labels (task spec's ROUND OFF SOURCE / SCENARIO RESOLVER section) --
# purely additive/descriptive: they report which rule and grouping resolved
# each voucher, they never change the round_off/final_voucher_total/status
# values the two rules above already compute.
SCENARIO_IMPORTED_SINGLE = "IMPORTED_SINGLE"
SCENARIO_IMPORTED_MULTI_LINE = "IMPORTED_MULTI_LINE"
SCENARIO_GENERATED_SINGLE = "GENERATED_SINGLE"
SCENARIO_GENERATED_MULTI_LINE = "GENERATED_MULTI_LINE"
SCENARIO_ZERO = "ZERO"
SCENARIO_TOTAL_MISMATCH = "TOTAL_MISMATCH"


def voucher_source_type(voucher):
    """The explicit uploaded/manual flag. Never inferred from UI state."""
    return MANUAL if str(voucher.get("source_type") or "").strip().casefold() == MANUAL else UPLOADED


def has_source_invoice_total(voucher):
    """Whether a genuine source Invoice Total is present on this voucher --
    checked on the raw value, before any numeric conversion, so a missing/
    blank total is never conflated with a genuinely-present zero (task spec's
    SOURCE TOTAL DETECTION section: "0 and missing are different"). Deciding
    the scenario from ``source_type`` alone would wrongly treat an uploaded
    row that happens to carry no Invoice Total at all as a large mismatch
    instead of correctly falling back to the nearest-rupee rule."""
    value = voucher.get("invoice_total")
    return value is not None and str(value).strip() != ""


def is_multi_line_invoice(voucher):
    """Whether this voucher was assembled from more than one source row /
    GST-rate line (mappings.normalized_vouchers already groups every row for
    one invoice before this module runs -- this only labels that grouping
    for the scenario resolver/UI, it never re-groups anything itself)."""
    return len(voucher.get("items") or []) > 1


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
    ``round_off``, ``final_voucher_total``, ``round_off_source``,
    ``round_off_scenario``, ``status`` and ``reason`` -- the exact Voucher
    Preview columns.
    """
    source_type = voucher_source_type(voucher)
    multi_line = is_multi_line_invoice(voucher)
    generated_scenario = SCENARIO_GENERATED_MULTI_LINE if multi_line else SCENARIO_GENERATED_SINGLE
    total = component_total(voucher)
    if source_type == MANUAL or not has_source_invoice_total(voucher):
        final_total = nearest_rupee(total)
        round_off = money(final_total - total)
        # source_type is reported as-is (not forced to MANUAL): an
        # "uploaded" row can also reach this branch when it genuinely has no
        # Invoice Total to detect against, and validate_voucher's own
        # upfront numeric check independently blocks that case as Invalid
        # Source Data -- this branch only ever governs the Round Off numbers
        # themselves, never the voucher's own source/upload identity.
        return {"source_type": source_type, "component_total": str(total), "source_invoice_value": "",
                "difference": "", "round_off": str(round_off), "final_voucher_total": str(final_total),
                "round_off_source": ROUND_OFF_SOURCE_CALCULATED if round_off != ZERO else ROUND_OFF_SOURCE_NONE,
                "round_off_scenario": generated_scenario,
                "status": "Ready", "reason": ""}

    source_invoice_value = money(voucher.get("invoice_total"))
    difference = money(source_invoice_value - total)
    source_round_off = money(voucher.get("source_round_off"))
    manual_correction = voucher.get("round_off_corrected", False)
    if manual_correction:
        final_total = money(total + source_round_off)
        matched = final_total == source_invoice_value
        return {"source_type": UPLOADED, "component_total": str(total),
                "source_invoice_value": str(source_invoice_value), "difference": str(difference),
                "suggested_round_off": str(difference), "round_off": str(source_round_off),
                "final_voucher_total": str(final_total), "round_off_source": ROUND_OFF_SOURCE_DETECTED,
                "round_off_scenario": (SCENARIO_IMPORTED_MULTI_LINE if multi_line else SCENARIO_IMPORTED_SINGLE) if matched else SCENARIO_TOTAL_MISMATCH,
                "status": "Ready" if matched else "Review Required",
                "reason": "" if matched else "Entered Round Off does not reconcile the invoice total."}
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
    if status == "Review Required":
        scenario = SCENARIO_TOTAL_MISMATCH
    elif round_off == ZERO:
        scenario = SCENARIO_ZERO
    else:
        scenario = SCENARIO_IMPORTED_MULTI_LINE if multi_line else SCENARIO_IMPORTED_SINGLE
    return {"source_type": UPLOADED, "component_total": str(total), "source_invoice_value": str(source_invoice_value),
            "difference": str(difference),
            "suggested_round_off": str(difference if abs(difference) <= tolerance else ZERO),
            "round_off": str(round_off),
            "final_voucher_total": str(money(total + round_off)),
            "round_off_source": round_off_source, "round_off_scenario": scenario, "status": status, "reason": reason}
