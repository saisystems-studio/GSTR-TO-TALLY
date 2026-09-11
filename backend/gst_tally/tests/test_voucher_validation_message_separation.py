"""Regression coverage: Voucher Preview validation-message classification
must keep three independent concepts separate --

  1. voucher readiness (status/reason/error_code)
  2. tax validation diagnostics (tax_validation: source vs calculated)
  3. Sandbox taxpayer-enrichment status (sandbox_warning)

Bug 1 (fixed first): a Ready voucher with a real GST rate whose calculated
CGST/SGST differed from the source showed "Source CGST differs from the
rate-based validation amount (...)" as if it were the voucher's Reason,
with the unrelated Sandbox enrichment code
("SANDBOX_PARTY_DETAILS_UNAVAILABLE") displayed alongside it as if it were
the voucher's Code.

Bug 2 (this file's current state, fixed second): the first fix went too
far the other way -- it introduced a "material_mismatch" threshold that
turned a large-enough rate-vs-source component difference into its own
blocking condition (status=Review Required, error_code=TAX_AMOUNT_MISMATCH,
import_eligible=false), which incorrectly blocked a large fraction of a
real batch (219 -> 178 eligible) purely because recalculated GST differed
from perfectly valid imported historical source data. Rate-based tax
comparison is now PURE diagnostic information (tax_validation.
diagnostic_mismatch), regardless of magnitude, and never affects
eligibility on its own. The only thing that can block an eligible-party
imported voucher is its SOURCE component total failing to reconcile with
its SOURCE Invoice Total beyond the existing round-off tolerance -- a
completely separate, untouched code path (genuine_mismatch).

These tests exercise the same status-decision logic service.prepare() uses
(traced from validate_voucher()/party_eligibility(), since prepare() itself
needs a live DB unavailable in this environment) to lock in the corrected
behaviour without depending on the database.
"""
from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from gst_tally.tally.validators import money, validate_voucher
from gst_tally.services.party_lookup import party_eligibility

GSTIN = "33AJJPD4912E1ZQ"


def _voucher(taxable, cgst, sgst, gst_rate="18", igst="0", invoice_total=None, name_source="Taxpayer",
             invoice_number="2526/794"):
    invoice_total = invoice_total if invoice_total is not None else str(
        Decimal(taxable) + Decimal(cgst) + Decimal(sgst) + Decimal(igst))
    return {
        "invoice_number": invoice_number, "invoice_date": "2025-04-01",
        "party": {"name": "VASANTHAM AGENCIES", "gstin": GSTIN, "name_source": name_source},
        "items": [{"taxable_value": taxable, "gst_rate": gst_rate, "rate_available": True}],
        "transaction_type": "INTRA-STATE",
        "taxable_total": taxable, "cgst": cgst, "sgst": sgst, "igst": igst, "cess": "0",
        "other_charges": "0", "invoice_total": invoice_total, "source_type": "uploaded",
    }


def _decide_status(voucher, validation, eligibility):
    """Mirrors service.prepare()'s exact status-decision logic (the
    corrected version) so this can be tested without a live database. Only
    the SOURCE-component-vs-SOURCE-invoice-total round-off check
    (genuine_mismatch) can block an eligible-party voucher -- the rate-based
    tax_validation diagnostic never participates in this decision."""
    if not validation["critical_valid"]:
        return {"status": "Invalid Source Data", "reason": "; ".join(validation["errors"])}
    if not eligibility["tally_ready"]:
        return {"status": "Needs Attention", "reason": eligibility.get("reason", "")}
    fallback = voucher["party"].get("name_source") == "GSTIN"
    source_difference = money(validation["difference"]) != 0
    genuine_mismatch = voucher.get("invoice_value_conflict") or (source_difference and not validation["within_rounding_tolerance"])
    warnings = list(validation.get("review_reasons", []))
    if genuine_mismatch:
        status, reason = "Review Required", "; ".join(warnings) if warnings else ""
    else:
        status = "Ready with GSTIN Fallback" if fallback else "Ready"
        reason = "; ".join(warnings) if warnings else "GSTIN is used as the party ledger name" if fallback else ""
    error_code = ""  # genuine_mismatch carries no error_code of its own (pre-existing, untouched behaviour)
    sandbox_warning = ({"code": eligibility.get("warning_code", ""), "message": eligibility.get("warning", "")}
                        if status.startswith("Ready") and eligibility.get("warning_code") else None)
    return {"status": status, "reason": reason, "error_code": error_code, "warnings": warnings,
            "tax_validation": validation.get("tax_validation", {}), "sandbox_warning": sandbox_warning}


class AcceptanceCase2526794Tests(SimpleTestCase):
    """The task spec's exact bug report and EXPECTED RESULT section."""

    @override_settings(GST_LOOKUP_PROVIDER="sandbox")
    def test_ready_voucher_with_rate_drift_and_sandbox_failure(self):
        voucher = _voucher("765.73", "68.50", "68.50")  # calculated CGST=68.92, SGST=68.91
        validation = validate_voucher(voucher)
        eligibility = party_eligibility(None, voucher["party"]["name"], voucher["party"]["gstin"], "Tamil Nadu")
        result = _decide_status(voucher, validation, eligibility)

        self.assertEqual(result["status"], "Ready")
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["error_code"], "")
        self.assertEqual(result["warnings"], [])
        self.assertNotIn("Source CGST differs", str(result))
        self.assertNotIn("Source SGST differs", str(result))

    @override_settings(GST_LOOKUP_PROVIDER="sandbox")
    def test_sandbox_warning_is_separate_and_never_becomes_the_voucher_code(self):
        voucher = _voucher("765.73", "68.50", "68.50")
        validation = validate_voucher(voucher)
        eligibility = party_eligibility(None, voucher["party"]["name"], voucher["party"]["gstin"], "Tamil Nadu")
        result = _decide_status(voucher, validation, eligibility)

        self.assertEqual(result["sandbox_warning"]["code"], "SANDBOX_PARTY_DETAILS_UNAVAILABLE")
        # The critical assertion: this code must never leak into error_code.
        self.assertEqual(result["error_code"], "")
        self.assertNotEqual(result["error_code"], "SANDBOX_PARTY_DETAILS_UNAVAILABLE")

    @override_settings(GST_LOOKUP_PROVIDER="sandbox")
    def test_tax_validation_diagnostic_shows_calculated_and_source_preserved(self):
        voucher = _voucher("765.73", "68.50", "68.50")
        validation = validate_voucher(voucher)
        tax_validation = validation["tax_validation"]

        self.assertTrue(tax_validation["rate_available"])
        self.assertEqual(tax_validation["calculated_cgst"], "68.92")
        self.assertEqual(tax_validation["calculated_sgst"], "68.91")
        self.assertEqual(tax_validation["source_cgst"], "68.50")
        self.assertEqual(tax_validation["source_sgst"], "68.50")
        self.assertTrue(tax_validation["source_preserved"])

    @override_settings(GST_LOOKUP_PROVIDER="sandbox")
    def test_source_amounts_are_never_overwritten_by_calculated_amounts(self):
        voucher = _voucher("765.73", "68.50", "68.50")
        # Source tax amounts on the voucher dict itself are untouched by
        # validate_voucher() -- it never mutates voucher["cgst"]/["sgst"].
        validate_voucher(voucher)
        self.assertEqual(voucher["cgst"], "68.50")
        self.assertEqual(voucher["sgst"], "68.50")


class DiagnosticMismatchNeverBlocksEligibilityTests(SimpleTestCase):
    """The core correction: no matter how large the rate-vs-source
    difference is, it is diagnostic-only and must never make the voucher
    Review Required / ineligible on its own."""

    @override_settings(GST_LOOKUP_PROVIDER="sandbox")
    def test_large_rate_based_difference_still_leaves_voucher_ready(self):
        voucher = _voucher("1000.00", "0", "0")  # expected 90/90, source 0/0 -- a full 18% miss
        validation = validate_voucher(voucher)
        eligibility = party_eligibility(None, voucher["party"]["name"], voucher["party"]["gstin"], "Tamil Nadu")
        result = _decide_status(voucher, validation, eligibility)

        self.assertTrue(result["tax_validation"]["diagnostic_mismatch"])
        self.assertEqual(result["status"], "Ready")
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["error_code"], "")
        self.assertNotIn("TAX_AMOUNT_MISMATCH", str(result))

    @override_settings(GST_LOOKUP_PROVIDER="sandbox")
    def test_source_amounts_still_never_overwritten_even_for_a_large_diagnostic_difference(self):
        voucher = _voucher("1000.00", "0", "0")
        validate_voucher(voucher)
        self.assertEqual(voucher["cgst"], "0")
        self.assertEqual(voucher["sgst"], "0")


class GenuineSourceTotalMismatchStillBlocksTests(SimpleTestCase):
    """Task spec's "IMPORTANT REAL MISMATCH CASE" (Invoice J117): when the
    SOURCE components themselves don't reconcile with the SOURCE Invoice
    Total (independent of any GST rate), the voucher must still correctly
    become Review Required -- this pre-existing path (genuine_mismatch) is
    completely unrelated to, and unaffected by, the tax_validation fix."""

    @override_settings(GST_LOOKUP_PROVIDER="sandbox")
    def test_invoice_j117_source_total_mismatch_remains_review_required(self):
        # Taxable 22056.26 + CGST 1912.77 + SGST 1912.77 = 25881.80 source
        # component total, but the source Invoice Total is 25661.00 -- a
        # genuine 220.80 gap in the SOURCE data itself, no GST rate involved.
        voucher = _voucher("22056.26", "1912.77", "1912.77", gst_rate="0",
                            invoice_total="25661.00", invoice_number="J117")
        voucher["items"][0]["rate_available"] = False
        validation = validate_voucher(voucher)
        eligibility = party_eligibility(None, voucher["party"]["name"], voucher["party"]["gstin"], "Tamil Nadu")
        result = _decide_status(voucher, validation, eligibility)

        self.assertEqual(result["status"], "Review Required")
        self.assertFalse(validation["within_rounding_tolerance"])


class RateUnavailablePathUnaffectedTests(SimpleTestCase):
    """Scenario B (missing rate) must keep working exactly as the prior fix
    established -- no rate-based comparison at all, never a diagnostic
    mismatch either."""

    def test_missing_rate_never_produces_a_diagnostic_mismatch(self):
        voucher = _voucher("3024.53", "136.25", "136.25")
        voucher["items"][0]["rate_available"] = False
        validation = validate_voucher(voucher)
        self.assertFalse(validation["tax_validation"]["rate_available"])
        self.assertFalse(validation["tax_validation"]["diagnostic_mismatch"])
        self.assertEqual(validation["review_reasons"], [])
