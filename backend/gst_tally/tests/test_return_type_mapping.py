from decimal import Decimal
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.json_master_builder import TALLY_APPLICABLE, TALLY_ANY, build_json_master
from gst_tally.tally.json_voucher_builder import build_json_voucher
from gst_tally.tally.master_builder import build_master, masters_for
from gst_tally.tally.return_mapping import SUPPORTED_RATES, get_tally_mapping
from gst_tally.tally.voucher_builder import build_voucher


GSTIN = "33AAACB2894G1ZJ"


def mapped_voucher(return_type, interstate=False, rate="18"):
    mapping = get_tally_mapping(return_type)
    tax = Decimal("1000") * Decimal(rate) / Decimal("100")
    component = "IGST" if interstate else "CGST"
    taxes = ([{"ledger": mapping.tax_ledger("IGST", rate), "component": "IGST", "amount": str(tax), "tax_type": "Integrated Tax"}]
             if interstate else [
                 {"ledger": mapping.tax_ledger("CGST", rate), "component": "CGST", "amount": str(tax / 2), "tax_type": "Central Tax"},
                 {"ledger": mapping.tax_ledger("SGST", rate), "component": "SGST", "amount": str(tax / 2), "tax_type": "State Tax"},
             ])
    account = mapping.account_ledger(rate)
    return {
        "invoice_number": "INV-1", "invoice_date": "2025-04-25", "invoice_total": str(Decimal("1000") + tax),
        "return_type": mapping.return_type, "direction": mapping.direction, "voucher_type": mapping.voucher_type,
        "party_group": mapping.party_group, "account_group": mapping.account_group,
        "party": {"name": "ABC", "gstin": GSTIN}, "place_of_supply": "Tamil Nadu",
        "items": [{"gst_rate": rate, "taxable_value": "1000", "sales_ledger": account,
                   "account_ledger": account, "supply_type": "Goods"}],
        "rate_allocations": [{"gst_rate": rate, "taxable_value": "1000", "sales_ledger": account,
                              "account_ledger": account}],
        "tax_allocations": taxes, "taxable_total": "1000",
        "cgst": "0" if interstate else str(tax / 2), "sgst": "0" if interstate else str(tax / 2),
        "igst": str(tax) if interstate else "0", "cess": "0", "other_charges": "0", "rounding_adjustment": "0",
    }


def exempt_voucher(return_type, taxable_value="1000"):
    value = mapped_voucher(return_type, False, "18")
    value["invoice_total"] = taxable_value
    value["taxable_total"] = taxable_value
    value["cgst"] = "0"
    value["sgst"] = "0"
    value["igst"] = "0"
    value["items"] = [{
        "gst_rate": "0",
        "taxable_value": taxable_value,
        "sales_ledger": "GST Exempted",
        "account_ledger": "GST Exempted",
        "supply_type": "Goods",
        "taxability": "Exempt",
    }]
    value["rate_allocations"] = [{
        "gst_rate": "0",
        "taxable_value": taxable_value,
        "sales_ledger": "GST Exempted",
        "account_ledger": "GST Exempted",
        "taxability": "Exempt",
    }]
    value["tax_allocations"] = []
    return value


class ReturnTypeMappingTests(SimpleTestCase):
    def assert_flow(self, return_type, interstate, party_group, account, expected_taxes):
        value = mapped_voucher(return_type, interstate)
        masters = masters_for([value])
        names = {row["name"]: row for row in masters}
        self.assertEqual(names["ABC"]["group"], party_group)
        self.assertEqual(names[account]["group"], "Sales Accounts" if return_type == "GSTR-1" else "Purchase Accounts")
        self.assertEqual({row["name"] for row in masters if row["master_type"] == "Tax"}, set(expected_taxes))
        xml = ET.fromstring(build_voucher(value, "Company D"))
        self.assertEqual(xml.findtext(".//VOUCHERTYPENAME"), "Sales" if return_type == "GSTR-1" else "Purchase")
        payload = build_json_voucher(value, "Company D")["tallymessage"][0]
        self.assertEqual(payload["vouchertypename"], "Sales" if return_type == "GSTR-1" else "Purchase")

    def test_gstr1_18_intra(self):
        self.assert_flow("GSTR-1", False, "Sundry Debtors", "GST Sales 18%", {"Output CGST 9%", "Output SGST 9%"})

    def test_gstr1_18_inter(self):
        self.assert_flow("GSTR-1", True, "Sundry Debtors", "GST Sales 18%", {"Output IGST 18%"})

    def test_gstr2b_18_intra(self):
        self.assert_flow("GSTR-2B", False, "Sundry Creditors", "GST Purchase 18%", {"Input CGST 9%", "Input SGST 9%"})

    def test_gstr2a_18_inter(self):
        self.assert_flow("GSTR-2A", True, "Sundry Creditors", "GST Purchase 18%", {"Input IGST 18%"})

    def test_gstr2b_5_intra_uses_only_required_purchase_and_input_ledgers(self):
        value = mapped_voucher("GSTR2B", False, "5")
        names = {row["name"] for row in masters_for([value]) if row["master_type"] != "Party"}
        purchase = {row["name"] for row in masters_for([value]) if row["master_type"] == "Purchase"}
        taxes = {row["name"] for row in masters_for([value]) if row["master_type"] == "Tax"}

        # Only the 5% Purchase account ledger this batch actually uses is
        # required -- not every SUPPORTED_RATES combination.
        self.assertEqual(purchase, {"GST Purchase 5%"})
        self.assertEqual(taxes, {"Input CGST 2.5%", "Input SGST 2.5%"})
        self.assertEqual(names, purchase | taxes)

    def test_gstr2b_18_interstate_uses_purchase_and_input_igst_only(self):
        value = mapped_voucher("GSTR2B", True, "18")
        purchase = {row["name"] for row in masters_for([value]) if row["master_type"] == "Purchase"}
        taxes = {row["name"] for row in masters_for([value]) if row["master_type"] == "Tax"}

        self.assertEqual(purchase, {"GST Purchase 18%"})
        self.assertEqual(taxes, {"Input IGST 18%"})

    def test_unknown_or_blank_return_type_never_falls_back_to_sales(self):
        for value in ("", "UNKNOWN", "GSTR-2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                get_tally_mapping(value)

    def test_all_supported_rates(self):
        for return_type, prefix in (("GSTR-1", "GST Sales"), ("GSTR-2A", "GST Purchase"), ("GSTR-2B", "GST Purchase")):
            mapping = get_tally_mapping(return_type)
            self.assertEqual([mapping.account_ledger(rate) for rate in SUPPORTED_RATES],
                             [f"{prefix} {rate}%" for rate in (1, 3, 5, 12, 18, 28, 40)])

    def test_gstr1_creates_only_the_sales_account_master_actually_used(self):
        value = mapped_voucher("GSTR-1", False, "18")

        names = {row["name"] for row in masters_for([value]) if row["master_type"] == "Sales"}

        # Master creation is data-driven: a batch that only uses 18% must
        # never also create GST Sales 1/3/5/12/28/40% ledgers.
        self.assertEqual(names, {"GST Sales 18%"})

    def test_gstr2b_creates_only_the_purchase_account_master_actually_used(self):
        value = mapped_voucher("GSTR-2B", False, "18")

        names = {row["name"] for row in masters_for([value]) if row["master_type"] == "Purchase"}

        self.assertEqual(names, {"GST Purchase 18%"})

    def test_multiple_invoices_at_different_rates_create_exactly_those_rates(self):
        values = [mapped_voucher("GSTR-2B", False, rate) for rate in ("5", "18")]
        purchase = {row["name"] for row in masters_for(values) if row["master_type"] == "Purchase"}

        self.assertEqual(purchase, {"GST Purchase 5%", "GST Purchase 18%"})

    def test_gstr2b_3_intra_keeps_purchase_master_full_rate_and_splits_input_tax(self):
        value = mapped_voucher("GSTR-2B", False, "3")
        purchase = {row["name"] for row in masters_for([value]) if row["master_type"] == "Purchase"}
        taxes = {row["name"] for row in masters_for([value]) if row["master_type"] == "Tax"}

        self.assertIn("GST Purchase 3%", purchase)
        self.assertEqual(taxes, {"Input CGST 1.5%", "Input SGST 1.5%"})

    def test_gstr2a_3_interstate_keeps_purchase_master_full_rate_and_uses_input_igst(self):
        value = mapped_voucher("GSTR-2A", True, "3")
        purchase = {row["name"] for row in masters_for([value]) if row["master_type"] == "Purchase"}
        taxes = {row["name"] for row in masters_for([value]) if row["master_type"] == "Tax"}

        self.assertIn("GST Purchase 3%", purchase)
        self.assertEqual(taxes, {"Input IGST 3%"})

    def test_gstr1_3_intra_keeps_sales_master_full_rate_and_splits_output_tax(self):
        value = mapped_voucher("GSTR-1", False, "3")
        sales = {row["name"] for row in masters_for([value]) if row["master_type"] == "Sales"}
        taxes = {row["name"] for row in masters_for([value]) if row["master_type"] == "Tax"}

        self.assertIn("GST Sales 3%", sales)
        self.assertEqual(taxes, {"Output CGST 1.5%", "Output SGST 1.5%"})

    def test_purchase_json_has_credit_party_and_debit_purchase_and_input_tax(self):
        entries = build_json_voucher(mapped_voucher("GSTR-2B"), "Company D")["tallymessage"][0]["ledgerentries"]
        amounts = {row["ledgername"]: Decimal(row["amount"]) for row in entries}
        self.assertGreater(amounts["ABC"], 0)
        self.assertLess(amounts["GST Purchase 18%"], 0)
        self.assertLess(amounts["Input CGST 9%"], 0)
        self.assertLess(amounts["Input SGST 9%"], 0)

    def test_gstr1_exempt_master_is_sales_account_with_exempt_zero_rate(self):
        master = next(row for row in masters_for([exempt_voucher("GSTR-1")]) if row["name"] == "GST Exempted")
        message = build_json_master(master, "Company D")["tallymessage"][0]
        details = message["gstdetails"][0]
        rates = {row["gstratedutyhead"]: row["gstrate"]
                 for row in details["statewisedetails"][0]["ratedetails"]}

        self.assertEqual(master["master_type"], "Sales")
        self.assertEqual(master["group"], "Sales Accounts")
        self.assertEqual(master["gst_rate"], "0")
        self.assertEqual(master["taxability"], "Exempt")
        self.assertEqual(message["name"], "GST Exempted")
        self.assertEqual(message["parent"], "Sales Accounts")
        self.assertEqual(message["gstapplicable"], TALLY_APPLICABLE)
        self.assertEqual(message["gsttypeofsupply"], "Goods")
        self.assertEqual(message["rateoftaxcalculation"], " 0")
        self.assertEqual(details["srcofgstdetails"], "Specify Details Here")
        self.assertEqual(details["taxability"], "Exempt")
        self.assertEqual(details["supplytype"], "Goods")
        self.assertEqual(details["statewisedetails"][0]["statename"], TALLY_ANY)
        self.assertEqual({head: rates[head] for head in ("CGST", "SGST/UTGST", "IGST")}, {
            "CGST": " 0", "SGST/UTGST": " 0", "IGST": " 0",
        })

    def test_gstr2a_exempt_master_is_purchase_account_with_exempt_zero_rate(self):
        master = next(row for row in masters_for([exempt_voucher("GSTR-2A")]) if row["name"] == "GST Exempted")
        ledger = ET.fromstring(build_master(master)).find(".//LEDGER")

        self.assertEqual(master["master_type"], "Purchase")
        self.assertEqual(master["group"], "Purchase Accounts")
        self.assertEqual(ledger.findtext("NAME"), "GST Exempted")
        self.assertEqual(ledger.findtext("PARENT"), "Purchase Accounts")
        self.assertEqual(ledger.findtext("GSTAPPLICABLE"), "Applicable")
        self.assertEqual(ledger.findtext("GSTTYPEOFSUPPLY"), "Goods")
        self.assertEqual(ledger.findtext("RATEOFTAXCALCULATION"), "0")
        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/TAXABILITY"), "Exempt")
        self.assertEqual(ledger.findtext(".//GSTDETAILS.LIST/SRCOFGSTDETAILS"), "Specify Details Here")
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                 for row in ledger.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}
        self.assertEqual({head: rates[head] for head in ("CGST", "SGST/UTGST", "IGST")}, {
            "CGST": "0", "SGST/UTGST": "0", "IGST": "0",
        })

    def test_gstr2b_exempt_master_is_purchase_account_with_exempt_zero_rate(self):
        master = next(row for row in masters_for([exempt_voucher("GSTR-2B")]) if row["name"] == "GST Exempted")
        message = build_json_master(master, "Company D")["tallymessage"][0]

        self.assertEqual(master["master_type"], "Purchase")
        self.assertEqual(master["group"], "Purchase Accounts")
        self.assertEqual(message["name"], "GST Exempted")
        self.assertEqual(message["parent"], "Purchase Accounts")
        self.assertEqual(message["gstapplicable"], TALLY_APPLICABLE)
        self.assertEqual(message["gstdetails"][0]["taxability"], "Exempt")
        self.assertEqual(message["gstdetails"][0]["supplytype"], "Goods")
        self.assertEqual(message["rateoftaxcalculation"], " 0")

    def test_existing_taxable_purchase_ledger_stays_taxable_at_18_percent(self):
        master = next(row for row in masters_for([mapped_voucher("GSTR-2B", False, "18")])
                      if row["name"] == "GST Purchase 18%")
        message = build_json_master(master, "Company D")["tallymessage"][0]

        self.assertEqual(master["taxability"], "Taxable")
        self.assertEqual(message["gstdetails"][0]["taxability"], "Taxable")
        self.assertEqual(message["rateoftaxcalculation"], " 18")

    def test_exempt_voucher_posts_only_party_and_gst_exempted_without_tax_ledgers(self):
        xml = ET.fromstring(build_voucher(exempt_voucher("GSTR-2B", "5000"), "Company D"))
        amounts = {entry.findtext("LEDGERNAME"): entry.findtext("AMOUNT")
                   for entry in xml.findall(".//LEDGERENTRIES.LIST")}

        self.assertEqual(amounts, {"ABC": "5000.00", "GST Exempted": "-5000.00"})
        self.assertFalse(any(name.startswith(("Input CGST", "Input SGST", "Input IGST", "Output CGST", "Output SGST", "Output IGST"))
                             for name in amounts))

    def test_mixed_exempt_and_18_percent_posts_tax_only_for_taxable_allocation(self):
        value = mapped_voucher("GSTR-2B", False, "18")
        value["invoice_total"] = "6900"
        value["taxable_total"] = "6000"
        value["items"].append({
            "gst_rate": "0",
            "taxable_value": "1000",
            "sales_ledger": "GST Exempted",
            "account_ledger": "GST Exempted",
            "supply_type": "Goods",
            "taxability": "Exempt",
        })
        value["rate_allocations"].append({
            "gst_rate": "0",
            "taxable_value": "1000",
            "sales_ledger": "GST Exempted",
            "account_ledger": "GST Exempted",
            "taxability": "Exempt",
        })

        xml = ET.fromstring(build_voucher(value, "Company D"))
        amounts = {entry.findtext("LEDGERNAME"): Decimal(entry.findtext("AMOUNT"))
                   for entry in xml.findall(".//LEDGERENTRIES.LIST")}

        self.assertEqual(amounts["ABC"], Decimal("6900.00"))
        self.assertEqual(amounts["GST Exempted"], Decimal("-1000.00"))
        self.assertEqual(amounts["GST Purchase 18%"], Decimal("-1000.00"))
        self.assertEqual(amounts["Input CGST 9%"], Decimal("-90.00"))
        self.assertEqual(amounts["Input SGST 9%"], Decimal("-90.00"))
        self.assertNotIn("Input CGST 0%", amounts)
        self.assertNotIn("Input SGST 0%", amounts)


class _FakeInvoiceQuery:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, _field):
        return self.rows

    def values_list(self, field, flat=False):
        return [getattr(row, field) for row in self.rows]


class ExemptSourceNormalizationTests(SimpleTestCase):
    def test_source_exempt_row_normalizes_to_gst_purchase_exempted_without_tax_allocations(self):
        row = SimpleNamespace(
            id=1,
            invoice_no="EX-1",
            invoice_date=date(2025, 4, 1),
            customer_gstin=GSTIN,
            taxable_value=Decimal("5000.00"),
            tax_percent=Decimal("0.000"),
            cgst=Decimal("0.00"),
            sgst=Decimal("0.00"),
            igst=Decimal("0.00"),
            cess=Decimal("0.00"),
            invoice_value=Decimal("5000.00"),
            place_of_supply="33",
            state_code="33",
            reverse_charge="",
            invoice_type="Exempt",
            filing_period="",
            source_line={"source_row_number": 2, "Taxability": "Exempt"},
            supply_type="",
            item_name="",
            description="",
            hsn_sac="",
            quantity=None,
            unit="",
            rate=None,
            discount=Decimal("0.00"),
            other_charges=Decimal("0.00"),
            round_off=Decimal("0.00"),
        )
        batch = SimpleNamespace(
            gst_return_type="GSTR2B",
            invoices=_FakeInvoiceQuery([row]),
            source_parties={GSTIN: {"party_name": "ABC", "trade_name": "ABC", "state": "Tamil Nadu"}},
            company_details={},
        )

        with patch("gst_tally.models.GSTParty.objects.filter", return_value=[]), \
             patch("gst_tally.models.GSTLedgerMapping.objects.filter", return_value=[]):
            voucher = normalized_vouchers(batch, {"state": "Tamil Nadu"})[0]
        allocation = voucher["rate_allocations"][0]

        self.assertEqual(voucher["voucher_type"], "Purchase")
        self.assertEqual(allocation["account_ledger"], "GST Purchase Exempted")
        self.assertEqual(allocation["sales_ledger"], "GST Purchase Exempted")
        self.assertEqual(allocation["gst_rate"], "0")
        self.assertEqual(allocation["taxability"], "Exempt")
        self.assertEqual(allocation["taxable_value"], "5000.00")
        self.assertEqual(voucher["tax_allocations"], [])
