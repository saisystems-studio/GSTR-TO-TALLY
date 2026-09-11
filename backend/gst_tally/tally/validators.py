from decimal import Decimal, ROUND_HALF_UP
from datetime import date
import re

ZERO = Decimal("0")
PAISE = Decimal("0.01")
INVOICE_ROUNDING_TOLERANCE = Decimal("1.00")
# For imported/uploaded source data, a component-level (CGST/SGST/IGST)
# rate-based tax difference is PURE DIAGNOSTIC information -- it never gates
# voucher eligibility on its own (see tax_validation.diagnostic_mismatch
# below). Only used to decide whether the difference is worth flagging as
# "diagnostic_mismatch" at all (a few paise of rounding noise isn't); it must
# never be confused with a blocking condition. The only thing that can
# genuinely block a voucher is the SOURCE component total failing to
# reconcile with the SOURCE Invoice Total beyond the existing round-off
# tolerance (see round_off.py/resolve_round_off, untouched by this).
TAX_DIAGNOSTIC_DIFFERENCE_THRESHOLD = INVOICE_ROUNDING_TOLERANCE

# Complete GST state/UT code map, per the GST portal's official list. GSTIN's
# first two digits are the authority for state resolution; nothing here is
# specific to Tamil Nadu, and no company/party is ever assumed to be TN.
STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}

GSTIN_PATTERN = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]")

def valid_gstin(value):
    return bool(GSTIN_PATTERN.fullmatch(str(value or "").strip().upper()))

def dec(value):
    try: return Decimal(str(value or 0))
    except Exception: return ZERO

def money(value): return dec(value).quantize(PAISE, rounding=ROUND_HALF_UP)
def numeric(value):
    try: Decimal(str(value)); return True
    except (TypeError, ValueError, ArithmeticError): return False

def state_name(value):
    cleaned = str(value or "").strip()
    return STATE_CODES.get(cleaned.zfill(2), cleaned)

def transaction_type(company_state, place_of_supply, party_state="", party_gstin=""):
    destination = state_name(place_of_supply) or state_name(party_state) or state_name(str(party_gstin)[:2])
    origin = state_name(company_state)
    return ("INTRA-STATE" if origin and destination and origin.casefold() == destination.casefold() else "INTER-STATE"), destination

def validate_voucher(voucher, tolerance=PAISE):
    errors, review_reasons = [], []
    if not voucher.get("invoice_number"): errors.append("Invoice number is required")
    if not voucher.get("invoice_date"): errors.append("Invoice Date is missing or invalid in the uploaded file.")
    if voucher.get("invoice_date"):
        try: date.fromisoformat(str(voucher["invoice_date"]))
        except ValueError: errors.append("Invoice Date is missing or invalid in the uploaded file.")
    if not voucher.get("party", {}).get("name"): errors.append("Party name is required")
    gstin = str(voucher.get("party", {}).get("gstin") or "").strip().upper()
    if not valid_gstin(gstin): errors.append("Customer GSTIN is invalid")
    for field, label in (("taxable_total", "Taxable value"), ("invoice_total", "Invoice total")):
        if not numeric(voucher.get(field)): errors.append(f"{label} is invalid")
    if not voucher.get("items"): errors.append("At least one invoice line is required")
    for index, item in enumerate(voucher.get("items", []), 1):
        if dec(item.get("gst_rate")) < 0: errors.append(f"Line {index}: GST rate is invalid")
        if dec(item.get("taxable_value")) == 0: errors.append(f"Line {index}: taxable value is required")
    # Deferred import: round_off.py imports helpers from this module at load time,
    # so importing it back at module level here would be circular.
    from .round_off import resolve_round_off
    round_off_result = resolve_round_off(voucher)
    component_total = dec(round_off_result["component_total"])
    # Legacy contract (see difference_definition below): difference = calculated_total
    # - source_invoice_value. resolve_round_off reports the opposite sign
    # (source - component); "final_voucher_total" is always the authoritative total
    # in both branches (the source Invoice Value for uploaded, the nearest-rupee
    # total for manual), so flipping against it here keeps every existing caller's
    # sign unchanged while covering both source types uniformly.
    difference = money(component_total - dec(round_off_result["source_invoice_value"] or round_off_result["final_voucher_total"]))
    cgst, sgst, igst = money(voucher.get("cgst")), money(voucher.get("sgst")), money(voucher.get("igst"))
    if igst and (cgst or sgst):
        errors.append("Both intra-state and inter-state GST components are present; voucher cannot be mapped safely.")
        review_reasons.append("Both intra-state and inter-state GST components are present.")
    elif bool(cgst) != bool(sgst): review_reasons.append("Source GST breakup is incomplete/inconsistent.")
    if voucher.get("transaction_type") == "INTRA-STATE" and igst:
        review_reasons.append("Source IGST conflicts with the intra-state place-of-supply validation.")
    if voucher.get("transaction_type") == "INTER-STATE" and (cgst or sgst):
        review_reasons.append("Source CGST/SGST conflicts with the inter-state place-of-supply validation.")
    # Rate-based tax comparison only makes sense when a real GST rate exists
    # to calculate an expected tax from -- a missing rate is never treated as
    # 0% (see mappings.normalized_vouchers/canonical_invoice.py), so
    # comparing real source CGST/SGST/IGST against an "expected" amount
    # derived from a fabricated 0% rate would always produce a false
    # mismatch. A voucher is only rate-available when every line item is
    # (mixed availability within one invoice is treated conservatively as
    # unavailable, matching the missing-rate fallback's own per-invoice
    # ledger selection).
    items = voucher.get("items", [])
    rate_available = bool(items) and all(item.get("rate_available", True) for item in items)
    tax_tolerance = max(dec(tolerance), Decimal("0.02"))
    # Rate-based tax comparison is PURE DIAGNOSTIC information, full stop --
    # for imported/historical source data, source CGST/SGST/IGST is always
    # authoritative (SOURCE TAX PRESERVATION): it is never overwritten by the
    # recalculated amount, never mixed into `review_reasons`/`reason`/any
    # error_code, and -- regardless of how large the difference is --
    # NEVER blocks the voucher or affects critical_valid/review_required on
    # its own. A source-vs-rate difference beyond
    # TAX_DIAGNOSTIC_DIFFERENCE_THRESHOLD only flags `diagnostic_mismatch`
    # for display purposes; it carries no eligibility consequence. The only
    # thing that can genuinely block an imported voucher is its SOURCE
    # component total failing to reconcile with its SOURCE Invoice Total
    # beyond the existing round-off tolerance (round_off.py, untouched).
    if rate_available:
        expected_gst = sum((dec(item.get("taxable_value")) * dec(item.get("gst_rate")) / Decimal("100") for item in items), ZERO)
        if voucher.get("transaction_type") == "INTRA-STATE":
            expected_cgst = money(expected_gst / 2); expected_sgst = money(expected_gst - expected_cgst); expected_igst = ZERO
        else:
            expected_cgst = expected_sgst = ZERO; expected_igst = money(expected_gst)
        component_diffs = {field: abs(money(voucher.get(field)) - expected)
                            for field, expected in (("cgst", expected_cgst), ("sgst", expected_sgst), ("igst", expected_igst))}
        diagnostic_mismatch = any(diff > TAX_DIAGNOSTIC_DIFFERENCE_THRESHOLD for diff in component_diffs.values())
        tax_validation = {
            "rate_available": True,
            "source_cgst": str(cgst), "calculated_cgst": str(expected_cgst),
            "source_sgst": str(sgst), "calculated_sgst": str(expected_sgst),
            "source_igst": str(igst), "calculated_igst": str(expected_igst),
            "source_preserved": True, "diagnostic_mismatch": diagnostic_mismatch,
        }
    else:
        # Missing-rate fallback: the source CGST/SGST/IGST/Cess amounts are
        # trusted as-is (see mappings.py/master_builder.py) -- there is
        # nothing to validate them against, and reverse-calculating a GST
        # percentage from these amounts is explicitly disallowed.
        expected_cgst = expected_sgst = expected_igst = ZERO
        tax_validation = {"rate_available": False, "source_preserved": True, "diagnostic_mismatch": False}
    review_reasons = list(dict.fromkeys(review_reasons))
    # A valid explicit source Round Off is accepted even when its magnitude is
    # larger than the auto-detection tolerance; it has independently reconciled
    # the invoice equation within paise precision.
    within_rounding_tolerance = round_off_result["status"] == "Ready"
    diagnostics = {
        "invoice_number": str(voucher.get("invoice_number") or ""),
        "gstin": gstin, "taxable_total": str(money(voucher.get("taxable_total"))),
        "cgst_total": str(cgst), "sgst_total": str(sgst), "igst_total": str(igst),
        "cess_total": str(money(voucher.get("cess"))), "other_charges_total": str(money(voucher.get("other_charges"))),
        "round_off": round_off_result["round_off"], "round_off_source": round_off_result["round_off_source"],
        "calculated_invoice_total": str(component_total),
        "source_invoice_value": str(money(voucher.get("invoice_total"))), "difference": str(difference),
    }
    return {"valid": not errors, "critical_valid": not errors, "review_required": bool(review_reasons),
            "errors": errors, "review_reasons": review_reasons, "calculated_total": str(component_total), "difference": str(difference),
            "difference_definition": "calculated_total - source_invoice_value",
            "component_total": str(component_total), "source_type": round_off_result["source_type"],
            "rounding_adjustment": round_off_result["round_off"], "within_rounding_tolerance": within_rounding_tolerance,
            "round_off_source": round_off_result["round_off_source"], "final_voucher_total": round_off_result["final_voucher_total"],
            "round_off_scenario": round_off_result.get("round_off_scenario", ""),
            "round_off_status": round_off_result["status"], "round_off_reason": round_off_result["reason"],
            "suggested_round_off": round_off_result.get("suggested_round_off", round_off_result["round_off"]),
            "expected_cgst": str(expected_cgst), "expected_sgst": str(expected_sgst), "expected_igst": str(expected_igst),
            "tax_validation": tax_validation,
            "diagnostics": diagnostics}
