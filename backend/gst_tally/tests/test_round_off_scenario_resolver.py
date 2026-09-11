"""Round Off SCENARIO RESOLVER: resolve_round_off() must report which of the
six scenarios (IMPORTED_SINGLE, IMPORTED_MULTI_LINE, GENERATED_SINGLE,
GENERATED_MULTI_LINE, ZERO, TOTAL_MISMATCH) it applied, purely additive to
the two existing round-off rules (uploaded/detected vs manual/calculated) --
neither rule's own numbers change here, only the new round_off_scenario
label and the has_source_invoice_total()/is_multi_line_invoice() helpers
that derive it.
"""
from django.test import SimpleTestCase

from gst_tally.tally.round_off import (MANUAL, UPLOADED, has_source_invoice_total, is_multi_line_invoice,
                                       resolve_round_off)


def voucher(taxable, cgst, sgst, invoice_total=None, source_type=UPLOADED, items=None, igst="0", cess="0"):
    data = {"taxable_total": taxable, "cgst": cgst, "sgst": sgst, "igst": igst, "cess": cess,
            "other_charges": "0", "source_type": source_type, "items": items or []}
    if invoice_total is not None:
        data["invoice_total"] = invoice_total
    return data


class AcceptanceScenarioTests(SimpleTestCase):
    """The task spec's six acceptance tests, verbatim."""

    def test_1_imported_single_invoice(self):
        result = resolve_round_off(voucher("1579.52", "50.00", "50.00", "1680.00", items=[{"row": 1}]))
        self.assertEqual(result["round_off_scenario"], "IMPORTED_SINGLE")
        self.assertEqual(result["round_off"], "0.48")
        self.assertEqual(result["final_voucher_total"], "1680.00")

    def test_2_imported_multi_line_invoice(self):
        # Three source rows already grouped upstream (mappings.normalized_vouchers)
        # into one combined component total of 4999.70.
        result = resolve_round_off(voucher("2999.70", "1000.00", "1000.00", "5000.00",
                                            items=[{"row": 1}, {"row": 2}, {"row": 3}]))
        self.assertEqual(result["round_off_scenario"], "IMPORTED_MULTI_LINE")
        self.assertEqual(result["component_total"], "4999.70")
        self.assertEqual(result["round_off"], "0.30")
        self.assertEqual(result["final_voucher_total"], "5000.00")

    def test_3_new_single_invoice(self):
        result = resolve_round_off(voucher("100.62", "0", "0", source_type=MANUAL))
        self.assertEqual(result["round_off_scenario"], "GENERATED_SINGLE")
        self.assertEqual(result["round_off"], "0.38")
        self.assertEqual(result["final_voucher_total"], "101.00")

    def test_4_new_multi_line_invoice(self):
        # 100.49 + 200.49 + 300.49, already combined once into 601.47 -- never
        # rounded per row.
        result = resolve_round_off(voucher("601.47", "0", "0", source_type=MANUAL,
                                            items=[{"row": 1}, {"row": 2}, {"row": 3}]))
        self.assertEqual(result["round_off_scenario"], "GENERATED_MULTI_LINE")
        self.assertEqual(result["round_off"], "-0.47")
        self.assertEqual(result["final_voucher_total"], "601.00")

    def test_5_exact_match_is_zero_scenario(self):
        result = resolve_round_off(voucher("4900.00", "50.00", "50.00", "5000.00"))
        self.assertEqual(result["round_off_scenario"], "ZERO")
        self.assertEqual(result["round_off"], "0.00")

    def test_6_large_mismatch_is_total_mismatch_scenario(self):
        result = resolve_round_off(voucher("10000", "0", "0", "12000"))
        self.assertEqual(result["round_off_scenario"], "TOTAL_MISMATCH")
        self.assertEqual(result["status"], "Review Required")
        self.assertEqual(result["round_off"], "0.00")
        self.assertNotEqual(result["round_off"], "2000.00")


class HasSourceInvoiceTotalTests(SimpleTestCase):
    """0 and missing must never be conflated (task spec's SOURCE TOTAL
    DETECTION section)."""

    def test_zero_is_a_genuine_present_total(self):
        self.assertTrue(has_source_invoice_total({"invoice_total": "0"}))
        self.assertTrue(has_source_invoice_total({"invoice_total": 0}))
        self.assertTrue(has_source_invoice_total({"invoice_total": "0.00"}))

    def test_none_and_blank_are_genuinely_missing(self):
        self.assertFalse(has_source_invoice_total({"invoice_total": None}))
        self.assertFalse(has_source_invoice_total({"invoice_total": ""}))
        self.assertFalse(has_source_invoice_total({"invoice_total": "  "}))
        self.assertFalse(has_source_invoice_total({}))

    def test_an_uploaded_row_with_no_genuine_invoice_total_falls_back_to_the_generated_rule(self):
        # source_type alone must never decide the scenario -- an "uploaded"
        # row that genuinely carries no Invoice Total value still gets the
        # nearest-rupee rule, not a false large-mismatch-against-zero.
        result = resolve_round_off(voucher("100.62", "0", "0", invoice_total=None, source_type=UPLOADED))
        self.assertEqual(result["round_off_scenario"], "GENERATED_SINGLE")
        self.assertEqual(result["round_off"], "0.38")
        self.assertNotEqual(result["round_off_scenario"], "TOTAL_MISMATCH")


class IsMultiLineInvoiceTests(SimpleTestCase):
    def test_no_items_key_is_single_line(self):
        self.assertFalse(is_multi_line_invoice({}))

    def test_one_item_is_single_line(self):
        self.assertFalse(is_multi_line_invoice({"items": [{"a": 1}]}))

    def test_multiple_items_is_multi_line(self):
        self.assertTrue(is_multi_line_invoice({"items": [{"a": 1}, {"b": 2}]}))


class ExistingRulesUnchangedTests(SimpleTestCase):
    """Regression guard: the scenario resolver is purely additive -- every
    existing field/value from before this refactor must still be present
    and correct."""

    def test_uploaded_rule_fields_all_still_present(self):
        result = resolve_round_off(voucher("1579.52", "50.00", "50.00", "1680.00"))
        for field in ("source_type", "component_total", "source_invoice_value", "difference",
                      "suggested_round_off", "round_off", "final_voucher_total", "round_off_source",
                      "status", "reason"):
            self.assertIn(field, result, f"{field} missing from uploaded result")
        self.assertEqual(result["source_type"], UPLOADED)
        self.assertEqual(result["round_off_source"], "Source")

    def test_manual_rule_fields_all_still_present(self):
        result = resolve_round_off(voucher("100.62", "0", "0", source_type=MANUAL))
        for field in ("source_type", "component_total", "round_off", "final_voucher_total",
                      "round_off_source", "status", "reason"):
            self.assertIn(field, result, f"{field} missing from manual result")
        self.assertEqual(result["source_type"], MANUAL)
        self.assertEqual(result["round_off_source"], "Calculated")

    def test_explicit_source_round_off_path_still_reports_imported_scenario(self):
        # Section: explicit_round_off_valid path (a source file's own,
        # independently-reconciled Round Off column) -- unaffected by the
        # scenario resolver, still labeled as an imported scenario.
        v = voucher("1679.52", "0", "0", "1680.00")
        v["source_round_off"] = "0.48"
        result = resolve_round_off(v)
        self.assertEqual(result["round_off_source"], "Source")
        self.assertEqual(result["round_off_scenario"], "IMPORTED_SINGLE")
