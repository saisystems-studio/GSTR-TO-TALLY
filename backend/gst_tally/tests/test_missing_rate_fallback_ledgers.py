"""Three-path account/tax ledger selection, additive to the existing
rate-wise flow:

  PATH 1 (unchanged): a real GST rate is present -> existing rate-wise
      "GST Sales 18%"/"GST Purchase 5%" ledgers and per-rate "Input CGST 9%"
      style tax ledgers.
  PATH 2 (new): the source genuinely has no GST rate at all (never merely a
      0% rate) -> the common, non-rate-suffixed "GST Sales"/"GST Purchase"
      account ledger, and fixed Duties & Taxes tax ledgers ("GST CGST" /
      "GST Input CGST" etc.) keyed by the actual nonzero tax component
      amounts, never a derived/assumed rate.
  PATH 3 (renamed, same semantics): a source-identified exempt supply ->
      "GST Sales Exempted" / "GST Purchase Exempted" (previously the single
      shared "GST Exempted" name).
"""
from decimal import Decimal
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from gst_tally.tally.json_master_builder import TALLY_ANY, build_json_master
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.master_builder import masters_for

GSTIN = "33AAACB2894G1ZJ"


def _row(tax_percent, cgst="0", sgst="0", igst="0", cess="0", taxability_hint=""):
    return SimpleNamespace(
        id=1, invoice_no="INV-1", invoice_date=date(2025, 4, 1), customer_gstin=GSTIN,
        taxable_value=Decimal("10000.00"), tax_percent=tax_percent,
        cgst=Decimal(cgst), sgst=Decimal(sgst), igst=Decimal(igst), cess=Decimal(cess),
        invoice_value=Decimal("10000.00") + Decimal(cgst) + Decimal(sgst) + Decimal(igst) + Decimal(cess),
        place_of_supply="33", state_code="33", reverse_charge="", invoice_type="",
        filing_period="", source_line={"source_row_number": 2, "Taxability": taxability_hint},
        supply_type="", item_name="", description="", hsn_sac="", quantity=None, unit="",
        rate=None, discount=Decimal("0.00"), other_charges=Decimal("0.00"), round_off=Decimal("0.00"),
    )


class _FakeInvoiceQuery:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, _field):
        return self.rows

    def values_list(self, field, flat=False):
        return [getattr(row, field) for row in self.rows]

    def exclude(self, **_kwargs):
        return self

    def exists(self):
        return True


def _voucher(return_type, row):
    batch = SimpleNamespace(
        gst_return_type=return_type, invoices=_FakeInvoiceQuery([row]),
        source_parties={GSTIN: {"party_name": "ABC", "trade_name": "ABC", "state": "Tamil Nadu"}},
        company_details={},
    )
    with patch("gst_tally.models.GSTParty.objects.filter", return_value=[]), \
         patch("gst_tally.models.GSTLedgerMapping.objects.filter", return_value=[]):
        return normalized_vouchers(batch, {"state": "Tamil Nadu"})[0]


class ExistingRateWisePathIsUnchangedTests(SimpleTestCase):
    """Acceptance tests 1-2: PATH 1 must be untouched by this addition."""

    def test_gstr1_taxable_18_percent_uses_existing_rate_wise_sales_ledger(self):
        voucher = _voucher("GSTR1", _row(Decimal("18"), cgst="900", sgst="900"))
        allocation = voucher["rate_allocations"][0]

        self.assertEqual(allocation["account_ledger"], "GST Sales 18%")
        self.assertTrue(allocation["rate_available"])
        self.assertEqual({t["ledger"] for t in voucher["tax_allocations"]}, {"Output CGST 9%", "Output SGST 9%"})

    def test_gstr2b_taxable_5_percent_uses_existing_rate_wise_purchase_ledger(self):
        voucher = _voucher("GSTR2B", _row(Decimal("5"), cgst="250", sgst="250"))
        allocation = voucher["rate_allocations"][0]

        self.assertEqual(allocation["account_ledger"], "GST Purchase 5%")
        self.assertTrue(allocation["rate_available"])
        self.assertEqual({t["ledger"] for t in voucher["tax_allocations"]}, {"Input CGST 2.5%", "Input SGST 2.5%"})


class MissingRateFallbackTests(SimpleTestCase):
    """Acceptance tests 3-4, 7, 9: PATH 2 (rate genuinely unavailable)."""

    def test_gstr1_no_rate_column_but_cgst_sgst_present_uses_common_sales_and_flat_tax_ledgers(self):
        voucher = _voucher("GSTR1", _row(None, cgst="4500", sgst="4500"))
        allocation = voucher["rate_allocations"][0]

        self.assertEqual(allocation["account_ledger"], "GST Sales")
        self.assertFalse(allocation["rate_available"])
        self.assertEqual({t["ledger"] for t in voucher["tax_allocations"]}, {"GST CGST", "GST SGST/UTGST"})
        self.assertEqual(next(t["amount"] for t in voucher["tax_allocations"] if t["ledger"] == "GST CGST"), "4500.00")

    def test_gstr2a_no_rate_column_but_igst_present_uses_common_purchase_and_flat_input_igst(self):
        voucher = _voucher("GSTR2A", _row(None, igst="9000"))
        allocation = voucher["rate_allocations"][0]

        self.assertEqual(allocation["account_ledger"], "GST Purchase")
        self.assertFalse(allocation["rate_available"])
        self.assertEqual({t["ledger"] for t in voucher["tax_allocations"]}, {"GST Input IGST"})
        self.assertEqual(voucher["tax_allocations"][0]["amount"], "9000.00")

    def test_missing_rate_never_infers_a_percentage(self):
        """A 10000/900/900 split looks like 18% but the rate column is
        genuinely absent -- the ledger names must never encode an inferred
        18%, only the fixed common/flat names."""
        voucher = _voucher("GSTR2B", _row(None, cgst="900", sgst="900"))
        names = {voucher["rate_allocations"][0]["account_ledger"]} | {t["ledger"] for t in voucher["tax_allocations"]}

        self.assertFalse(any("18" in name or "%" in name for name in names))
        self.assertEqual(names, {"GST Purchase", "GST Input CGST", "GST Input SGST/UTGST"})

    def test_missing_rate_and_missing_tax_amounts_fabricates_nothing(self):
        voucher = _voucher("GSTR1", _row(None))
        allocation = voucher["rate_allocations"][0]

        self.assertEqual(allocation["account_ledger"], "GST Sales")
        self.assertEqual(voucher["tax_allocations"], [])

    def test_missing_rate_with_nonzero_cess_uses_gst_cess_not_cess(self):
        voucher = _voucher("GSTR1", _row(None, cess="500"))
        self.assertEqual(voucher["cess_ledger"], "GST Cess")

    def test_missing_rate_purchase_with_nonzero_cess_uses_gst_input_cess(self):
        voucher = _voucher("GSTR2B", _row(None, cess="500"))
        self.assertEqual(voucher["cess_ledger"], "GST Input Cess")

    def test_rate_available_voucher_keeps_plain_cess_ledger_name(self):
        voucher = _voucher("GSTR1", _row(Decimal("18"), cgst="900", sgst="900", cess="50"))
        self.assertEqual(voucher["cess_ledger"], "Cess")

    def test_tax_type_values_are_tally_dropdown_values_not_portal_labels(self):
        voucher = _voucher("GSTR2A", _row(None, cgst="450", sgst="450"))
        tax_types = {t["tax_type"] for t in voucher["tax_allocations"]}
        self.assertEqual(tax_types, {"CGST", "SGST/UTGST"})
        self.assertNotIn("Central Tax", tax_types)
        self.assertNotIn("State Tax", tax_types)


class ExemptPathIsDirectionalTests(SimpleTestCase):
    """Acceptance tests 5-6: PATH 3, renamed to be return-type-directional."""

    def test_gstr1_exempt_invoice_uses_gst_sales_exempted(self):
        voucher = _voucher("GSTR1", _row(Decimal("0"), taxability_hint="Exempt"))
        allocation = voucher["rate_allocations"][0]
        self.assertEqual(allocation["account_ledger"], "GST Sales Exempted")
        self.assertEqual(allocation["taxability"], "Exempt")

    def test_gstr2b_exempt_invoice_uses_gst_purchase_exempted(self):
        voucher = _voucher("GSTR2B", _row(Decimal("0"), taxability_hint="Exempt"))
        allocation = voucher["rate_allocations"][0]
        self.assertEqual(allocation["account_ledger"], "GST Purchase Exempted")
        self.assertEqual(allocation["taxability"], "Exempt")

    def test_exempt_takes_priority_over_missing_rate(self):
        # Rate genuinely unavailable AND exempt -- exempt must win, never the
        # common "GST Sales"/"GST Purchase" fallback.
        voucher = _voucher("GSTR1", _row(None, taxability_hint="Exempt"))
        self.assertEqual(voucher["rate_allocations"][0]["account_ledger"], "GST Sales Exempted")


class MasterCreationMatrixTests(SimpleTestCase):
    """Acceptance test 8-ish: masters_for() must require exactly the common
    fallback masters used, and dedupe across many invoices."""

    def test_masters_for_gstr1_missing_rate_batch_includes_the_documented_common_masters(self):
        voucher = _voucher("GSTR1", _row(None, cgst="4500", sgst="4500", cess="100"))
        names = {row["name"] for row in masters_for([voucher])}

        self.assertIn("GST Sales", names)
        self.assertIn("GST CGST", names)
        self.assertIn("GST SGST/UTGST", names)
        self.assertIn("GST Cess", names)
        self.assertNotIn("GST IGST", names)  # never required unless actually used

    def test_masters_for_gstr2a_missing_rate_batch_includes_input_side_masters(self):
        voucher = _voucher("GSTR2A", _row(None, igst="9000", cess="100"))
        names = {row["name"] for row in masters_for([voucher])}

        self.assertIn("GST Purchase", names)
        self.assertIn("GST Input IGST", names)
        self.assertIn("GST Input Cess", names)

    def test_many_missing_rate_invoices_still_produce_one_common_ledger_each(self):
        rows = [_row(None, cgst="90", sgst="90") for _ in range(20)]
        voucher = _voucher("GSTR2B", rows[0])
        # masters_for dedupes by (master_type, name) across the whole batch;
        # simulate a 20-invoice batch by repeating the same resolved voucher
        # shape (mirrors how the existing "many invoices, one rate" tests in
        # test_cess_zero_master.py exercise the same dedup path).
        masters = masters_for([voucher] * 20)
        purchase_names = [row["name"] for row in masters if row["master_type"] == "Purchase" and row["name"] == "GST Purchase"]
        cgst_names = [row["name"] for row in masters if row["name"] == "GST Input CGST"]

        self.assertEqual(len(purchase_names), 1)
        self.assertEqual(len(cgst_names), 1)


class CommonLedgerNativeJsonShapeTests(SimpleTestCase):
    def test_gst_sales_common_ledger_inherits_from_company_group(self):
        voucher = _voucher("GSTR1", _row(None, cgst="4500", sgst="4500"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST Sales")
        message = build_json_master(master, "Company D")["tallymessage"][0]
        details = message["gstdetails"][0]

        self.assertEqual(message["parent"], "Sales Accounts")
        self.assertEqual(details["srcofgstdetails"], "As per Company/Group")
        # "As per Company/Group" means no ledger-level override -- Tally
        # itself exposes no Taxability Type in this mode, so it must not be
        # force-sent as "Taxable".
        self.assertNotIn("taxability", details)
        self.assertNotIn("supplytype", details)
        self.assertEqual(message["rateoftaxcalculation"], " 0")

    def test_gst_purchase_common_ledger_inherits_from_company_group(self):
        voucher = _voucher("GSTR2A", _row(None, igst="9000"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST Purchase")
        message = build_json_master(master, "Company D")["tallymessage"][0]

        self.assertEqual(message["parent"], "Purchase Accounts")
        self.assertEqual(message["gstdetails"][0]["srcofgstdetails"], "As per Company/Group")

    def test_gst_sales_exempted_keeps_specify_details_here_and_exempt_taxability(self):
        voucher = _voucher("GSTR1", _row(Decimal("0"), taxability_hint="Exempt"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST Sales Exempted")
        message = build_json_master(master, "Company D")["tallymessage"][0]
        details = message["gstdetails"][0]

        self.assertEqual(details["srcofgstdetails"], "Specify Details Here")
        self.assertEqual(details["taxability"], "Exempt")
        self.assertEqual(details["supplytype"], "Goods")

    def test_rate_wise_ledger_is_completely_unaffected(self):
        voucher = _voucher("GSTR1", _row(Decimal("18"), cgst="900", sgst="900"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST Sales 18%")
        message = build_json_master(master, "Company D")["tallymessage"][0]
        details = message["gstdetails"][0]

        self.assertEqual(details["srcofgstdetails"], "Specify Details Here")
        self.assertEqual(details["taxability"], "Taxable")
        self.assertEqual(message["rateoftaxcalculation"], " 18")


class CommonTaxLedgerNativeJsonShapeTests(SimpleTestCase):
    def test_gst_cgst_output_ledger_shape(self):
        voucher = _voucher("GSTR1", _row(None, cgst="4500", sgst="4500"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST CGST")
        message = build_json_master(master, "Company D")["tallymessage"][0]

        self.assertEqual(message["parent"], "Duties & Taxes")
        self.assertEqual(message["taxtype"], "GST")
        self.assertEqual(message["gstdutyhead"], "CGST")
        self.assertEqual(message["rateoftaxcalculation"], " 0")

    def test_gst_sgst_utgst_output_ledger_uses_the_slashed_duty_head(self):
        voucher = _voucher("GSTR1", _row(None, cgst="4500", sgst="4500"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST SGST/UTGST")
        message = build_json_master(master, "Company D")["tallymessage"][0]
        self.assertEqual(message["gstdutyhead"], "SGST/UTGST")

    def test_gst_cess_ledger_carries_any_valuation_type(self):
        voucher = _voucher("GSTR1", _row(None, cess="500"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST Cess")
        message = build_json_master(master, "Company D")["tallymessage"][0]

        self.assertEqual(message["gstdutyhead"], "Cess")
        self.assertEqual(message["valuationtype"], TALLY_ANY)

    def test_gst_input_igst_purchase_ledger_shape(self):
        voucher = _voucher("GSTR2A", _row(None, igst="9000"))
        master = next(row for row in masters_for([voucher]) if row["name"] == "GST Input IGST")
        message = build_json_master(master, "Company D")["tallymessage"][0]

        self.assertEqual(message["parent"], "Duties & Taxes")
        self.assertEqual(message["gstdutyhead"], "IGST")
        self.assertEqual(message["rateoftaxcalculation"], " 0")
