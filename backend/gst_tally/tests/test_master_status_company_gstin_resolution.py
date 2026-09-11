"""Regression coverage for the Tally Masters screen "stale GSTIN mismatch"
bug: prepare_master_results()/_verify_company() must resolve the company
GSTIN the same way license verification does (services/company.py's
resolve_verified_company_gstin), never batch.company_gstin directly -- which
can still be blank even after Company Verification already succeeded and
persisted the real GSTIN to CompanyDetails.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from gst_tally.models import CompanyDetails, GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.service import prepare_master_results

GSTIN = "33AJJPD4912E1ZQ"
COMPANY = "VASANTHAM AGENCIES"


def tally_status(company_name=COMPANY, gstin=GSTIN):
    return {"odbc_connected": True, "read_connected": True, "company_detected": True,
            "company_open": True, "company_name": company_name, "company": company_name,
            "company_gstin": gstin, "gstin": gstin,
            "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
            "can_import": True, "message": "Tally company and GST registration detected."}


class MasterStatusCompanyGstinResolutionTests(TestCase):
    def _seed_invoice(self, batch):
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="2526/211", invoice_date=date(2025, 4, 7),
            customer_gstin=GSTIN, taxable_value=Decimal("1585.96"), tax_percent=Decimal("18"),
            cgst=Decimal("142.74"), sgst=Decimal("142.74"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1871.44"), place_of_supply="Tamil Nadu",
            source_line={"source_row_number": 2})
        GSTParty.objects.create(gstin=GSTIN, trade_name=COMPANY, state_name="Tamil Nadu")

    def test_reports_false_gstin_mismatch_when_batch_company_gstin_is_blank(self):
        """The exact reported bug, reproduced: batch.company_gstin was never
        populated, but Company Verification already succeeded and persisted
        the real GSTIN to CompanyDetails."""
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="", company_details={"selected_tally_company": COMPANY})
        CompanyDetails.objects.create(company_name=COMPANY.upper(), gstin=GSTIN,
                                      company_verified=True, tally_connected=True)
        self._seed_invoice(batch)

        with patch("gst_tally.tally.service.odbc_company_status", return_value=tally_status()):
            result = prepare_master_results(batch)

        self.assertTrue(result["company_verified"])
        self.assertEqual(result["verification_code"], "MATCHED")
        self.assertNotEqual(result["verification_code"], "GSTIN_MISMATCH")
        self.assertTrue(result["ready"])
        self.assertNotIn("does not match", result["message"])

    def test_master_rows_are_not_stuck_pending_once_company_is_verified(self):
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="", company_details={"selected_tally_company": COMPANY})
        CompanyDetails.objects.create(company_name=COMPANY.upper(), gstin=GSTIN,
                                      company_verified=True, tally_connected=True)
        self._seed_invoice(batch)

        with patch("gst_tally.tally.service.odbc_company_status", return_value=tally_status()):
            result = prepare_master_results(batch)

        self.assertTrue(result["masters"], "expected at least one required master row")
        self.assertTrue(all(row["status"] != "Pending" for row in result["masters"]))

    def test_can_continue_to_voucher_preview_reflects_ready_flag(self):
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="", company_details={"selected_tally_company": COMPANY})
        CompanyDetails.objects.create(company_name=COMPANY.upper(), gstin=GSTIN,
                                      company_verified=True, tally_connected=True)
        self._seed_invoice(batch)

        with patch("gst_tally.tally.service.odbc_company_status", return_value=tally_status()):
            result = prepare_master_results(batch)

        self.assertTrue(result["ready"])

    def test_genuine_gstin_mismatch_is_still_reported_when_tally_has_a_different_company(self):
        # A real mismatch must still be caught -- this fix must not make every
        # verification pass unconditionally.
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="", company_details={"selected_tally_company": COMPANY})
        CompanyDetails.objects.create(company_name=COMPANY.upper(), gstin=GSTIN,
                                      company_verified=True, tally_connected=True)
        self._seed_invoice(batch)

        with patch("gst_tally.tally.service.odbc_company_status",
                  return_value=tally_status(company_name="A DIFFERENT COMPANY", gstin="27AAAAA0000A1Z5")):
            result = prepare_master_results(batch)

        self.assertFalse(result["company_verified"])
        self.assertEqual(result["verification_code"], "GSTIN_MISMATCH")
        self.assertFalse(result["ready"])

    def test_falls_back_to_batch_company_gstin_when_no_companydetails_record_exists(self):
        # Backward compatibility: existing batches that already carry
        # company_gstin directly (no CompanyDetails record needed) still work.
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=GSTIN, company_details={"company_name": COMPANY, "gstin": GSTIN, "state": "Tamil Nadu"})
        self._seed_invoice(batch)

        with patch("gst_tally.tally.service.odbc_company_status", return_value=tally_status()):
            result = prepare_master_results(batch)

        self.assertTrue(result["company_verified"])
        self.assertEqual(result["verification_code"], "MATCHED")
