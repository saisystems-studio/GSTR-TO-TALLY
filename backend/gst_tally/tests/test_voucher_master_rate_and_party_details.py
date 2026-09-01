"""Regression tests for two live-Tally-reported voucher display gaps:

1. GST PURCHASE RATE COLUMN -- import_batch() must bind each taxable
   rate_allocation to its own ledger MASTER's actual, Tally-re-read GST rate
   (not just whatever the source invoice line said), so a mixed-rate invoice
   never lets one allocation's rate leak into another's, and the voucher sent
   to Tally always reflects the confirmed master.

2. PARTY DETAILS -- the voucher must carry the party's own State/Country/GST
   Registration Type/GSTIN (sourced from the already-resolved voucher["party"]
   dict), not just PARTYLEDGERNAME/PARTYNAME, so Tally's Party Details popup
   is populated instead of showing "Not Applicable"/blank.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase, TestCase, override_settings

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.service import import_batch
from gst_tally.tally.voucher_builder import build_voucher
from gst_tally.tests.test_tally_read_write_separation import (
    COMPANY, COMPANY_GSTIN, EMPTY_COLLECTION, GSTIN, IMPORT_ACKNOWLEDGEMENT,
    LIVE_EMPTY_DAY_BOOK, RecordingClient, collection_with,
)

SUPPLIER_GSTIN = "33AABCT7933K1Z4"


class VerifiedRateMasterClient(RecordingClient):
    """GST Purchase 5% and GST Purchase 18% both already exist in Tally with
    correct, fully-configured nested GST Rate Details -- so the master step
    creates/reads them cleanly (no repair needed) and import_batch's rate
    cross-check has a real, distinct master rate to bind each allocation to."""

    RATES = {
        "GST Purchase 5%": ("5", "2.5", "2.5"),
        "GST Purchase 18%": ("18", "9", "9"),
    }

    def post(self, payload):
        self.payloads.append(payload)
        if b"Import Data" in payload:
            self.write_log.append(("xml", payload))
            if b"<REPORTNAME>Vouchers</REPORTNAME>" in payload:
                root = ET.fromstring(payload)
                taxable = [{"ledger": entry.findtext("LEDGERNAME") or "", "amount": entry.findtext("AMOUNT") or "",
                            "gst_rate": entry.findtext("RATEOFINVOICETAX") or ""}
                           for entry in root.findall(".//LEDGERENTRIES.LIST")]
                self.written.append({"number": root.findtext(".//VOUCHERNUMBER") or "",
                                     "type": (root.find(".//VOUCHER").get("VCHTYPE") or ""),
                                     "reference": root.findtext(".//REFERENCE") or "",
                                     "party": root.findtext(".//PARTYLEDGERNAME") or "",
                                     "master_id": "101", "alter_id": "2", "guid": "written-guid",
                                     "taxable_allocations": taxable, "raw_xml": payload})
            return IMPORT_ACKNOWLEDGEMENT
        if b"<TYPE>Object</TYPE>" in payload and b"<SUBTYPE>Ledger</SUBTYPE>" in payload:
            for name, (igst, cgst, sgst) in self.RATES.items():
                if name.encode() in payload:
                    return (f'<ENVELOPE><BODY><DATA><LEDGER NAME="{name}">'
                            f'<PARENT>Purchase Accounts</PARENT><GSTAPPLICABLE>Applicable</GSTAPPLICABLE>'
                            f'<GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>'
                            f'<RATEOFTAXCALCULATION>{igst}</RATEOFTAXCALCULATION>'
                            f'<GSTDETAILS.LIST><APPLICABLEFROM>20250401</APPLICABLEFROM>'
                            f'<TAXABILITY>Taxable</TAXABILITY><SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>'
                            f'<STATEWISEDETAILS.LIST><STATENAME>Any</STATENAME>'
                            f'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD><GSTRATE>{igst}</GSTRATE></RATEDETAILS.LIST>'
                            f'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD><GSTRATE>{cgst}</GSTRATE></RATEDETAILS.LIST>'
                            f'<RATEDETAILS.LIST><GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD><GSTRATE>{sgst}</GSTRATE></RATEDETAILS.LIST>'
                            f'</STATEWISEDETAILS.LIST></GSTDETAILS.LIST></LEDGER></DATA></BODY></ENVELOPE>').encode()
            if b"RE SUSTAINABILITY" in payload:
                return (b'<ENVELOPE><BODY><DATA><LEDGER NAME="RE SUSTAINABILITY IWM SOLUTIONS LIMITED">'
                        b'<PARENT>Sundry Creditors</PARENT><GSTREGISTRATIONNO>' + SUPPLIER_GSTIN.encode() +
                        b'</GSTREGISTRATIONNO><LEDSTATENAME>Tamil Nadu</LEDSTATENAME><COUNTRYNAME>India</COUNTRYNAME>'
                        b'<GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>'
                        b'<LEDGSTREGDETAILS.LIST><GSTIN>' + SUPPLIER_GSTIN.encode() +
                        b'</GSTIN><STATE>Tamil Nadu</STATE><PLACEOFSUPPLY>Tamil Nadu</PLACEOFSUPPLY>'
                        b'</LEDGSTREGDETAILS.LIST></LEDGER></DATA></BODY></ENVELOPE>')
            return EMPTY_COLLECTION
        if b"<TYPE>Voucher</TYPE>" in payload:
            return collection_with(self.existing + self.written)
        if b"<REPORTNAME>Day Book</REPORTNAME>" in payload:
            return LIVE_EMPTY_DAY_BOOK
        return EMPTY_COLLECTION


@override_settings(TALLY_DRY_RUN=False, TALLY_ENABLED=True, TALLY_WRITE_FORMAT="XML",
                   GST_LOOKUP_PROVIDER="", TALLY_ODBC_ENABLED=True)
class MixedRateVoucherUsesVerifiedMasterRateTests(TestCase):
    """(ISSUE 1) A mixed-rate Purchase invoice -- 190.00 @ 5% and 5960.00 @
    18% -- must send each taxable ledger with its own master-verified rate,
    never the other allocation's."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu",
                             "selected_tally_company": COMPANY},
            source_parties={SUPPLIER_GSTIN: {"party_name": "RE Sustainability IWM Solutions Limited"}})
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
             patch("gst_tally.tally.service.odbc_existing_masters", return_value=({}, {})):
            return import_batch(self.batch, client)

    def test_each_allocation_carries_its_own_verified_master_rate_in_the_sent_voucher(self):
        client = VerifiedRateMasterClient()

        result = self.run_import(client)

        row = next(row for row in result["results"] if row["invoice_no"] == "1")
        self.assertEqual(row["status"], "Imported", row.get("reason"))

        [written] = client.written
        rates = {row["ledger"]: row["gst_rate"] for row in written["taxable_allocations"]}
        self.assertEqual(rates["GST Purchase 5%"], "5")
        self.assertEqual(rates["GST Purchase 18%"], "18")
        self.assertNotEqual(rates["GST Purchase 5%"], rates["GST Purchase 18%"])

        # The masters were genuinely re-read from Tally with their own nested
        # rate breakdown -- not merely trusted from the source invoice.
        five_percent_master = next(m for m in result["masters"] if m["name"] == "GST Purchase 5%")
        eighteen_percent_master = next(m for m in result["masters"] if m["name"] == "GST Purchase 18%")
        self.assertEqual(five_percent_master["actual_properties"]["gst_rates"]["IGST"], "5")
        self.assertEqual(eighteen_percent_master["actual_properties"]["gst_rates"]["IGST"], "18")


class VoucherPartyDetailsTests(SimpleTestCase):
    """(ISSUE 2) The voucher's Party Details must reflect the resolved party
    master fields, not just the party ledger name."""

    def _voucher(self, party_overrides):
        return {
            "invoice_number": "1", "invoice_date": "2025-04-01", "voucher_type": "Purchase",
            "party": {"name": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED", **party_overrides},
            "rate_allocations": [{"account_ledger": "GST Purchase 18%", "gst_rate": "18", "taxable_value": "5960.00"}],
            "tax_allocations": [{"ledger": "Input CGST 9%", "amount": "536.40"},
                                {"ledger": "Input SGST 9%", "amount": "536.40"}],
            "invoice_total": "7032.80", "rounding_adjustment": "0",
        }

    def test_generated_voucher_contains_the_verified_party_master_details(self):
        voucher = self._voucher({
            "gstin": SUPPLIER_GSTIN, "state": "Tamil Nadu", "country": "India",
            "registration_type": "Regular", "mailing_name": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED",
            "pincode": "600001", "place_of_supply": "Tamil Nadu",
        })

        xml = ET.fromstring(build_voucher(voucher, "SRI MAHALAKSHMI TRADERS,"))
        node = xml.find(".//VOUCHER")

        self.assertEqual(node.findtext("PARTYLEDGERNAME"), "RE SUSTAINABILITY IWM SOLUTIONS LIMITED")
        self.assertEqual(node.findtext("PARTYGSTIN"), SUPPLIER_GSTIN)
        self.assertEqual(node.findtext("STATENAME"), "Tamil Nadu")
        self.assertEqual(node.findtext("COUNTRYNAME"), "India")
        self.assertEqual(node.findtext("GSTREGISTRATIONTYPE"), "Regular")
        self.assertEqual(node.findtext("PARTYMAILINGNAME"), "RE SUSTAINABILITY IWM SOLUTIONS LIMITED")
        self.assertEqual(node.findtext("PARTYPINCODE"), "600001")
        self.assertEqual(node.findtext("PLACEOFSUPPLY"), "")  # unchanged: still sourced from top-level voucher field

    def test_missing_party_master_fields_are_never_fabricated(self):
        """No gstin/state/country on the source party -- never invent one."""
        voucher = self._voucher({})

        xml = ET.fromstring(build_voucher(voucher, "SRI MAHALAKSHMI TRADERS,"))
        node = xml.find(".//VOUCHER")

        self.assertIsNone(node.find("PARTYGSTIN"))
        self.assertIsNone(node.find("STATENAME"))
        self.assertIsNone(node.find("COUNTRYNAME"))
        self.assertIsNone(node.find("GSTREGISTRATIONTYPE"))
        self.assertIsNone(node.find("PARTYPINCODE"))
        # The name-only fields still work exactly as before.
        self.assertEqual(node.findtext("PARTYLEDGERNAME"), "RE SUSTAINABILITY IWM SOLUTIONS LIMITED")
