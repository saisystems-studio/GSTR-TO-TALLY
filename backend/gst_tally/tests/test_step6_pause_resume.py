"""Step 6 pause/resume regression tests.

Exercises import_batch()'s own pause boundary and resume-safety directly
(the same function tally/import_job.py calls on a background thread) --
never through the job/thread machinery, so a failure here points straight
at import_batch()'s voucher loop rather than at threading/polling.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.service import import_batch

from .test_step6_result_handling import COMPANY, COMPANY_GSTIN, GSTIN, ScriptedClient


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class PauseResumeImportTests(TestCase):
    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        for number in ("1", "2", "3"):
            GSTInvoice.objects.create(
                import_batch=self.batch, invoice_no=number, invoice_date=date(2025, 4, 1),
                customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
                cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
                invoice_value=Decimal("1180"), place_of_supply="33",
                source_line={"source_row_number": int(number) + 1})
        self.status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                       "company_open": True, "company_name": COMPANY, "company": COMPANY,
                       "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                       "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                       "can_import": True, "message": "ok"}

    def run_import(self, client, should_pause_callback=None, progress_callback=None):
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            return import_batch(self.batch, client, progress_callback=progress_callback,
                               should_pause_callback=should_pause_callback)

    def test_pause_stops_before_the_next_voucher_and_keeps_prior_progress(self):
        """Voucher 1 must be allowed to finish; pause takes effect only before
        voucher 2 starts (the exact "voucher 88 already in flight" example in
        the Step 6 spec)."""
        calls = {"n": 0}

        def should_pause():
            calls["n"] += 1
            return calls["n"] > 1

        client = ScriptedClient()
        result = self.run_import(client, should_pause_callback=should_pause)

        self.assertTrue(result["paused"])
        self.assertEqual(result["processed_before_pause"], 1)
        self.assertEqual(len(client.written), 1)
        self.assertEqual(client.written[0]["number"], "1")
        self.assertEqual(result["summary"]["imported"], 1)
        self.assertEqual(len(result["results"]), 1)

    def test_pause_requested_before_any_voucher_writes_nothing(self):
        client = ScriptedClient()
        result = self.run_import(client, should_pause_callback=lambda: True)

        self.assertTrue(result["paused"])
        self.assertEqual(result["processed_before_pause"], 0)
        self.assertEqual(len(client.written), 0)
        self.assertEqual(result["summary"]["imported"], 0)

    def test_resume_continues_from_checkpoint_and_never_resends_the_paused_run(self):
        calls = {"n": 0}

        def should_pause():
            calls["n"] += 1
            return calls["n"] > 1

        first_client = ScriptedClient()
        first_result = self.run_import(first_client, should_pause_callback=should_pause)
        self.assertTrue(first_result["paused"])
        self.assertEqual(len(first_client.written), 1)

        # Resume: Tally now genuinely has voucher 1 (what the paused run
        # itself just wrote) -- the second run must re-verify it live and
        # never resend its XML, then continue with vouchers 2 and 3.
        second_client = ScriptedClient(live_vouchers=first_client.written)
        second_result = self.run_import(second_client, should_pause_callback=lambda: False)

        self.assertFalse(second_result.get("paused"))
        rows = {r["invoice_no"]: r["status"] for r in second_result["results"]}
        self.assertEqual(rows["1"], "Already Imported")
        self.assertEqual(rows["2"], "Imported")
        self.assertEqual(rows["3"], "Imported")
        voucher_write_payloads = [p for p in second_client.payloads
                                  if b"Import Data" in p and b"<REPORTNAME>Vouchers</REPORTNAME>" in p]
        sent_numbers = set()
        from xml.etree import ElementTree as ET
        for payload in voucher_write_payloads:
            sent_numbers.add(ET.fromstring(payload).findtext(".//VOUCHERNUMBER") or "")
        self.assertNotIn("1", sent_numbers)
        self.assertEqual(sent_numbers, {"2", "3"})

    def test_progress_callback_reports_cumulative_counts_not_reset_per_run(self):
        """The frontend's live counters come from this callback -- it must
        report the running total processed so far, not restart from zero on
        every call within the same run. One callback per voucher attempted,
        plus one final call once every voucher has a result (processed ==
        total) -- see import_job.py's _make_progress_callback, which turns
        that last call into the job's VERIFYING status."""
        seen = []

        def progress(processed, total, results_so_far, current_invoice):
            seen.append((processed, total, len(results_so_far)))

        client = ScriptedClient()
        self.run_import(client, progress_callback=progress)

        self.assertEqual(seen, [(0, 3, 0), (1, 3, 1), (2, 3, 2), (3, 3, 3)])
        self.assertEqual(seen[-1][2], 3, "final callback must report all 3 results, not reset to 0")
