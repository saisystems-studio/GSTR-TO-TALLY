"""Regression tests for import_batch's result-aggregation: a source invoice
must never appear twice in the final results list, and every summary counter
must reconcile exactly to the total unique invoice count.

Covers two layers of the fix:
1. voucher_result_key / _dedupe_voucher_results -- a generic post-processing
   safety net that collapses multiple result rows for the same source invoice
   down to a single highest-priority outcome (Review Required > Imported >
   Already Imported > Master Setup Failed > other Failed statuses > Skipped).
2. The concrete regression it was written for: a required-master failure
   used to leave a stray, uncontrolled "continue" for only the first affected
   voucher, so every later invoice sharing that same failed master fell
   through past the dependency check and picked up a second, contradictory
   result row.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.service import _dedupe_voucher_results, import_batch, voucher_result_key
from gst_tally.tests.test_tally_read_write_separation import (
    COMPANY, COMPANY_GSTIN, GSTIN, PermanentlyBrokenPurchaseLedgerClient,
)


def _result(invoice_no, status, date_="2025-04-01", gstin=GSTIN, voucher_type="Purchase"):
    return {"invoice_no": invoice_no, "date": date_, "party": "Source Supplier", "gstin": gstin,
            "voucher_type": voucher_type, "status": status, "voucher_identifier": ""}


class VoucherResultKeyTests(SimpleTestCase):
    def test_key_normalizes_case_and_whitespace_but_not_date_or_type(self):
        a = voucher_result_key(_result(" pur-1 ", "Imported", gstin=" 33aaacb2894g1zj "))
        b = voucher_result_key(_result("PUR-1", "Imported", gstin="33AAACB2894G1ZJ"))
        self.assertEqual(a, b)

    def test_key_differs_on_invoice_number(self):
        a = voucher_result_key(_result("PUR-1", "Imported"))
        b = voucher_result_key(_result("PUR-2", "Imported"))
        self.assertNotEqual(a, b)


class VoucherResultDedupTests(SimpleTestCase):
    """(SECOND BUG) The three scenarios called out explicitly: a stale
    Master Setup Failed row must never survive alongside a later, real
    outcome for the same invoice."""

    def test_master_setup_failed_plus_already_imported_collapses_to_already_imported(self):
        results = [
            _result("PUR-1", "Master Setup Failed"),
            _result("PUR-1", "Already Imported"),
        ]

        deduped = _dedupe_voucher_results(results)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["status"], "Already Imported")

    def test_master_setup_failed_plus_imported_collapses_to_imported(self):
        results = [
            _result("PUR-1", "Master Setup Failed"),
            _result("PUR-1", "Imported"),
        ]

        deduped = _dedupe_voucher_results(results)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["status"], "Imported")

    def test_order_of_appearance_does_not_matter(self):
        results = [
            _result("PUR-1", "Imported"),
            _result("PUR-1", "Master Setup Failed"),
        ]

        deduped = _dedupe_voucher_results(results)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["status"], "Imported")

    def test_thirty_unique_invoices_reconcile_exactly_to_total(self):
        results = []
        for i in range(1, 31):
            invoice_no = f"PUR-{i}"
            if i <= 5:
                # These five were each visited twice -- once blocked by a
                # since-repaired master, once for the real outcome.
                results.append(_result(invoice_no, "Master Setup Failed"))
                results.append(_result(invoice_no, "Imported" if i % 2 else "Already Imported"))
            elif i <= 8:
                results.append(_result(invoice_no, "Master Setup Failed"))
            elif i <= 20:
                results.append(_result(invoice_no, "Imported"))
            elif i <= 27:
                results.append(_result(invoice_no, "Already Imported"))
            else:
                results.append(_result(invoice_no, "Skipped"))

        deduped = _dedupe_voucher_results(results)
        unique_keys = {voucher_result_key(r) for r in results}

        self.assertEqual(len(deduped), 30)
        self.assertEqual(len(unique_keys), 30)
        imported = sum(r["status"] == "Imported" for r in deduped)
        already_imported = sum(r["status"] == "Already Imported" for r in deduped)
        master_setup_failed = sum(r["status"] == "Master Setup Failed" for r in deduped)
        skipped = sum(r["status"] == "Skipped" for r in deduped)
        self.assertEqual(imported + already_imported + master_setup_failed + skipped, 30)
        # None of the first five stayed at the stale Master Setup Failed status.
        self.assertEqual(master_setup_failed, 3)


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class ImportBatchMasterSetupFailedRegressionTests(TestCase):
    """(SECOND BUG, root cause) A permanently-broken required master used to
    only correctly block the *first* dependent voucher; every later invoice
    sharing that same failed master fell through an unconditional-looking but
    actually first-failure-gated `continue` and picked up a second, spurious
    result (Already Imported / an attempted write) on top of its Master
    Setup Failed row -- producing impossible summaries where imported +
    failed exceeded the total invoice count."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        for i in range(1, 4):
            GSTInvoice.objects.create(
                import_batch=self.batch, invoice_no=f"PUR-{i}", invoice_date=date(2025, 4, 1),
                customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
                cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
                invoice_value=Decimal("1180"), place_of_supply="33",
                source_line={"source_row_number": i + 1})
        self.status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                       "company_open": True, "company_name": COMPANY, "company": COMPANY,
                       "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                       "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                       "can_import": True, "message": "ok"}

    def run_import(self, client):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters",
                   return_value=({"gst purchase 18%": "GST Purchase 18%"}, {}, {})):
            return import_batch(self.batch, client)

    def test_every_invoice_sharing_the_broken_master_gets_exactly_one_result(self):
        client = PermanentlyBrokenPurchaseLedgerClient()

        result = self.run_import(client)

        rows = [row for row in result["results"] if row["invoice_no"].startswith("PUR-")]
        seen_keys = [voucher_result_key(row) for row in rows]
        self.assertEqual(len(rows), 3, f"expected exactly 3 result rows, got {len(rows)}: {rows}")
        self.assertEqual(len(seen_keys), len(set(seen_keys)), "duplicate result row for the same invoice")
        self.assertTrue(all(row["status"] == "Master Setup Failed" for row in rows))

        summary = result["summary"]
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["master_setup_failed"], 3)
        self.assertEqual(summary["imported"], 0)
        self.assertEqual(summary["already_imported"], 0)
        self.assertEqual(
            summary["imported"] + summary["already_imported"] + summary["failed"] + summary["skipped"], 3)
