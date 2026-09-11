"""Regression coverage: rate-based tax validation (validate_voucher) must
never run against a fabricated 0% rate for a missing-rate voucher.

Bug: mappings.normalized_vouchers already correctly sets item["gst_rate"] =
"0" and item["rate_available"] = False for a missing-rate line (see
test_missing_rate_fallback_ledgers.py) -- but validate_voucher's rate-based
CGST/SGST/IGST comparison read that "0" gst_rate literally, computed an
expected tax of 0, and then flagged the real (nonzero) source CGST/SGST as
"differs from the rate-based validation amount (0.00)". A missing rate is
never 0% (see canonical_invoice.py / mappings.py); this file locks in that
the rate-based comparison itself is skipped entirely for a missing-rate
voucher, and left completely unchanged for a real-rate voucher.
"""
from django.test import SimpleTestCase

from gst_tally.tally.validators import validate_voucher

GSTIN = "33AJJPD4912E1ZQ"


def _voucher(taxable, cgst, sgst, igst="0", cess="0", gst_rate="0", rate_available=False,
             transaction_type="INTRA-STATE", invoice_total=None):
    return {
        "invoice_number": "10/25-26", "invoice_date": "2025-04-01",
        "party": {"name": "ABC Traders", "gstin": GSTIN},
        "items": [{"taxable_value": taxable, "gst_rate": gst_rate, "rate_available": rate_available}],
        "transaction_type": transaction_type,
        "taxable_total": taxable, "cgst": cgst, "sgst": sgst, "igst": igst, "cess": cess,
        "other_charges": "0", "source_type": "uploaded",
        "invoice_total": invoice_total if invoice_total is not None else str(
            float(taxable) + float(cgst) + float(sgst) + float(igst) + float(cess)),
    }


class MissingRateSkipsRateBasedValidationTests(SimpleTestCase):
    def test_acceptance_case_invoice_10_25_26_produces_no_fake_mismatch_warnings(self):
        voucher = _voucher("3024.53", "136.25", "136.25")
        result = validate_voucher(voucher)

        self.assertTrue(result["critical_valid"])
        self.assertEqual(result["review_reasons"], [])
        self.assertNotIn("Source CGST differs from the rate-based validation amount (0.00); source value was preserved.",
                          result["review_reasons"])
        self.assertNotIn("Source SGST differs from the rate-based validation amount (0.00); source value was preserved.",
                          result["review_reasons"])

    def test_no_review_reason_mentions_rate_based_validation_at_all(self):
        voucher = _voucher("3024.53", "136.25", "136.25")
        result = validate_voucher(voucher)
        self.assertFalse(any("rate-based validation amount" in reason for reason in result["review_reasons"]))

    def test_missing_rate_igst_voucher_also_skips_rate_based_comparison(self):
        voucher = _voucher("9000.00", "0", "0", igst="450.00", transaction_type="INTER-STATE")
        result = validate_voucher(voucher)
        self.assertFalse(any("rate-based validation amount" in reason for reason in result["review_reasons"]))

    def test_transaction_type_consistency_checks_still_run_when_rate_is_missing(self):
        # These are about component-type-vs-transaction-type, not rate-vs-amount
        # -- they must stay active regardless of rate availability.
        voucher = _voucher("1000.00", "0", "0", igst="50.00", transaction_type="INTRA-STATE")
        result = validate_voucher(voucher)
        self.assertIn("Source IGST conflicts with the intra-state place-of-supply validation.", result["review_reasons"])

    def test_mixed_rate_availability_within_one_invoice_is_conservatively_skipped(self):
        voucher = _voucher("3024.53", "136.25", "136.25")
        voucher["items"].append({"taxable_value": "500", "gst_rate": "18", "rate_available": True})
        result = validate_voucher(voucher)
        self.assertFalse(any("rate-based validation amount" in reason for reason in result["review_reasons"]))


class RateAvailablePathIsUnchangedTests(SimpleTestCase):
    """Every existing rate-wise validation behaviour must be untouched --
    the rate-based CGST/SGST/IGST-vs-source comparison itself still runs
    exactly as before; only where its result surfaces changed (see
    TaxValidationDiagnosticTests below): a genuinely small drift is now a
    pure tax_validation diagnostic, never review_reasons/reason text, and
    source amounts are always preserved either way."""

    def test_rate_based_drift_is_never_a_review_reason_regardless_of_size(self):
        # A rate-based tax difference is PURE diagnostic information,
        # regardless of size -- it must never produce review_reasons/reason
        # text, and (per the follow-up fix) it never blocks the voucher
        # either. Only tax_validation.diagnostic_mismatch reflects it, for
        # display purposes only.
        voucher = _voucher("1000.00", "0", "0", gst_rate="18", rate_available=True)
        result = validate_voucher(voucher)
        self.assertFalse(any("rate-based validation amount" in reason for reason in result["review_reasons"]))
        self.assertFalse(any("differs" in reason for reason in result["review_reasons"]))

    def test_matching_rate_based_tax_produces_no_mismatch_warning(self):
        voucher = _voucher("1000.00", "90.00", "90.00", gst_rate="18", rate_available=True)
        result = validate_voucher(voucher)
        self.assertFalse(any("rate-based validation amount" in reason for reason in result["review_reasons"]))

    def test_items_without_an_explicit_rate_available_key_default_to_available(self):
        # Backward compatibility: any existing caller/fixture that never set
        # rate_available at all (the field didn't exist before this fix)
        # must keep getting full rate-based validation (i.e. rate_available
        # stays True), exactly as before -- verified via tax_validation now
        # instead of review_reasons text (see SEPARATE MESSAGE TYPES).
        voucher = _voucher("1000.00", "0", "0", gst_rate="18")
        del voucher["items"][0]["rate_available"]
        result = validate_voucher(voucher)
        self.assertTrue(result["tax_validation"]["rate_available"])
        self.assertEqual(result["tax_validation"]["calculated_cgst"], "90.00")

    def test_expected_cgst_sgst_igst_still_returned_for_rate_available_voucher(self):
        voucher = _voucher("1000.00", "90.00", "90.00", gst_rate="18", rate_available=True)
        result = validate_voucher(voucher)
        self.assertEqual(result["expected_cgst"], "90.00")
        self.assertEqual(result["expected_sgst"], "90.00")
        self.assertEqual(result["expected_igst"], "0")


class TaxValidationDiagnosticTests(SimpleTestCase):
    """Rate-based tax comparison is exposed as a clean, separate
    tax_validation diagnostic -- never mixed into review_reasons/reason,
    source amounts are always reported as preserved, and
    diagnostic_mismatch is purely informational: it never gates eligibility
    (that decision belongs entirely to service.prepare()'s SOURCE-total-vs-
    SOURCE-invoice-total round-off check, unrelated to this)."""

    def test_large_rate_drift_is_flagged_diagnostic_but_source_is_preserved(self):
        voucher = _voucher("1000.00", "0", "0", gst_rate="18", rate_available=True)
        result = validate_voucher(voucher)
        tax_validation = result["tax_validation"]
        self.assertTrue(tax_validation["rate_available"])
        self.assertEqual(tax_validation["source_cgst"], "0.00")
        self.assertEqual(tax_validation["calculated_cgst"], "90.00")
        self.assertEqual(tax_validation["source_sgst"], "0.00")
        self.assertEqual(tax_validation["calculated_sgst"], "90.00")
        self.assertTrue(tax_validation["source_preserved"])
        # 90.00 vs 0.00 is a full 18% miss, not a paise-level drift -- still
        # flagged for display (diagnostic_mismatch=True) but this alone must
        # never affect eligibility (see
        # test_voucher_validation_message_separation.py's
        # DiagnosticMismatchNeverBlocksEligibilityTests).
        self.assertTrue(tax_validation["diagnostic_mismatch"])

    def test_acceptance_case_2526_794_diagnostic_shows_the_drift(self):
        # Task spec's exact acceptance numbers: Calculated CGST=68.92,
        # Calculated SGST=68.91, source CGST/SGST=64.43 -- flagged as a
        # diagnostic (the numbers genuinely differ) but never blocking.
        voucher = _voucher("765.73", "68.50", "68.50", gst_rate="18", rate_available=True)
        result = validate_voucher(voucher)
        tax_validation = result["tax_validation"]
        self.assertEqual(tax_validation["calculated_cgst"], "68.92")
        self.assertEqual(tax_validation["calculated_sgst"], "68.91")
        self.assertEqual(tax_validation["source_cgst"], "68.50")
        self.assertEqual(tax_validation["source_sgst"], "68.50")
        self.assertTrue(tax_validation["source_preserved"])
        self.assertFalse(any("Source " in reason and "differs" in reason for reason in result["review_reasons"]))

    def test_missing_rate_voucher_tax_validation_reports_rate_unavailable(self):
        voucher = _voucher("3024.53", "136.25", "136.25")  # rate_available=False (default)
        result = validate_voucher(voucher)
        tax_validation = result["tax_validation"]
        self.assertFalse(tax_validation["rate_available"])
        self.assertTrue(tax_validation["source_preserved"])
        self.assertFalse(tax_validation["diagnostic_mismatch"])
