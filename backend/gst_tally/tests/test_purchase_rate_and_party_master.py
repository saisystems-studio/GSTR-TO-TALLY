"""Regression tests for two Step 6 Purchase-voucher/party-master gaps:

1. Every taxable "GST Purchase NN%" ledger line must carry its own GST rate
   metadata -- RATEOFINVOICETAX/BASICRATEOFINVOICETAX (Tally's Rate/Per
   binding for the Accounting Invoice screen) plus the CGST/SGST/IGST
   RATEDETAILS breakup -- independently of every other allocation on the
   same voucher, so a mixed-rate invoice never has one rate bleed into
   another row. These are the flat TYPE="Number" elements Tally's own native
   import uses; an earlier attempt at this fix added a separate plain
   <RATE>NN%</RATE> tag and re-wrapped RATEOFINVOICETAX/BASICRATEOFINVOICETAX
   in a legacy ".LIST" collection, but that was never confirmed against a
   real Tally re-read and is not what voucher_builder.py sends.

2. Party master field population (State/Country/GST Registration Type/
   GSTIN/UIN/Place of Supply), and that an existing party with incomplete
   fields is updated in place (via the Alter repair path) rather than
   silently accepted as already-valid or duplicated as a new ledger.
"""
from decimal import Decimal
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from gst_tally.tally.master_builder import build_master
from gst_tally.tally.service import _verify_master_properties
from gst_tally.tally.voucher_builder import build_voucher


def _purchase_voucher(rate_allocations, tax_allocations, invoice_total, rounding_adjustment="0.00"):
    return {"invoice_number": "1", "invoice_date": "2025-04-01", "voucher_type": "Purchase",
            "party": {"name": "YUVRAJ FIREWORKS PRIVATE LIMITED", "gstin": "33AAACY4945P1ZS"},
            "rate_allocations": rate_allocations, "tax_allocations": tax_allocations,
            "invoice_total": invoice_total, "rounding_adjustment": rounding_adjustment}


class FirstTaxableLedgerRateTests(SimpleTestCase):
    """(1) Every taxable GST Purchase/Sales ledger line -- first, middle, or
    only one included -- must carry its own RATEOFINVOICETAX/
    BASICRATEOFINVOICETAX (Tally's Rate/Per binding) and RATEDETAILS breakup,
    independently of every other allocation on the same voucher."""

    def test_single_rate_purchase_voucher_carries_rate_on_its_only_taxable_line(self):
        voucher = _purchase_voucher(
            rate_allocations=[{"gst_rate": "18", "sales_ledger": "GST Purchase 18%", "taxable_value": "7860.00"}],
            tax_allocations=[{"ledger": "Input CGST 9%", "amount": "707.40"},
                             {"ledger": "Input SGST 9%", "amount": "707.40"}],
            invoice_total="9274.80")
        xml = ET.fromstring(build_voucher(voucher, "SRI MAHALAKSHMI TRADERS,"))
        entry = next(e for e in xml.findall(".//LEDGERENTRIES.LIST") if e.findtext("LEDGERNAME") == "GST Purchase 18%")

        self.assertEqual(entry.findtext("RATEOFINVOICETAX"), "18")
        self.assertEqual(entry.findtext("BASICRATEOFINVOICETAX"), "18")
        self.assertEqual(entry.findtext("GSTTAXRATE"), "18")
        self.assertEqual(entry.findtext("AMOUNT"), "-7860.00")

    def test_first_of_multiple_taxable_rate_lines_also_carries_rate(self):
        voucher = _purchase_voucher(
            rate_allocations=[
                {"gst_rate": "5", "sales_ledger": "GST Purchase 5%", "taxable_value": "190.00"},
                {"gst_rate": "18", "sales_ledger": "GST Purchase 18%", "taxable_value": "5960.00"},
            ],
            tax_allocations=[
                {"ledger": "Input CGST 2.5%", "amount": "4.75"}, {"ledger": "Input SGST 2.5%", "amount": "4.75"},
                {"ledger": "Input CGST 9%", "amount": "536.40"}, {"ledger": "Input SGST 9%", "amount": "536.40"},
            ],
            invoice_total="7232.30")
        xml = ET.fromstring(build_voucher(voucher, "SRI MAHALAKSHMI TRADERS,"))
        entries = xml.findall(".//LEDGERENTRIES.LIST")
        # rate_allocations are written in ascending-rate order; "GST Purchase 5%" is first.
        first_taxable = entries[1]
        self.assertEqual(first_taxable.findtext("LEDGERNAME"), "GST Purchase 5%")
        self.assertEqual(first_taxable.findtext("RATEOFINVOICETAX"), "5")
        self.assertEqual(first_taxable.findtext("GSTTAXRATE"), "5")

        second_taxable = entries[2]
        self.assertEqual(second_taxable.findtext("LEDGERNAME"), "GST Purchase 18%")
        self.assertEqual(second_taxable.findtext("RATEOFINVOICETAX"), "18")
        self.assertEqual(second_taxable.findtext("GSTTAXRATE"), "18")

    def test_all_supported_rates_carry_their_own_rate(self):
        for rate in ("1", "3", "5", "12", "18", "28", "40"):
            with self.subTest(rate=rate):
                voucher = _purchase_voucher(
                    rate_allocations=[{"gst_rate": rate, "sales_ledger": f"GST Purchase {rate}%", "taxable_value": "1000.00"}],
                    tax_allocations=[], invoice_total="1000.00")
                xml = ET.fromstring(build_voucher(voucher, "SRI MAHALAKSHMI TRADERS,"))
                entry = next(e for e in xml.findall(".//LEDGERENTRIES.LIST") if e.findtext("LEDGERNAME") == f"GST Purchase {rate}%")
                self.assertEqual(entry.findtext("RATEOFINVOICETAX"), rate)
                self.assertEqual(entry.findtext("GSTTAXRATE"), rate)

    def test_rate_field_does_not_change_the_written_amount_or_total(self):
        """The fix is additive metadata only -- amounts/totals are untouched."""
        voucher = _purchase_voucher(
            rate_allocations=[{"gst_rate": "18", "sales_ledger": "GST Purchase 18%", "taxable_value": "7860.00"}],
            tax_allocations=[{"ledger": "Input CGST 9%", "amount": "707.40"}, {"ledger": "Input SGST 9%", "amount": "707.40"}],
            invoice_total="9274.80")
        xml = ET.fromstring(build_voucher(voucher, "SRI MAHALAKSHMI TRADERS,"))
        amounts = [Decimal(e.findtext("AMOUNT")) for e in xml.findall(".//LEDGERENTRIES.LIST")]
        self.assertEqual(sum(amounts), Decimal("0.00"))
        party_entry = next(e for e in xml.findall(".//LEDGERENTRIES.LIST") if e.findtext("ISPARTYLEDGER") == "Yes")
        self.assertEqual(party_entry.findtext("AMOUNT"), "9274.80")


class PartyMasterFieldTests(SimpleTestCase):
    """(2) Every field required for the Tally Party Details screen -- State,
    Country, GST Registration Type, GSTIN/UIN, Place of Supply -- must reach
    the actual master-write XML for a valid-GSTIN Purchase supplier."""

    def _yuvraj_master(self):
        return {"master_type": "Party", "name": "YUVRAJ FIREWORKS PRIVATE LIMITED", "group": "Sundry Creditors",
                "gstin": "33AAACY4945P1ZS", "state": "Tamil Nadu", "country": "India",
                "registration_type": "Regular", "place_of_supply": "Tamil Nadu",
                "pincode": "625011", "applicable_from": "2025-04-01"}

    def test_tamil_nadu_gstin_populates_all_required_fields_on_create(self):
        xml = ET.fromstring(build_master({**self._yuvraj_master(), "action": "Create"}))
        node = xml.find(".//LEDGER")

        self.assertEqual(node.findtext("COUNTRYNAME"), "India")
        self.assertEqual(node.findtext("COUNTRYOFRESIDENCE"), "India")
        self.assertEqual(node.findtext("GSTREGISTRATIONTYPE"), "Regular")
        reg = node.find("LEDGSTREGDETAILS.LIST")
        self.assertEqual(reg.findtext("STATE"), "Tamil Nadu")
        self.assertEqual(reg.findtext("PLACEOFSUPPLY"), "Tamil Nadu")
        self.assertEqual(reg.findtext("GSTIN"), "33AAACY4945P1ZS")
        self.assertEqual(reg.findtext("ISOTHTERRITORYASSESSEE"), "No")
        self.assertEqual(reg.findtext("CONSIDERPURCHASEFOREXPORT"), "No")
        self.assertEqual(reg.findtext("ISTRANSPORTER"), "No")
        self.assertEqual(reg.findtext("ISCOMMONPARTY"), "No")
        mailing = node.find("LEDMAILINGDETAILS.LIST")
        self.assertEqual(mailing.findtext("STATE"), "Tamil Nadu")
        self.assertEqual(mailing.findtext("COUNTRY"), "India")

    def test_tamil_nadu_gstin_populates_all_required_fields_on_alter(self):
        """The Alter path (used to repair an existing party) omits the legacy
        top-level scalars but must still carry every field through the dated
        LEDGSTREGDETAILS.LIST/LEDMAILINGDETAILS.LIST aggregates."""
        xml = ET.fromstring(build_master({**self._yuvraj_master(), "action": "Alter"}))
        node = xml.find(".//LEDGER")

        reg = node.find("LEDGSTREGDETAILS.LIST")
        child_tags = [child.tag for child in node]
        self.assertLess(child_tags.index("LEDGSTREGDETAILS.LIST"), child_tags.index("LEDMAILINGDETAILS.LIST"))
        self.assertEqual(node.findtext("PARTYGSTIN"), "33AAACY4945P1ZS")
        self.assertEqual(node.findtext("GSTREGISTRATIONNO"), "33AAACY4945P1ZS")
        self.assertEqual(node.findtext("LEDSTATENAME"), "Tamil Nadu")
        self.assertEqual(node.findtext("COUNTRYOFRESIDENCE"), "India")
        self.assertEqual(node.findtext("PINCODE"), "625011")
        self.assertEqual(node.findtext("GSTREGISTRATIONTYPE"), "Regular")
        self.assertEqual(reg.findtext("STATE"), "Tamil Nadu")
        self.assertEqual(reg.findtext("PLACEOFSUPPLY"), "Tamil Nadu")
        self.assertEqual(reg.findtext("GSTIN"), "33AAACY4945P1ZS")
        self.assertEqual(reg.findtext("GSTREGISTRATIONTYPE"), "Regular")
        self.assertEqual(reg.findtext("ISOTHTERRITORYASSESSEE"), "No")
        self.assertEqual(reg.findtext("CONSIDERPURCHASEFOREXPORT"), "No")
        self.assertEqual(reg.findtext("ISTRANSPORTER"), "No")
        self.assertEqual(reg.findtext("ISCOMMONPARTY"), "No")
        mailing = node.find("LEDMAILINGDETAILS.LIST")
        self.assertEqual(mailing.findtext("STATE"), "Tamil Nadu")
        self.assertEqual(mailing.findtext("COUNTRY"), "India")

    def test_gstin_written_is_the_actual_source_gstin_not_hardcoded(self):
        """Different suppliers, different GSTINs -- never one hardcoded value."""
        other = {**self._yuvraj_master(), "name": "AAKASH CONSTRUCTION", "gstin": "33ABFFA3666C1ZU",
                 "action": "Create"}
        xml = ET.fromstring(build_master(other))
        reg = xml.find(".//LEDGSTREGDETAILS.LIST")
        self.assertEqual(reg.findtext("GSTIN"), "33ABFFA3666C1ZU")
        self.assertNotEqual(reg.findtext("GSTIN"), "33AAACY4945P1ZS")

    def test_party_dated_details_start_at_financial_year_not_later_invoice_date(self):
        """A later invoice date must not make Tally hide party GST details from
        the currently open April period during import verification."""
        xml = ET.fromstring(build_master({**self._yuvraj_master(), "applicable_from": "2025-04-30"}))

        self.assertEqual(xml.findtext(".//LEDGSTREGDETAILS.LIST/APPLICABLEFROM"), "20250401")
        self.assertEqual(xml.findtext(".//LEDMAILINGDETAILS.LIST/APPLICABLEFROM"), "20250401")


class ExistingPartyRepairTests(SimpleTestCase):
    """(3) An existing party master with incomplete State/Country/GSTIN must
    be flagged invalid -- not silently accepted -- so the caller's existing
    match-by-name repair path (service.py's Alter branch) updates the same
    ledger instead of leaving it wrong or creating a duplicate."""

    def test_existing_party_missing_state_country_gstin_fails_verification(self):
        master = {"master_type": "Party", "gstin": "33AAACY4945P1ZS", "state": "Tamil Nadu",
                  "registration_type": "Regular", "place_of_supply": "Tamil Nadu"}
        actual = {"exists": True, "name": "YUVRAJ FIREWORKS PRIVATE LIMITED", "gstin": "",
                 "state": "", "country": "", "registration_type": "", "place_of_supply": ""}

        result = _verify_master_properties(master, actual)

        self.assertFalse(result["valid"])
        self.assertIn("GSTIN", result["reason"])

    def test_existing_party_with_correct_fields_passes_verification(self):
        master = {"master_type": "Party", "gstin": "33AAACY4945P1ZS", "state": "Tamil Nadu",
                  "registration_type": "Regular", "place_of_supply": "Tamil Nadu"}
        actual = {"exists": True, "name": "YUVRAJ FIREWORKS PRIVATE LIMITED", "gstin": "33AAACY4945P1ZS",
                 "state": "Tamil Nadu", "country": "India", "registration_type": "Regular",
                 "place_of_supply": "Tamil Nadu"}

        result = _verify_master_properties(master, actual)

        self.assertTrue(result["valid"], result["reason"])

    def test_wrong_gstin_on_the_existing_ledger_fails_verification(self):
        """Guards against silently accepting an existing ledger under a
        different party's GSTIN -- must never pass as if it were correct."""
        master = {"master_type": "Party", "gstin": "33AAACY4945P1ZS", "state": "Tamil Nadu"}
        actual = {"exists": True, "gstin": "33WRONG0000X1Z1", "state": "Tamil Nadu",
                 "country": "India", "registration_type": "Regular"}

        result = _verify_master_properties(master, actual)

        self.assertFalse(result["valid"])
        self.assertIn("GSTIN", result["reason"])
