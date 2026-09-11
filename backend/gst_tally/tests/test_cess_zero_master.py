"""Regression coverage: masters_for() must never invent a "Cess Zero"
account ledger merely because the source file carries a Cess column (even
when every value in it is 0/absent). A zero or missing cess amount on a
voucher means no cess ledger at all is required for it -- only a genuinely
nonzero voucher["cess"] adds the real Duties & Taxes Cess/Input Cess tax
ledger. Required masters must trace back to actual eligible voucher data,
never to a batch/file-level signal.

The "Cess Zero" concept has been removed entirely -- masters_for() no
longer has any code path that can produce it, and the now-unreachable
resolve_cess_zero_alias() reconciliation helper (previously kept around for
a live Tally company with a pre-existing "Cess Zero"-style ledger) has been
deleted along with its call sites in service.py, per the task's MASTER
CLEANUP instruction.
"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.master_builder import masters_for
from gst_tally.tally.return_mapping import get_tally_mapping
from gst_tally.tally.service import _verify_master_properties, prepare

GSTIN = "33AAACB2894G1ZJ"
COMPANY_GSTIN = "33AFHPM6103Q1Z8"
COMPANY = "Test Company"
CESS_ZERO_MASTER_NAME = "Cess Zero"


def _voucher(return_type, rate="18", cess="0"):
    mapping = get_tally_mapping(return_type)
    account = mapping.account_ledger(rate)
    return {
        "invoice_date": "2025-04-25", "voucher_type": mapping.voucher_type,
        "account_group": mapping.account_group, "party": {"name": "ABC", "gstin": GSTIN},
        "party_group": mapping.party_group,
        "items": [{"gst_rate": rate, "taxable_value": "1000", "sales_ledger": account,
                   "account_ledger": account, "supply_type": "Goods"}],
        "tax_allocations": [], "cess": cess, "other_charges": "0", "rounding_adjustment": "0",
    }


class MastersForNeverAddsCessZeroTests(SimpleTestCase):
    """masters_for() must never require "Cess Zero", regardless of the
    return type or the (now-removed) cess_field_detected signal."""

    def test_gstr1_never_requires_cess_zero(self):
        masters = masters_for([_voucher("GSTR-1")], "GSTR-1")
        self.assertFalse(any(row["name"] == CESS_ZERO_MASTER_NAME for row in masters))

    def test_gstr2a_never_requires_cess_zero(self):
        masters = masters_for([_voucher("GSTR-2A")], "GSTR-2A")
        self.assertFalse(any(row["name"] == CESS_ZERO_MASTER_NAME for row in masters))

    def test_gstr2b_never_requires_cess_zero(self):
        masters = masters_for([_voucher("GSTR-2B")], "GSTR-2B")
        self.assertFalse(any(row["name"] == CESS_ZERO_MASTER_NAME for row in masters))

    def test_zero_cess_on_a_voucher_requires_no_cess_ledger_at_all(self):
        # cess="0" -- not merely absent from Cess Zero, but no cess-related
        # master (real or placeholder) at all.
        masters = masters_for([_voucher("GSTR-2B", cess="0")], "GSTR-2B")
        self.assertFalse(any("cess" in row["name"].casefold() for row in masters))

    def test_nonzero_cess_still_requires_the_real_duties_and_taxes_cess_ledger_only(self):
        voucher = _voucher("GSTR-2B", cess="50")
        masters = masters_for([voucher], "GSTR-2B")

        cess_rows = [row for row in masters if "cess" in row["name"].casefold()]
        self.assertEqual(len(cess_rows), 1)
        self.assertEqual(cess_rows[0]["name"], "Cess")
        self.assertEqual(cess_rows[0]["master_type"], "Tax")
        self.assertEqual(cess_rows[0]["group"], "Duties & Taxes")

    def test_acceptance_case_gstr2b_missing_rate_with_cgst_sgst_only(self):
        """Task spec acceptance case: GSTR2B, rate unavailable, ABC Traders,
        Central Tax=3350, State/UT Tax=3350, Integrated Tax=0, Cess=0 ->
        exactly 4 masters, no Cess Zero, no unused IGST/Cess ledgers."""
        voucher = {
            "invoice_date": "2025-04-25", "voucher_type": "Purchase", "account_group": "Purchase Accounts",
            "party": {"name": "ABC Traders", "gstin": GSTIN}, "party_group": "Sundry Creditors",
            "items": [{"gst_rate": "0", "taxable_value": "33500", "sales_ledger": "GST Purchase",
                       "account_ledger": "GST Purchase", "supply_type": "Goods"}],
            "tax_allocations": [
                {"ledger": "GST Input CGST", "tax_type": "CGST", "gst_rate": "0"},
                {"ledger": "GST Input SGST/UTGST", "tax_type": "SGST/UTGST", "gst_rate": "0"},
            ],
            "cess": "0", "other_charges": "0", "rounding_adjustment": "0",
        }
        masters = masters_for([voucher], "GSTR2B")
        names = {row["name"] for row in masters}

        self.assertEqual(names, {"ABC Traders", "GST Purchase", "GST Input CGST", "GST Input SGST/UTGST"})
        self.assertNotIn("GST Input IGST", names)
        self.assertNotIn("GST Input Cess", names)
        self.assertNotIn(CESS_ZERO_MASTER_NAME, names)

        by_type = {}
        for row in masters:
            by_type.setdefault(row["master_type"], 0)
            by_type[row["master_type"]] += 1
        self.assertEqual(by_type.get("Party"), 1)
        self.assertEqual(by_type.get("Purchase"), 1)
        self.assertEqual(by_type.get("Tax"), 2)
        self.assertNotIn("Charge", by_type)

    def test_multiple_invoices_multiple_rates_produce_no_cess_zero(self):
        vouchers = [_voucher("GSTR-2B", rate) for rate in ("5", "12", "18", "28")] * 5
        masters = masters_for(vouchers, "GSTR-2B")

        self.assertFalse(any(row["name"] == CESS_ZERO_MASTER_NAME for row in masters))
        purchase_names = {row["name"] for row in masters if row["master_type"] == "Purchase"}
        self.assertEqual(purchase_names, {"GST Purchase 5%", "GST Purchase 12%", "GST Purchase 18%", "GST Purchase 28%"})


class PrepareNeverIncludesCessZeroTests(TestCase):
    """End-to-end through service.prepare(): no Cess Zero master, regardless
    of whether the source carries a real (possibly-zero) cess value."""

    def _batch(self):
        return GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN,
            company_details={"company_name": COMPANY, "gstin": COMPANY_GSTIN, "state": "Tamil Nadu"},
            source_parties={GSTIN: {"party_name": "Source Supplier"}})

    def _invoice(self, batch, cess):
        GSTParty.objects.get_or_create(gstin=GSTIN, defaults={"trade_name": "Source Supplier", "state_name": "Tamil Nadu"})
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="PUR-1", invoice_date=date(2025, 4, 1),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=cess,
            invoice_value=Decimal("1180"), place_of_supply="33",
            source_line={"source_row_number": 2})

    def test_prepare_never_includes_cess_zero_when_cess_is_a_real_zero(self):
        batch = self._batch()
        self._invoice(batch, Decimal("0"))

        _, _, masters = prepare(batch, "Tamil Nadu")

        self.assertFalse(any(row["name"] == CESS_ZERO_MASTER_NAME for row in masters))

    def test_prepare_never_includes_cess_zero_when_cess_was_never_in_the_source(self):
        batch = self._batch()
        self._invoice(batch, None)

        _, _, masters = prepare(batch, "Tamil Nadu")

        self.assertFalse(any(row["name"] == CESS_ZERO_MASTER_NAME for row in masters))

    def test_prepare_still_requires_party_account_and_tax_masters(self):
        batch = self._batch()
        self._invoice(batch, Decimal("0"))

        _, _, masters = prepare(batch, "Tamil Nadu")
        names = {row["name"] for row in masters}

        self.assertIn("Source Supplier", names)
        self.assertIn("GST Purchase 18%", names)
        self.assertTrue(any(name.startswith("Input CGST") for name in names))
        self.assertTrue(any(name.startswith("Input SGST") for name in names))


class VerifyMasterPropertiesZeroRateTests(SimpleTestCase):
    """The falsy-zero edge case a genuinely 0%-rate account ledger (e.g. the
    common "GST Purchase" missing-rate fallback) exposes in the shared
    Sales/Purchase verification path: Tally can legitimately echo back a
    blank/absent rate for a 0% ledger, which must not be treated the same as
    a real ledger's rate write failing."""

    def _zero_rate_master(self):
        return {"master_type": "Purchase", "name": "GST Purchase", "group": "Purchase Accounts",
                "gst_rate": "0", "supply_type": "Goods"}

    def test_zero_rate_master_with_blank_flat_rate_is_still_valid(self):
        master = self._zero_rate_master()
        actual = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "", "outer_gst_rate": "",
                  "gst_rates": {}}
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])

    def test_zero_rate_master_with_missing_nested_cgst_sgst_igst_is_still_valid(self):
        master = self._zero_rate_master()
        actual = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "0", "outer_gst_rate": "0",
                  "gst_rates": {"IGST": "0"}}  # CGST/SGST absent entirely, as a real 0%-ledger export can show
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])

    def test_nonzero_rate_master_with_blank_rate_still_fails(self):
        # Regression guard: the expected_rate == 0 exemption must never
        # apply to a real (nonzero-rate) Sales/Purchase ledger.
        master = {"master_type": "Purchase", "name": "GST Purchase 18%", "group": "Purchase Accounts",
                  "gst_rate": "18", "supply_type": "Goods"}
        actual = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "", "outer_gst_rate": "",
                  "gst_rates": {}}
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], "GST_ACCOUNT_RATE_WRITE_FAILED")

    def test_nonzero_rate_master_with_missing_nested_duty_head_still_fails(self):
        master = {"master_type": "Purchase", "name": "GST Purchase 18%", "group": "Purchase Accounts",
                  "gst_rate": "18", "supply_type": "Goods"}
        actual = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "18", "outer_gst_rate": "18",
                  "gst_rates": {"IGST": "18"}}  # CGST/SGST missing while a real nonzero rate is expected
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], "GST_ACCOUNT_RATE_WRITE_FAILED")


class VerifyMasterPropertiesCessValuationTests(SimpleTestCase):
    """The task's "TALLY MASTER VERIFICATION" requirement: the common
    "GST Sales"/"GST Purchase" ledgers (and every rate-wise Sales/Purchase
    ledger too, since Cess is never a Sales/Purchase account ledger's own
    duty -- see master_builder._gst_rate_detail_heads) must have their Cess
    Valuation Type/Rate confirmed on query-back, not merely written and
    assumed. Absent history stays legitimate (mirrors the existing
    CGST/SGST/IGST leniency); only positive evidence of the wrong value
    blocks."""

    def _master(self, name="GST Purchase", gst_rate="0"):
        return {"master_type": "Purchase", "name": name, "group": "Purchase Accounts",
                "gst_rate": gst_rate, "supply_type": "Goods"}

    def _actual(self, gst_rate="0", cess_rate=None, cess_valuation_type=None):
        gst_rates = {}
        if cess_rate is not None:
            gst_rates["Cess"] = cess_rate
        valuation_types = {}
        if cess_valuation_type is not None:
            valuation_types["Cess"] = cess_valuation_type
        return {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                "taxability": "Taxable", "supply_type": "Goods", "gst_rate": gst_rate, "outer_gst_rate": gst_rate,
                "gst_rates": gst_rates, "gst_rate_valuation_types": valuation_types}

    def test_common_ledger_with_confirmed_cess_not_applicable_and_zero_rate_is_valid(self):
        master = self._master()
        actual = self._actual(cess_rate="0", cess_valuation_type="Not Applicable")
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])

    def test_missing_cess_history_entirely_is_still_valid(self):
        master = self._master()
        actual = self._actual()
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])

    def test_rate_wise_ledger_with_confirmed_cess_not_applicable_is_valid(self):
        master = self._master(name="GST Purchase 18%", gst_rate="18")
        actual = self._actual(gst_rate="18", cess_rate="0", cess_valuation_type="Not Applicable")
        actual["gst_rates"].update({"CGST": "9", "SGST/UTGST": "9", "IGST": "18"})
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])

    def test_nonzero_cess_rate_is_rejected(self):
        master = self._master()
        actual = self._actual(cess_rate="5", cess_valuation_type="Not Applicable")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("Cess", result["reason"])

    def test_cess_valuation_type_based_on_value_is_rejected(self):
        master = self._master()
        actual = self._actual(cess_rate="0", cess_valuation_type="Based on Value")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("Cess Valuation Type", result["reason"])
