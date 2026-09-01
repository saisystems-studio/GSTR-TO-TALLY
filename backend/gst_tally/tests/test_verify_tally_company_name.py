"""Regression test for the Company Verification bug: verification must compare
the Tally company name the user actually typed into the Verify Company box
against the currently open Tally company -- not the (often blank/differently
formatted) company name parsed out of the uploaded GST return file.
"""
from django.test import SimpleTestCase

from gst_tally.tally.odbc import verify_tally_company

GSTIN = "33AFHPM6103Q1Z8"
CONNECTED_STATUS = {
    "read_connected": True, "odbc_connected": True, "company_detected": True,
    "company_name": "SRI MAHALAKSHMI TRADERS,", "company": "SRI MAHALAKSHMI TRADERS,",
    "company_gstin": GSTIN, "gstin": GSTIN,
    "company_state": "Tamil Nadu", "failure_type": "",
    "financial_year_from": "2025-04-01", "financial_year_to": "2026-03-31",
    "financial_year": "01 Apr 2025 - 31 Mar 2026",
}


class VerifyTallyCompanyNameTests(SimpleTestCase):
    def test_matches_the_entered_company_name_even_when_the_source_file_name_is_blank(self):
        source_company = {"selected_tally_company": "Sri Mahalakshmi Traders", "company_name": ""}
        result = verify_tally_company(GSTIN, CONNECTED_STATUS, source_company)
        self.assertTrue(result["company_verified"])
        self.assertEqual(result["verification"], "MATCHED")

    def test_leading_trailing_spaces_case_and_trailing_comma_are_harmless(self):
        source_company = {"selected_tally_company": "  sri mahalakshmi traders  "}
        result = verify_tally_company(GSTIN, CONNECTED_STATUS, source_company)
        self.assertTrue(result["company_verified"])

    def test_a_name_mismatch_alone_does_not_block_verification_when_gstin_matches(self):
        # GSTIN is the authoritative identity: an exact GSTIN match verifies the
        # company even when the entered name looks nothing like the Tally name.
        source_company = {"selected_tally_company": "A Totally Different Company"}
        result = verify_tally_company(GSTIN, CONNECTED_STATUS, source_company)
        self.assertTrue(result["company_verified"])
        self.assertEqual(result["verification"], "MATCHED")
        self.assertFalse(result["company_name_match"])

    def test_falls_back_to_source_file_company_name_when_nothing_was_entered(self):
        source_company = {"company_name": "Sri Mahalakshmi Traders"}
        result = verify_tally_company(GSTIN, CONNECTED_STATUS, source_company)
        self.assertTrue(result["company_verified"])

    def test_verified_company_result_includes_active_tally_financial_period(self):
        source_company = {"selected_tally_company": "Sri Mahalakshmi Traders"}
        result = verify_tally_company(GSTIN, CONNECTED_STATUS, source_company)

        self.assertEqual(result["company_read"]["financial_year_from"], "2025-04-01")
        self.assertEqual(result["company_read"]["financial_year_to"], "2026-03-31")
        self.assertEqual(result["company_read"]["financial_year"], "01 Apr 2025 - 31 Mar 2026")
