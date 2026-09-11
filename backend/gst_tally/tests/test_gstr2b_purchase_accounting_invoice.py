"""Final acceptance test: one real GSTR-2B multi-rate invoice, end to end --
GSTR-2B source -> normalized JSON -> Sundry Creditors party -> GST Purchase
ledger(s) -> Input CGST+SGST (intra-state) / Input IGST (inter-state) ->
Purchase voucher -> Accounting Invoice mode payload. Uses the exact figures
from the reference screen: 190.00 @ 5% and 5,960.00 @ 18%, no round off.
"""
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from django.test import TestCase

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.json_voucher_builder import build_json_voucher
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.voucher_builder import build_voucher

SUPPLIER_GSTIN = "33AABCT7933K1Z4"
COMPANY = {"state": "Tamil Nadu"}


class Gstr2bPurchaseAccountingInvoiceTests(TestCase):
    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
            source_parties={SUPPLIER_GSTIN: {"party_name": "RE Sustainability IWM Solutions Limited (source)"}})
        GSTParty.objects.create(gstin=SUPPLIER_GSTIN, trade_name="RE SUSTAINABILITY IWM SOLUTIONS LIMITED",
                                state_name="Tamil Nadu")
        for row_number, taxable, rate, cgst, sgst in (
            (2, "190.00", "5", "4.75", "4.75"),
            (3, "5960.00", "18", "536.40", "536.40"),
        ):
            GSTInvoice.objects.create(
                import_batch=self.batch, invoice_no="1", invoice_date=date(2025, 4, 1),
                customer_gstin=SUPPLIER_GSTIN, taxable_value=Decimal(taxable), tax_percent=Decimal(rate),
                cgst=Decimal(cgst), sgst=Decimal(sgst), igst=Decimal("0"), cess=Decimal("0"),
                invoice_value=Decimal("7232.30"), place_of_supply="33",
                source_line={"source_row_number": row_number})

    def build(self):
        [voucher] = normalized_vouchers(self.batch, COMPANY)
        return voucher

    def test_party_is_sundry_creditors_with_fetched_trade_name_not_gstin(self):
        voucher = self.build()
        self.assertEqual(voucher["party_group"], "Sundry Creditors")
        self.assertEqual(voucher["party"]["name"], "RE SUSTAINABILITY IWM SOLUTIONS LIMITED")
        self.assertNotEqual(voucher["party"]["name"], SUPPLIER_GSTIN)

    def test_party_details_use_gstin_state_as_authoritative_fallback(self):
        voucher = self.build()

        self.assertEqual(voucher["party"], {
            "gstin": SUPPLIER_GSTIN,
            "trade_name": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED",
            "name": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED",
            "name_source": "Name/Mapping",
            "legal_name": "",
            "mailing_name": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED",
            "address": "",
            "state": "Tamil Nadu",
            "country": "India",
            "pincode": "",
            "registration_type": "Regular",
            "place_of_supply": "Tamil Nadu",
            # A name-only fallback (no legal_name/address/pincode) is never
            # genuine Sandbox taxpayer enrichment, no matter how readable it is.
            "party_details_complete": False,
        })

    def test_voucher_is_purchase_type_with_both_rate_buckets(self):
        voucher = self.build()
        self.assertEqual(voucher["voucher_type"], "Purchase")
        self.assertEqual(voucher["account_group"], "Purchase Accounts")
        ledgers = {row["sales_ledger"]: row["taxable_value"] for row in voucher["rate_allocations"]}
        self.assertEqual(ledgers, {"GST Purchase 5%": "190.00", "GST Purchase 18%": "5960.00"})

    def test_intra_state_produces_input_cgst_sgst_pairs_per_rate_no_igst(self):
        voucher = self.build()
        taxes = {row["ledger"]: row["amount"] for row in voucher["tax_allocations"]}
        self.assertEqual(taxes, {
            "Input CGST 2.5%": "4.75", "Input SGST 2.5%": "4.75",
            "Input CGST 9%": "536.40", "Input SGST 9%": "536.40",
        })
        self.assertFalse(any("IGST" in name for name in taxes))

    def test_invoice_total_matches_source_with_no_round_off_needed(self):
        voucher = self.build()
        self.assertEqual(voucher["invoice_total"], "7232.30")
        total = (Decimal(voucher["taxable_total"]) + Decimal(voucher["cgst"])
                + Decimal(voucher["sgst"]) + Decimal(voucher["igst"]))
        self.assertEqual(total, Decimal("7232.30"))

    def test_xml_payload_is_purchase_accounting_invoice_not_as_voucher(self):
        voucher = {**self.build(), "rounding_adjustment": "0"}
        xml = ET.fromstring(build_voucher(voucher, "GSTRCOMPANY"))
        node = xml.find(".//VOUCHER")
        self.assertEqual(node.get("VCHTYPE"), "Purchase")
        self.assertEqual(node.get("OBJVIEW"), "Invoice Voucher View")
        self.assertEqual(xml.findtext(".//PERSISTEDVIEW"), "Invoice Voucher View")
        self.assertEqual(xml.findtext(".//ISINVOICE"), "Yes")
        self.assertIsNone(xml.find(".//ALLINVENTORYENTRIES.LIST"))
        # "Supplier Invoice No." / "Date" fields on the Purchase Accounting Invoice.
        self.assertEqual(xml.findtext(".//REFERENCE"), "1")
        self.assertEqual(xml.findtext(".//REFERENCEDATE"), "20250401")
        self.assertEqual(xml.findtext(".//PARTYLEDGERNAME"), "RE SUSTAINABILITY IWM SOLUTIONS LIMITED")
        party_entry = next(entry for entry in xml.findall(".//LEDGERENTRIES.LIST") if entry.findtext("ISPARTYLEDGER") == "Yes")
        self.assertEqual(party_entry.findtext("ISLASTDEEMEDPOSITIVE"), "No")
        self.assertEqual(party_entry.findtext("BILLALLOCATIONS.LIST/NAME"), "1")
        self.assertEqual(party_entry.findtext("BILLALLOCATIONS.LIST/BILLTYPE"), "New Ref")
        self.assertEqual(party_entry.findtext("BILLALLOCATIONS.LIST/AMOUNT"), "7232.30")

    def test_xml_ledger_entries_match_the_reference_screen_amounts_and_order(self):
        voucher = {**self.build(), "rounding_adjustment": "0"}
        xml = ET.fromstring(build_voucher(voucher, "GSTRCOMPANY"))
        entries = xml.findall(".//LEDGERENTRIES.LIST")
        names = [entry.findtext("LEDGERNAME") for entry in entries]
        self.assertEqual(names, [
            "RE SUSTAINABILITY IWM SOLUTIONS LIMITED",
            "GST Purchase 5%", "GST Purchase 18%",
            "Input CGST 2.5%", "Input SGST 2.5%",
            "Input CGST 9%", "Input SGST 9%",
        ])
        amounts = {entry.findtext("LEDGERNAME"): entry.findtext("AMOUNT") for entry in entries}
        self.assertEqual(amounts["GST Purchase 5%"], "-190.00")
        self.assertEqual(amounts["GST Purchase 18%"], "-5960.00")
        self.assertEqual(amounts["Input CGST 2.5%"], "-4.75")
        self.assertEqual(amounts["Input SGST 2.5%"], "-4.75")
        self.assertEqual(amounts["Input CGST 9%"], "-536.40")
        self.assertEqual(amounts["Input SGST 9%"], "-536.40")
        self.assertEqual(amounts["RE SUSTAINABILITY IWM SOLUTIONS LIMITED"], "7232.30")
        self.assertEqual(sum(Decimal(v) for v in amounts.values()), Decimal("0.00"))
        # No voucher-level GST rate override: RATEOFINVOICETAX/BASICRATEOFINVOICETAX/
        # RATE/GSTTAXRATE/RATEDETAILS.LIST must be absent so Tally resolves the
        # GST rate purely from the named ledger's own master ("As per Ledger",
        # not "As per Voucher").
        taxable = {entry.findtext("LEDGERNAME"): entry for entry in entries if entry.find("GSTASSESSABLEVALUE") is not None}
        for name in ("GST Purchase 5%", "GST Purchase 18%"):
            for tag in ("RATE", "RATEOFINVOICETAX", "BASICRATEOFINVOICETAX", "GSTTAXRATE"):
                self.assertIsNone(taxable[name].find(tag))
            self.assertEqual(taxable[name].findall("RATEDETAILS.LIST"), [])
        self.assertEqual(taxable["GST Purchase 5%"].findtext("VATEXPAMOUNT"), "-190.00")

    def test_json_payload_mirrors_xml_and_omits_sales_only_buyer_fields(self):
        voucher = {**self.build(), "rounding_adjustment": "0"}
        payload = build_json_voucher(voucher, "GSTRCOMPANY")["tallymessage"][0]
        self.assertEqual(payload["vouchertypename"], "Purchase")
        self.assertEqual(payload["metadata"]["objview"], "Invoice Voucher View")
        self.assertEqual(payload["persistedview"], "Invoice Voucher View")
        self.assertTrue(payload["isinvoice"])
        self.assertEqual(payload["reference"], "1")
        self.assertEqual(payload["referencedate"], "20250401")
        self.assertNotIn("basicbuyername", payload)
        self.assertNotIn("basicbuyeraddress", payload)
        entries = {row["ledgername"]: row["amount"] for row in payload["ledgerentries"]}
        self.assertEqual(entries["GST Purchase 5%"], "-190.00")
        self.assertEqual(entries["Input CGST 9%"], "-536.40")
        # No voucher-level GST rate override on the JSON path either.
        for row in payload["ledgerentries"]:
            self.assertNotIn("rateofinvoicetax", row)
            self.assertNotIn("basicrateofinvoicetax", row)
            self.assertNotIn("ratedetails", row)

    def test_interstate_supplier_uses_input_igst_only(self):
        interstate_gstin = "29ABFFA3666C1ZQ"
        GSTParty.objects.create(gstin=interstate_gstin, trade_name="KARNATAKA SUPPLIER", state_name="Karnataka")
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b2.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
            source_parties={interstate_gstin: {"party_name": "Karnataka Supplier"}})
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="2", invoice_date=date(2025, 4, 1),
            customer_gstin=interstate_gstin, taxable_value=Decimal("1000.00"), tax_percent=Decimal("18"),
            cgst=Decimal("0"), sgst=Decimal("0"), igst=Decimal("180.00"), cess=Decimal("0"),
            invoice_value=Decimal("1180.00"), place_of_supply="29",
            source_line={"source_row_number": 2})
        [voucher] = normalized_vouchers(batch, COMPANY)
        taxes = {row["ledger"]: row["amount"] for row in voucher["tax_allocations"]}
        self.assertEqual(taxes, {"Input IGST 18%": "180.00"})
        self.assertNotIn("Input CGST 9%", taxes)
        self.assertNotIn("Input SGST 9%", taxes)
