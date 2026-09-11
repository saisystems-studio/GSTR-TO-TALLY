from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.round_off import resolve_round_off
from gst_tally.tally.service import save_voucher_correction
from gst_tally.tally.odbc import verify_tally_company

GSTIN = "33AAACB2894G1ZJ"

class InvoiceCorrectionTests(TestCase):
    def setUp(self):
        self.batch = GSTImportBatch.objects.create(company_gstin=GSTIN, gst_return_type="GSTR1")
        GSTParty.objects.create(gstin=GSTIN, trade_name="Supplier", state_name="Tamil Nadu")
        for rate, tax in [(10, 5), (36, 18), (0, 0)]:
            GSTInvoice.objects.create(import_batch=self.batch, customer_gstin=GSTIN,
                invoice_no="14", invoice_date=date(2025, 4, 4), taxable_value=100,
                tax_percent=rate, cgst=tax, sgst=tax, invoice_value=340, place_of_supply="33")

    def vouchers(self):
        return normalized_vouchers(self.batch, {"state": "Tamil Nadu", "gstin": GSTIN})

    def test_multi_item_and_persisted_correction(self):
        before_rows = list(self.batch.invoices.values())
        voucher = self.vouchers()[0]
        self.assertEqual(len(voucher["items"]), 3)
        self.assertEqual(voucher["taxable_total"], "300.00")
        self.assertEqual(voucher["invoice_total"], "340.00")
        self.assertEqual(resolve_round_off(voucher)["component_total"], "346.00")
        with patch("gst_tally.tally.service.odbc_company_status", return_value={"company_state": "Tamil Nadu"}), patch("gst_tally.tally.service.prepare", return_value=({}, [voucher], {})), patch("gst_tally.tally.service.voucher_preview", return_value={"vouchers": []}):
            save_voucher_correction(self.batch, GSTIN, "14", date(2025, 4, 4), "round_off", Decimal("-6"))
        self.batch.refresh_from_db()
        result = resolve_round_off(self.vouchers()[0])
        self.assertEqual(result["round_off"], "-6.00")
        self.assertEqual(result["final_voucher_total"], "340.00")
        self.assertEqual(result["status"], "Ready")
        self.assertEqual(list(self.batch.invoices.values()), before_rows)

    def test_other_invoice_stays_separate(self):
        GSTInvoice.objects.create(import_batch=self.batch, customer_gstin=GSTIN,
            invoice_no="15", invoice_date=date(2025, 4, 4), taxable_value=100,
            tax_percent=0, invoice_value=100, place_of_supply="33")
        vouchers = self.vouchers()
        self.assertEqual(len(vouchers), 2)
        self.assertEqual(resolve_round_off(vouchers[1])["final_voucher_total"], "100.00")

    def test_nonmatching_manual_amount_is_rejected(self):
        voucher = self.vouchers()[0]
        with patch("gst_tally.tally.service.odbc_company_status", return_value={}), patch("gst_tally.tally.service.prepare", return_value=({}, [voucher], {})):
            with self.assertRaises(ValueError):
                save_voucher_correction(self.batch, GSTIN, "14", date(2025, 4, 4), "round_off", Decimal("-5"))
        self.assertFalse((self.batch.company_details or {}).get("voucher_corrections"))

    def test_invalid_gst_is_not_hidden(self):
        voucher = self.vouchers()[0]
        voucher["igst"] = "1"
        with patch("gst_tally.tally.service.odbc_company_status", return_value={}), patch("gst_tally.tally.service.prepare", return_value=({}, [voucher], {})):
            with self.assertRaises(ValueError):
                save_voucher_correction(self.batch, GSTIN, "14", date(2025, 4, 4), "round_off", Decimal("-7"))

class CompanyGstinTests(SimpleTestCase):
    def test_names_are_display_only(self):
        status = {"read_connected": True, "company_detected": True, "company_name": "ABC TRADERS 2026", "company_gstin": GSTIN}
        self.assertTrue(verify_tally_company(" " + GSTIN.lower() + " ", status, {"company_name": "ABC TRADERS"})["company_verified"])
        self.assertFalse(verify_tally_company("27AAACB2894G1ZJ", status, {"company_name": "ABC TRADERS 2026"})["company_verified"])
