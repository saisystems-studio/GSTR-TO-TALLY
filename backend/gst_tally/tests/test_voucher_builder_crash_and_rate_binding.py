"""Regression tests for two voucher_builder.py / service.py defects:

1. build_voucher() had its entire "tax ledgers -> cess -> other charges ->
   round off -> narration -> return xml" tail wrongly nested inside per-item/
   per-field conditionals, so for a normal rate_allocations-driven voucher it
   fell off the end of the function and returned None. service.py then called
   `.decode()` on that None unconditionally, crashing the whole import
   request with AttributeError: 'NoneType' object has no attribute 'decode'.

2. _rate_metadata()/_taxable_amount() must bind each taxable ledger's own
   GST rate (RATEOFINVOICETAX/BASICRATEOFINVOICETAX/GSTTAXRATE/RATEDETAILS)
   independently per allocation, so a mixed-rate invoice never has one row's
   rate bleed into another.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, TestCase, override_settings

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.service import import_batch, safe_decode
from gst_tally.tally.voucher_builder import build_voucher
from gst_tally.tests.test_tally_read_write_separation import COMPANY, COMPANY_GSTIN, GSTIN, RecordingClient


def _purchase_voucher(rate_allocations, tax_allocations, invoice_total,
                      cess="0", other_charges="0", rounding_adjustment="0.00"):
    return {"invoice_number": "1", "invoice_date": "2025-04-01", "voucher_type": "Purchase",
            "party": {"name": "Buyer", "gstin": "33AAACB2894G1ZJ"},
            "rate_allocations": rate_allocations, "tax_allocations": tax_allocations,
            "invoice_total": invoice_total, "cess": cess, "other_charges": other_charges,
            "rounding_adjustment": rounding_adjustment}


class SafeDecodeTests(SimpleTestCase):
    def test_none_returns_default(self):
        self.assertEqual(safe_decode(None), "")
        self.assertEqual(safe_decode(None, "fallback"), "fallback")

    def test_bytes_are_decoded(self):
        self.assertEqual(safe_decode(b"<ENVELOPE/>"), "<ENVELOPE/>")

    def test_str_is_returned_as_is(self):
        self.assertEqual(safe_decode("already a string"), "already a string")


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class BuildVoucherNoneCrashRegressionTests(TestCase):
    """TEST 1 -- a build_voucher() that unexpectedly returns None must never
    crash the request with AttributeError: 'NoneType' object has no
    attribute 'decode'. It must become one controlled, per-voucher Failed
    result instead."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Source Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="PUR-1", invoice_date=date(2025, 4, 1),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1180"), place_of_supply="33",
            source_line={"source_row_number": 2})
        self.status = {"odbc_connected": True, "read_connected": True, "company_detected": True,
                       "company_open": True, "company_name": COMPANY, "company": COMPANY,
                       "company_gstin": COMPANY_GSTIN, "gstin": COMPANY_GSTIN,
                       "company_state": "Tamil Nadu", "state": "Tamil Nadu", "failure_type": "",
                       "can_import": True, "message": "ok"}

    def test_none_voucher_payload_becomes_a_controlled_failed_result_not_a_crash(self):
        client = RecordingClient()
        with patch("gst_tally.tally.service.odbc_company_status", return_value=self.status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": COMPANY, "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})), \
             patch("gst_tally.tally.service.build_voucher", return_value=None):
            # Must not raise AttributeError: 'NoneType' object has no attribute 'decode'.
            result = import_batch(self.batch, client)

        row = next(row for row in result["results"] if row["invoice_no"] == "PUR-1")
        self.assertEqual(row["status"], "Failed")
        self.assertIn("Tally voucher XML generation returned no payload", row["reason"])
        self.assertIn("PUR-1", row["reason"])
        self.assertEqual(result["summary"]["total"], 1)


class MixedGstRateBindingTests(SimpleTestCase):
    """TEST 2/3/4 -- each taxable ledger allocation carries its own,
    independently-bound GST rate; a later allocation must never overwrite an
    earlier one's rate metadata."""

    def _entry(self, xml, ledger_name):
        return next(e for e in xml.findall(".//LEDGERENTRIES.LIST") if e.findtext("LEDGERNAME") == ledger_name)

    def test_single_5_percent_allocation_carries_its_own_rate(self):
        voucher = _purchase_voucher(
            rate_allocations=[{"account_ledger": "GST Purchase 5%", "gst_rate": "5", "taxable_value": "190.00"}],
            tax_allocations=[{"ledger": "Input CGST 2.5%", "amount": "4.75"},
                             {"ledger": "Input SGST 2.5%", "amount": "4.75"}],
            invoice_total="199.50")
        xml = ET.fromstring(build_voucher(voucher, "D"))
        entry = self._entry(xml, "GST Purchase 5%")

        self.assertEqual(entry.findtext("RATE"), "5")
        self.assertEqual(entry.findtext("RATEOFINVOICETAX"), "5")
        self.assertEqual(entry.findtext("BASICRATEOFINVOICETAX"), "5")
        self.assertEqual(entry.findtext("GSTTAXRATE"), "5")
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE") for row in entry.findall("RATEDETAILS.LIST")}
        self.assertEqual(rates, {"CGST": "2.5", "SGST/UTGST": "2.5", "IGST": "5"})

    def test_single_18_percent_allocation_carries_its_own_rate(self):
        voucher = _purchase_voucher(
            rate_allocations=[{"account_ledger": "GST Purchase 18%", "gst_rate": "18", "taxable_value": "5960.00"}],
            tax_allocations=[{"ledger": "Input CGST 9%", "amount": "536.40"},
                             {"ledger": "Input SGST 9%", "amount": "536.40"}],
            invoice_total="7032.80")
        xml = ET.fromstring(build_voucher(voucher, "D"))
        entry = self._entry(xml, "GST Purchase 18%")

        self.assertEqual(entry.findtext("RATE"), "18")
        self.assertEqual(entry.findtext("RATEOFINVOICETAX"), "18")
        self.assertEqual(entry.findtext("BASICRATEOFINVOICETAX"), "18")
        self.assertEqual(entry.findtext("GSTTAXRATE"), "18")
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE") for row in entry.findall("RATEDETAILS.LIST")}
        self.assertEqual(rates, {"CGST": "9", "SGST/UTGST": "9", "IGST": "18"})

    def test_mixed_rate_voucher_never_lets_one_rate_bleed_into_another(self):
        voucher = _purchase_voucher(
            rate_allocations=[
                {"account_ledger": "GST Purchase 5%", "gst_rate": "5", "taxable_value": "190.00"},
                {"account_ledger": "GST Purchase 18%", "gst_rate": "18", "taxable_value": "5960.00"},
            ],
            tax_allocations=[
                {"ledger": "Input CGST 2.5%", "amount": "4.75"}, {"ledger": "Input SGST 2.5%", "amount": "4.75"},
                {"ledger": "Input CGST 9%", "amount": "536.40"}, {"ledger": "Input SGST 9%", "amount": "536.40"},
            ],
            invoice_total="7232.30")
        xml = ET.fromstring(build_voucher(voucher, "D"))

        five_percent = self._entry(xml, "GST Purchase 5%")
        eighteen_percent = self._entry(xml, "GST Purchase 18%")

        self.assertEqual(five_percent.findtext("RATE"), "5")
        self.assertEqual(five_percent.findtext("RATEOFINVOICETAX"), "5")
        self.assertEqual(five_percent.findtext("BASICRATEOFINVOICETAX"), "5")
        self.assertEqual(five_percent.findtext("GSTTAXRATE"), "5")
        self.assertEqual(eighteen_percent.findtext("RATE"), "18")
        self.assertEqual(eighteen_percent.findtext("RATEOFINVOICETAX"), "18")
        self.assertEqual(eighteen_percent.findtext("BASICRATEOFINVOICETAX"), "18")
        self.assertEqual(eighteen_percent.findtext("GSTTAXRATE"), "18")
        # The literal failure mode being guarded against: the second
        # allocation's rate must not have overwritten the first's.
        self.assertNotEqual(five_percent.findtext("RATEOFINVOICETAX"), eighteen_percent.findtext("RATEOFINVOICETAX"))

    def test_reversed_input_order_still_binds_each_row_to_its_own_rate(self):
        """Feeding the 18% allocation first (before 5%) must not make the
        first-written taxable row blank or borrow the wrong rate -- proves
        the fix is not specific to a particular position or to 5% itself."""
        voucher = _purchase_voucher(
            rate_allocations=[
                {"account_ledger": "GST Purchase 18%", "gst_rate": "18", "taxable_value": "5960.00"},
                {"account_ledger": "GST Purchase 5%", "gst_rate": "5", "taxable_value": "190.00"},
            ],
            tax_allocations=[
                {"ledger": "Input CGST 9%", "amount": "536.40"}, {"ledger": "Input SGST 9%", "amount": "536.40"},
                {"ledger": "Input CGST 2.5%", "amount": "4.75"}, {"ledger": "Input SGST 2.5%", "amount": "4.75"},
            ],
            invoice_total="7232.30")
        xml = ET.fromstring(build_voucher(voucher, "D"))

        taxable_entries = [e for e in xml.findall(".//LEDGERENTRIES.LIST") if e.findtext("RATE") is not None]
        self.assertEqual(len(taxable_entries), 2)
        # Whichever row is physically first in the generated XML, every row's
        # rate fields must match its OWN ledger name -- never blank, never
        # the other allocation's rate.
        for entry in taxable_entries:
            expected_rate = "5" if entry.findtext("LEDGERNAME") == "GST Purchase 5%" else "18"
            self.assertEqual(entry.findtext("RATE"), expected_rate)
            self.assertEqual(entry.findtext("RATEOFINVOICETAX"), expected_rate)
            self.assertEqual(entry.findtext("BASICRATEOFINVOICETAX"), expected_rate)
            self.assertEqual(entry.findtext("GSTTAXRATE"), expected_rate)

        first_row = taxable_entries[0]
        self.assertIsNotNone(first_row.findtext("RATE"))
        self.assertNotEqual(first_row.findtext("RATE"), "")


class VoucherAmountsUnchangedByRateFixTests(SimpleTestCase):
    """TEST 5 -- fixing the GST rate metadata / None-return bug must not
    change any accounting amount, and must not duplicate cess/other-charges/
    round-off/tax ledger entries (the old corrupted control flow re-entered
    that tail once per truthy cgst/sgst/igst field)."""

    def test_amounts_and_signs_are_unchanged_and_nothing_is_duplicated(self):
        voucher = _purchase_voucher(
            rate_allocations=[
                {"account_ledger": "GST Purchase 5%", "gst_rate": "5", "taxable_value": "190.00"},
                {"account_ledger": "GST Purchase 18%", "gst_rate": "18", "taxable_value": "5960.00"},
            ],
            tax_allocations=[
                {"ledger": "Input CGST 2.5%", "amount": "4.75"}, {"ledger": "Input SGST 2.5%", "amount": "4.75"},
                {"ledger": "Input CGST 9%", "amount": "536.40"}, {"ledger": "Input SGST 9%", "amount": "536.40"},
            ],
            invoice_total="7237.25", cess="10.00", other_charges="5.00", rounding_adjustment="-0.05")
        xml = ET.fromstring(build_voucher(voucher, "D"))
        entries = xml.findall(".//LEDGERENTRIES.LIST")
        names = [entry.findtext("LEDGERNAME") for entry in entries]

        # Each ledger appears exactly once -- no duplication from the old
        # nested per-field cess/other-charges/round-off re-entry bug.
        self.assertEqual(names.count("Cess"), 1)
        self.assertEqual(names.count("Other Charges"), 1)
        self.assertEqual(names.count("Round Off"), 1)
        self.assertEqual(names.count("Input CGST 2.5%"), 1)
        self.assertEqual(names.count("Input SGST 2.5%"), 1)
        self.assertEqual(names.count("Input CGST 9%"), 1)
        self.assertEqual(names.count("Input SGST 9%"), 1)

        amounts = {entry.findtext("LEDGERNAME"): entry.findtext("AMOUNT") for entry in entries}
        self.assertEqual(amounts["GST Purchase 5%"], "-190.00")
        self.assertEqual(amounts["GST Purchase 18%"], "-5960.00")
        self.assertEqual(amounts["Input CGST 2.5%"], "-4.75")
        self.assertEqual(amounts["Input SGST 2.5%"], "-4.75")
        self.assertEqual(amounts["Input CGST 9%"], "-536.40")
        self.assertEqual(amounts["Input SGST 9%"], "-536.40")
        self.assertEqual(amounts["Cess"], "-10.00")
        self.assertEqual(amounts["Other Charges"], "5.00")
        self.assertEqual(amounts["Round Off"], "0.05")
        self.assertEqual(amounts["Buyer"], "7237.25")
        self.assertEqual(sum(Decimal(v) for v in amounts.values()), Decimal("0.00"))
        self.assertIsNotNone(xml.findtext(".//NARRATION"))
