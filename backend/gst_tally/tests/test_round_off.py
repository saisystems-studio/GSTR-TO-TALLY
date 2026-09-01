"""Round Off regression tests.

Two distinct rules, chosen by the explicit ``source_type`` flag:

* ``uploaded`` (Excel / CSV / JSON, any of GSTR-1 / GSTR-2A / GSTR-2B) -- the
  source Invoice Value is authoritative; Round Off is only ever *detected*
  from it, within a fixed tolerance.
* ``manual`` (a new invoice with no authoritative uploaded total) -- Round Off
  is *calculated* fresh, to the nearest whole rupee.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO
import json

from django.test import SimpleTestCase, TestCase

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.master_builder import masters_for
from gst_tally.tally.round_off import (MANUAL, ROUND_OFF_SOURCE_CALCULATED, ROUND_OFF_SOURCE_DETECTED,
                                       ROUND_OFF_SOURCE_NONE, UPLOADED, component_total, nearest_rupee,
                                       resolve_round_off, voucher_source_type)
from gst_tally.tally.service import _required_ledgers, _voucher_balance, prepare
from gst_tally.tally.validators import validate_voucher
from gst_tally.tally.voucher_builder import build_voucher
from xml.etree import ElementTree as ET


GSTIN = "33AAACB2894G1ZJ"


def uploaded_voucher(taxable, cgst, sgst, invoice_total, *, igst="0", cess="0", other_charges="0", source_type=UPLOADED):
    return {"taxable_total": taxable, "cgst": cgst, "sgst": sgst, "igst": igst, "cess": cess,
            "other_charges": other_charges, "invoice_total": invoice_total, "source_type": source_type}


class RoundOffDetectionTests(SimpleTestCase):
    """Section 1-4: the uploaded rule -- detect, never recompute a new total."""

    def test_existing_exact_match_has_zero_round_off(self):
        voucher = uploaded_voucher("125837.00", "13981.58", "13981.58", "153800.16")
        result = resolve_round_off(voucher)

        self.assertEqual(result["component_total"], "153800.16")
        self.assertEqual(result["round_off"], "0.00")
        self.assertEqual(result["final_voucher_total"], "153800.16")
        self.assertEqual(result["round_off_source"], ROUND_OFF_SOURCE_NONE)
        self.assertEqual(result["status"], "Ready")

    def test_existing_negative_small_difference_is_a_source_round_off(self):
        voucher = uploaded_voucher("7232.30", "0", "0", "7232.00")
        result = resolve_round_off(voucher)

        self.assertEqual(result["round_off"], "-0.30")
        self.assertEqual(result["final_voucher_total"], "7232.00")
        self.assertEqual(result["round_off_source"], ROUND_OFF_SOURCE_DETECTED)
        self.assertEqual(result["status"], "Ready")

    def test_existing_positive_small_difference_is_a_source_round_off(self):
        voucher = uploaded_voucher("7232.30", "0", "0", "7233.00")
        result = resolve_round_off(voucher)

        self.assertEqual(result["round_off"], "0.70")
        self.assertEqual(result["final_voucher_total"], "7233.00")
        self.assertEqual(result["round_off_source"], ROUND_OFF_SOURCE_DETECTED)
        self.assertEqual(result["status"], "Ready")

    def test_existing_large_difference_is_validation_failed_not_round_off(self):
        voucher = uploaded_voucher("7032.80", "0", "0", "7232.30")
        result = resolve_round_off(voucher)

        self.assertEqual(result["round_off"], "0.00")
        self.assertEqual(result["round_off_source"], ROUND_OFF_SOURCE_NONE)
        self.assertEqual(result["status"], "Review Required")
        self.assertIn("Invoice total mismatch. Difference ₹199.50 is too large to be treated as Round Off.", result["reason"])

    def test_tolerance_boundary_is_inclusive_at_exactly_one_rupee(self):
        within = uploaded_voucher("100.00", "0", "0", "101.00")
        outside = uploaded_voucher("100.00", "0", "0", "101.01")

        self.assertEqual(resolve_round_off(within)["status"], "Ready")
        self.assertEqual(resolve_round_off(within)["round_off"], "1.00")
        self.assertEqual(resolve_round_off(outside)["status"], "Review Required")

    def test_existing_invoice_is_never_force_rounded_to_a_whole_rupee(self):
        voucher = uploaded_voucher("125837.00", "13981.58", "13981.58", "153800.16")
        result = resolve_round_off(voucher)

        self.assertEqual(result["final_voucher_total"], "153800.16")
        self.assertNotEqual(result["final_voucher_total"], "153800.00")


class ManualRoundOffTests(SimpleTestCase):
    """Section 5: the manual rule -- calculate fresh, nearest-rupee, HALF_UP."""

    def test_new_100_20_rounds_down_by_0_20(self):
        result = nearest_rupee("100.20")
        self.assertEqual(str(result), "100.00")
        voucher = uploaded_voucher("100.20", "0", "0", "0", source_type=MANUAL)
        outcome = resolve_round_off(voucher)
        self.assertEqual(outcome["round_off"], "-0.20")
        self.assertEqual(outcome["final_voucher_total"], "100.00")

    def test_new_100_49_rounds_down_by_0_49(self):
        voucher = uploaded_voucher("100.49", "0", "0", "0", source_type=MANUAL)
        outcome = resolve_round_off(voucher)
        self.assertEqual(outcome["round_off"], "-0.49")
        self.assertEqual(outcome["final_voucher_total"], "100.00")

    def test_new_100_50_rounds_up_by_0_50_half_up_not_bankers_rounding(self):
        voucher = uploaded_voucher("100.50", "0", "0", "0", source_type=MANUAL)
        outcome = resolve_round_off(voucher)
        self.assertEqual(outcome["round_off"], "0.50")
        self.assertEqual(outcome["final_voucher_total"], "101.00")

    def test_new_100_80_rounds_up_by_0_20(self):
        voucher = uploaded_voucher("100.80", "0", "0", "0", source_type=MANUAL)
        outcome = resolve_round_off(voucher)
        self.assertEqual(outcome["round_off"], "0.20")
        self.assertEqual(outcome["final_voucher_total"], "101.00")

    def test_half_up_disagrees_with_python_banker_rounding_on_an_even_target(self):
        # Decimal's default ROUND_HALF_EVEN would round 100.50 down to 100 (even).
        # ROUND_HALF_UP must always round a .50 away from zero, i.e. up here.
        self.assertEqual(Decimal("100.50").quantize(Decimal("1"), rounding="ROUND_HALF_EVEN"), Decimal("100"))
        self.assertEqual(nearest_rupee("100.50"), Decimal("101.00"))

    def test_new_manual_invoice_does_get_nearest_rupee_rounding(self):
        voucher = uploaded_voucher("1000.60", "0", "0", "0", source_type=MANUAL)
        result = resolve_round_off(voucher)

        self.assertEqual(result["round_off"], "0.40")
        self.assertEqual(result["round_off_source"], ROUND_OFF_SOURCE_CALCULATED)
        self.assertEqual(result["final_voucher_total"], "1001.00")

    def test_manual_entry_already_a_whole_rupee_gets_no_round_off(self):
        voucher = uploaded_voucher("1000.00", "0", "0", "0", source_type=MANUAL)
        result = resolve_round_off(voucher)

        self.assertEqual(result["round_off"], "0.00")
        self.assertEqual(result["round_off_source"], ROUND_OFF_SOURCE_NONE)


class SourceTypeFlagTests(SimpleTestCase):
    """Section 6: source_type must be explicit, never inferred from UI state."""

    def test_missing_source_type_defaults_to_uploaded(self):
        self.assertEqual(voucher_source_type({}), UPLOADED)

    def test_explicit_manual_flag_selects_the_manual_rule(self):
        self.assertEqual(voucher_source_type({"source_type": "manual"}), MANUAL)
        self.assertEqual(voucher_source_type({"source_type": "Manual"}), MANUAL)

    def test_uploaded_and_manual_use_genuinely_different_rules_for_the_same_totals(self):
        # Same component figures (1000.60, not a whole rupee), only source_type
        # differs -- a single shared rule could not pass both assertions below.
        shared = {"taxable_total": "1000.60", "cgst": "0", "sgst": "0", "igst": "0", "cess": "0", "other_charges": "0"}
        uploaded = resolve_round_off({**shared, "invoice_total": "1000.60", "source_type": UPLOADED})
        manual = resolve_round_off({**shared, "invoice_total": "0", "source_type": MANUAL})

        self.assertEqual(uploaded["final_voucher_total"], "1000.60")   # preserved exactly, not force-rounded
        self.assertEqual(manual["final_voucher_total"], "1001.00")     # rounded to nearest rupee


class MultiRateGroupingTests(SimpleTestCase):
    """Section 7: Round Off is invoice-level, computed once after grouping every
    GST-rate row -- never per rate row."""

    def test_multi_rate_invoice_is_grouped_before_round_off_is_computed(self):
        # Invoice 32: 18% row (72712.00/6544.08/6544.08) + 28% row (53125.00/7437.50/7437.50).
        voucher = uploaded_voucher(
            taxable="125837.00", cgst="13981.58", sgst="13981.58", invoice_total="153800.16")
        result = resolve_round_off(voucher)

        self.assertEqual(result["component_total"], "153800.16")
        self.assertEqual(result["round_off"], "0.00")
        self.assertEqual(result["final_voucher_total"], "153800.16")

    def test_component_total_helper_sums_every_field_once_not_per_rate_row(self):
        voucher = uploaded_voucher("125837.00", "13981.58", "13981.58", "153800.16", other_charges="10.00")
        self.assertEqual(str(component_total(voucher)), "153800.16")


class VoucherPreviewFieldsTests(SimpleTestCase):
    """Section 8: the Voucher Preview columns."""

    def test_exact_match_preview_columns(self):
        voucher = uploaded_voucher("125837.00", "13981.58", "13981.58", "153800.16")
        result = resolve_round_off(voucher)
        self.assertEqual(result["difference"], "0.00")
        self.assertEqual(result["round_off"], "0.00")
        self.assertEqual(result["round_off_source"], "None")

    def test_source_round_off_preview_columns(self):
        voucher = uploaded_voucher("7232.30", "0", "0", "7232.00")
        result = resolve_round_off(voucher)
        self.assertEqual(result["difference"], "-0.30")
        self.assertEqual(result["round_off"], "-0.30")
        self.assertEqual(result["round_off_source"], "Source")

    def test_manual_preview_columns(self):
        voucher = uploaded_voucher("1000.60", "0", "0", "0", source_type=MANUAL)
        result = resolve_round_off(voucher)
        self.assertEqual(result["round_off"], "0.40")
        self.assertEqual(result["round_off_source"], "Calculated")


class RoundOffLedgerTests(SimpleTestCase):
    """Section 10: the Round Off ledger is only ever created/allocated when non-zero,
    and it never carries GST."""

    def test_nonzero_round_off_requires_the_round_off_ledger(self):
        voucher = {"party": {"name": "Buyer"}, "rate_allocations": [], "items": [],
                   "cgst": "0", "sgst": "0", "igst": "0", "cess": "0", "other_charges": "0",
                   "rounding_adjustment": "0.30"}
        self.assertIn("Round Off", _required_ledgers(voucher))
        self.assertIn(("Charge", "round off"), {(m["master_type"], m["name"].casefold()) for m in masters_for([voucher])})

    def test_zero_round_off_does_not_require_the_round_off_ledger(self):
        voucher = {"party": {"name": "Buyer"}, "rate_allocations": [], "items": [],
                   "cgst": "0", "sgst": "0", "igst": "0", "cess": "0", "other_charges": "0",
                   "rounding_adjustment": "0.00"}
        self.assertNotIn("Round Off", _required_ledgers(voucher))
        self.assertNotIn(("Charge", "round off"), {(m["master_type"], m["name"].casefold()) for m in masters_for([voucher])})

    def test_round_off_ledger_entry_carries_no_gst(self):
        voucher = {"invoice_number": "INV-1", "invoice_date": "2026-08-22", "voucher_type": "Sales",
                   "party": {"name": "Buyer"}, "items": [{"taxable_value": "1000", "gst_rate": "18", "sales_ledger": "GST Sales 18%"}],
                   "taxable_total": "1000", "cgst": "90", "sgst": "90", "igst": "0", "cess": "0",
                   "other_charges": "0", "rounding_adjustment": "0.01", "invoice_total": "1180.01"}
        xml = ET.fromstring(build_voucher(voucher, "Company D"))
        entries = {entry.findtext("LEDGERNAME"): entry for entry in xml.findall(".//LEDGERENTRIES.LIST")}

        self.assertIn("Round Off", entries)
        self.assertEqual(entries["Round Off"].findtext("AMOUNT"), "-0.01")
        # No separate GST ledger entry is generated for the Round Off amount --
        # only the CGST/SGST entries computed from the taxable rows exist.
        gst_entries = [name for name in entries if name not in {"Buyer", "GST Sales 18%", "Round Off"}]
        self.assertEqual(set(gst_entries), {"CGST", "SGST"})
        self.assertEqual(entries["CGST"].findtext("AMOUNT"), "90.00")


class PreviewMatchesTallyImportTests(TestCase):
    """Section 9: the exact Round Off in Voucher Preview must be the exact Round Off sent to Tally."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2a.csv", file_type="CSV", gst_return_type="GSTR2A",
            company_gstin="33AFHPM6103Q1Z8", source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Fetched Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="INV-1", invoice_date=date(2026, 8, 1),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1179.70"), place_of_supply="33",
            source_line={"source_row_number": 2})

    def test_preview_round_off_is_the_exact_value_sent_to_tally(self):
        _, vouchers, _ = prepare(self.batch, "Tamil Nadu")
        voucher = vouchers[0]

        self.assertEqual(voucher["status"], "Ready")
        self.assertEqual(voucher["round_off"], "-0.30")
        self.assertEqual(voucher["round_off_source"], "Source")
        self.assertEqual(voucher["final_voucher_total"], "1179.70")

        xml = ET.fromstring(build_voucher(voucher, "Company D"))
        round_off_entry = next(e for e in xml.findall(".//LEDGERENTRIES.LIST") if e.findtext("LEDGERNAME") == "Round Off")
        self.assertEqual(round_off_entry.findtext("AMOUNT"), str(-Decimal(voucher["round_off"])))
        self.assertTrue(_voucher_balance(voucher)["balanced"])


class ValidationFailedBlocksImportTests(TestCase):
    """Section 3/9/11: a mismatch too large for Round Off must reach Validation
    Failed, not silently import with a wrong total, and it must send the exact
    reason text specified."""

    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2a.csv", file_type="CSV", gst_return_type="GSTR2A",
            company_gstin="33AFHPM6103Q1Z8", source_parties={GSTIN: {"party_name": "Source Supplier"}})
        GSTParty.objects.create(gstin=GSTIN, trade_name="Fetched Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(
            import_batch=self.batch, invoice_no="INV-1", invoice_date=date(2026, 8, 1),
            customer_gstin=GSTIN, taxable_value=Decimal("1000"), tax_percent=Decimal("18"),
            cgst=Decimal("90"), sgst=Decimal("90"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("1380.01"), place_of_supply="33",
            source_line={"source_row_number": 2})

    def test_large_mismatch_is_validation_failed_with_the_exact_reason_text(self):
        _, vouchers, _ = prepare(self.batch, "Tamil Nadu")
        voucher = vouchers[0]

        self.assertEqual(voucher["status"], "Review Required")
        self.assertEqual(voucher["round_off"], "0.00")
        self.assertIn("Invoice total mismatch. Difference ₹200.01 is too large to be treated as Round Off.", voucher["reason"])


class SourceFormatCoverageTests(TestCase):
    """The uploaded rule must work identically for GSTR-1 (JSON), GSTR-2A (CSV)
    and GSTR-2B (Excel/XLSX)."""

    def _batch(self, return_type):
        return GSTImportBatch.objects.create(
            file_name="src", file_type="CSV", gst_return_type=return_type,
            company_gstin="33AFHPM6103Q1Z8", source_parties={GSTIN: {"party_name": "Source Supplier"}})

    def test_gstr2a_csv_row_produces_an_uploaded_voucher(self):
        from gst_tally.services.gstr2a_parser import parse as parse_gstr2a
        csv_text = ("GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Rate,Central Tax,State/UT Tax,Invoice Value\n"
                   f"{GSTIN},INV-CSV,01-08-2026,1000,18,90,90,1180\n")
        rows, _ = parse_gstr2a(BytesIO(csv_text.encode()))
        batch = self._batch("GSTR2A")
        GSTParty.objects.create(gstin=GSTIN, trade_name="Fetched Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(import_batch=batch, **rows[0])
        voucher = normalized_vouchers(batch, {"state": "Tamil Nadu"})[0]

        self.assertEqual(voucher_source_type(voucher), UPLOADED)
        self.assertEqual(resolve_round_off(voucher)["round_off"], "0.00")

    def test_gstr2b_excel_row_produces_an_uploaded_voucher(self):
        from openpyxl import Workbook
        from gst_tally.services.gstr2b_parser import parse as parse_gstr2b
        workbook = Workbook(); sheet = workbook.active
        sheet.append(["Customer GSTIN", "Invoice number", "Invoice Date", "Taxable Value", "Rate", "CGST", "SGST", "Invoice Value"])
        sheet.append([GSTIN, "INV-XLSX", "01-08-2026", 1000, 18, 90, 90, 1180])
        stream = BytesIO(); stream.name = "src.xlsx"; workbook.save(stream); stream.seek(0)
        rows, _ = parse_gstr2b(stream)
        batch = self._batch("GSTR2B")
        GSTParty.objects.create(gstin=GSTIN, trade_name="Fetched Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(import_batch=batch, **rows[0])
        voucher = normalized_vouchers(batch, {"state": "Tamil Nadu"})[0]

        self.assertEqual(voucher_source_type(voucher), UPLOADED)
        self.assertEqual(resolve_round_off(voucher)["round_off"], "0.00")

    def test_gstr1_json_row_produces_an_uploaded_voucher_and_honours_other_charges(self):
        from gst_tally.services.gstr1_parser import parse as parse_gstr1
        payload = {"gstin": "33AFHPM6103Q1Z8", "fp": "082026", "b2b": [{"ctin": GSTIN, "inv": [{
            "inum": "INV-JSON", "idt": "01-08-2026", "val": 1205, "pos": "33", "other_charges": 25,
            "itms": [{"itm_det": {"txval": 1000, "rt": 18, "camt": 90, "samt": 90}}]}]}]}
        rows, _ = parse_gstr1(BytesIO(json.dumps(payload).encode()))
        batch = self._batch("GSTR1")
        GSTParty.objects.create(gstin=GSTIN, trade_name="Fetched Supplier", state_name="Tamil Nadu")
        GSTInvoice.objects.create(import_batch=batch, **rows[0])
        voucher = normalized_vouchers(batch, {"state": "Tamil Nadu"})[0]

        self.assertEqual(voucher_source_type(voucher), UPLOADED)
        self.assertEqual(voucher["other_charges"], "25.00")
        result = resolve_round_off(voucher)
        self.assertEqual(result["component_total"], "1180.00")
        self.assertEqual(result["suggested_round_off"], "0")
        self.assertEqual(result["status"], "Review Required")


class ValidateVoucherIntegrationTests(SimpleTestCase):
    """validate_voucher must expose the same round-off decision, for both source types."""

    def test_validate_voucher_reports_uploaded_source_round_off(self):
        voucher = {"invoice_number": "INV-1", "invoice_date": "2026-08-22", "party": {"name": "Buyer", "gstin": "33AAACB2894G1ZJ"},
                   "items": [{"taxable_value": "7232.30", "gst_rate": "0"}], "transaction_type": "INTRA-STATE",
                   "taxable_total": "7232.30", "cgst": "0", "sgst": "0", "igst": "0", "cess": "0",
                   "other_charges": "0", "invoice_total": "7232.00", "source_type": UPLOADED}
        result = validate_voucher(voucher)

        self.assertEqual(result["source_type"], UPLOADED)
        self.assertEqual(result["rounding_adjustment"], "-0.30")
        self.assertEqual(result["round_off_source"], "Source")
        self.assertTrue(result["within_rounding_tolerance"])

    def test_validate_voucher_reports_manual_calculated_round_off(self):
        voucher = {"invoice_number": "INV-1", "invoice_date": "2026-08-22", "party": {"name": "Buyer", "gstin": "33AAACB2894G1ZJ"},
                   "items": [{"taxable_value": "1000.60", "gst_rate": "0"}], "transaction_type": "INTRA-STATE",
                   "taxable_total": "1000.60", "cgst": "0", "sgst": "0", "igst": "0", "cess": "0",
                   "other_charges": "0", "invoice_total": "0", "source_type": MANUAL}
        result = validate_voucher(voucher)

        self.assertEqual(result["source_type"], MANUAL)
        self.assertEqual(result["rounding_adjustment"], "0.40")
        self.assertEqual(result["round_off_source"], "Calculated")
        self.assertEqual(result["final_voucher_total"], "1001.00")
        self.assertTrue(result["within_rounding_tolerance"])
