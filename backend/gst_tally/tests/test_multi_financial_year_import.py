"""Regression coverage for multi-financial-year batch import.

A batch spanning more than one source financial year must no longer block
the whole batch (the old "Batch contains multiple financial years" /
MULTIPLE_FINANCIAL_YEARS behavior) -- invoices matching the Tally company's
current financial year are imported normally; only invoices from a
*different* financial year are excluded, with an honest
Skipped/FINANCIAL_YEAR_MISMATCH result, never a blanket "Not Attempted".
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.service import import_batch
from gst_tally.tests.test_tally_read_write_separation import RecordingClient

GSTIN = "33AAACB2894G1ZJ"
COMPANY = "SRI MAHALAKSHMI TRADERS,"
COMPANY_GSTIN = "33AFHPM6103Q1Z8"

# Tally FY 2025-26 (books beginning 01-Apr-2025, ending 31-Mar-2026) -- same
# shape as the acceptance test in the task spec.
TALLY_PERIOD = {"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}

CONNECTION_STATUS = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                     "company_open": True, "company_name": COMPANY, "company": COMPANY,
                     "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                     "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                     "can_import": True, "message": "ok"}

# Task spec section 12's exact acceptance-test invoice dates: 3 in FY 2024-25
# (March 2025), 3 in FY 2025-26 (April 2025).
ACCEPTANCE_DATES = [date(2025, 3, 7), date(2025, 3, 18), date(2025, 3, 31),
                    date(2025, 4, 1), date(2025, 4, 7), date(2025, 4, 30)]


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class MultiFinancialYearImportTests(TestCase):
    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        for index, invoice_date in enumerate(ACCEPTANCE_DATES, 1):
            GSTInvoice.objects.create(
                import_batch=self.batch, invoice_no=f"INV-{index}", invoice_date=invoice_date,
                customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
                cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
                invoice_value=Decimal("1180"), place_of_supply="33",
                source_line={"source_row_number": index + 1})

    def run_import(self, client=None):
        client = client or RecordingClient()
        with patch("gst_tally.tally.service.odbc_company_status", return_value=CONNECTION_STATUS), \
             patch("gst_tally.tally.service.odbc_company_period", return_value=TALLY_PERIOD), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            return import_batch(self.batch, client)

    def test_batch_is_not_globally_blocked(self):
        result = self.run_import()
        self.assertNotEqual(result.get("batch_status"), "MULTIPLE_FINANCIAL_YEARS")

    def test_march_invoices_are_skipped_with_financial_year_mismatch(self):
        result = self.run_import()
        march_rows = [row for row in result["results"] if row["invoice_no"] in {"INV-1", "INV-2", "INV-3"}]
        self.assertEqual(len(march_rows), 3)
        for row in march_rows:
            self.assertEqual(row["status"], "Skipped")
            self.assertEqual(row["reason_code"], "FINANCIAL_YEAR_MISMATCH")
            self.assertEqual(row["invoice_financial_year"], "2024-25")
            self.assertEqual(row["tally_financial_year"], "2025-26")
            self.assertIn("2024-25", row["reason"])
            self.assertIn("2025-26", row["reason"])

    def test_march_invoices_are_never_reported_as_failed(self):
        result = self.run_import()
        march_rows = [row for row in result["results"] if row["invoice_no"] in {"INV-1", "INV-2", "INV-3"}]
        for row in march_rows:
            self.assertNotIn(row["status"], ("Failed", "Not Attempted", "Tally Failed"))

    def test_april_invoices_are_actually_attempted_and_imported(self):
        result = self.run_import()
        april_rows = [row for row in result["results"] if row["invoice_no"] in {"INV-4", "INV-5", "INV-6"}]
        self.assertEqual(len(april_rows), 3)
        for row in april_rows:
            self.assertEqual(row["status"], "Imported")

    def test_no_march_voucher_is_ever_written_to_tally(self):
        client = RecordingClient()
        self.run_import(client)
        written_numbers = {row["number"] for row in client.written}
        self.assertFalse(written_numbers & {"INV-1", "INV-2", "INV-3"})
        self.assertEqual(written_numbers, {"INV-4", "INV-5", "INV-6"})

    def test_summary_counts_represent_every_invoice_truthfully(self):
        result = self.run_import()
        summary = result["summary"]
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["imported"], 3)
        self.assertEqual(summary["skipped"], 3)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["not_attempted"], 0)
        # Every row must be represented: nothing hidden as an untracked bucket.
        self.assertEqual(len(result["results"]), 6)

    def test_result_contract_exposes_financial_year_routing_fields(self):
        result = self.run_import()
        self.assertEqual(result["current_tally_fy"], "2025-26")
        self.assertEqual(result["financial_year_groups"], {"2024-25": 3, "2025-26": 3})
        self.assertEqual(result["eligible_count"], 3)

    def test_single_financial_year_batch_is_unaffected(self):
        # Backward compatibility: a batch entirely within the current Tally FY
        # must behave exactly as before -- no FY routing, no skipped rows.
        single_fy_batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY})
        GSTInvoice.objects.create(
            import_batch=single_fy_batch, invoice_no="SOLO-1", invoice_date=date(2025, 4, 15),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1180"), place_of_supply="33", source_line={"source_row_number": 2})
        client = RecordingClient()
        with patch("gst_tally.tally.service.odbc_company_status", return_value=CONNECTION_STATUS), \
             patch("gst_tally.tally.service.odbc_company_period", return_value=TALLY_PERIOD), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            result = import_batch(single_fy_batch, client)
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["summary"]["imported"], 1)
        self.assertEqual(result["summary"]["skipped"], 0)
        self.assertFalse(any(row["status"] == "Skipped" and row.get("reason_code") == "FINANCIAL_YEAR_MISMATCH"
                             for row in result["results"]))
