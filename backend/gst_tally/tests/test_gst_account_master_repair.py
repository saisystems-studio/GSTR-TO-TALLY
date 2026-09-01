"""Regression tests for the Sales/Purchase GST account-ledger master
read/write/verify/repair pipeline (Batch 99/100: otherwise-eligible
vouchers blocked at "Master Setup Failed" because an existing ledger
such as "GST Purchase 18%" -- with a correct outer GST rate, Taxability,
Supply Type, and account group -- was rejected purely because an exact
Ledger object query-back did not also echo the GST Rate Details
popup/history rows. Root cause (see the task that added this file):
those popup/history fields are not reliable evidence either way -- they
must be diagnostic-only, not a blocking eligibility gate.

Section references below match the tasks that introduced this file:
 N/14 - regression test for the exact reported failure, correctly valid
 O/13 - the same generic builder/verifier proven across multiple GST rates
 P/12 - both accounting directions (GSTR-1 Sales / GSTR-2A|2B Purchase)
 Q/11 - input-format independence (Excel/CSV/JSON all normalize the same way)
"""
import json
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from xml.etree import ElementTree as ET

from gst_tally.models import GSTImportBatch, GSTInvoice
from gst_tally.tally.json_master_builder import TALLY_ANY, TALLY_APPLICABLE, TALLY_NOT_APPLICABLE, build_json_master
from gst_tally.tally.mappings import normalized_vouchers
from gst_tally.tally.master_builder import _applicable_from, account_ledger_rate, build_master, masters_for
from gst_tally.tally.return_mapping import SUPPORTED_RATES, extract_rate_from_name, rate_text
from gst_tally.tally.service import _verify_master_properties

RATES_UNDER_TEST = ("5", "12", "18", "28")
GENERIC_TEST_MATRIX_RATES = ("1", "3", "5", "12", "18", "28", "40")


def _account_master(direction, rate, action="Create"):
    prefix = "Purchase" if direction == "Purchase" else "Sales"
    group = "Purchase Accounts" if direction == "Purchase" else "Sales Accounts"
    return {"master_type": direction, "name": f"GST {prefix} {rate}%", "group": group,
            "gst_rate": rate, "supply_type": "Goods", "applicable_from": "2025-04-01", "action": action}


class AccountMasterAlterXmlTests(SimpleTestCase):
    """(O)(P) The same generic builder must emit both real, Tally-confirmed
    valid representations for every supported rate, in both accounting
    directions: the flat RATEOFTAXCALCULATION (authoritative when the
    ledger's "Provide breakup of tax rate" is configured No) and the nested
    GST Rate Details collection (authoritative when it is Yes). Both are
    sent on Create AND Alter -- omitting RATEOFTAXCALCULATION on Alter was
    tried and reverted: a real Tally export confirms it is a required field
    in the flat representation, and since Alter does not clear omitted
    fields, leaving it out would make a 0 -> 18 repair not actually change
    the flat rate for a company configured that way."""

    def test_flat_rate_scalar_is_sent_on_both_create_and_alter(self):
        for direction in ("Purchase", "Sales"):
            for rate in RATES_UNDER_TEST:
                for action in ("Create", "Alter"):
                    with self.subTest(direction=direction, rate=rate, action=action):
                        xml = ET.fromstring(build_master(_account_master(direction, rate, action)))
                        node = xml.find(".//LEDGER")
                        self.assertEqual(node.get("ACTION"), action)
                        self.assertEqual(node.findtext("RATEOFTAXCALCULATION"), rate)
                        self.assertIsNotNone(node.find("GSTDETAILS.LIST"))

    def test_alter_keeps_full_nested_gst_rate_details(self):
        for direction in ("Purchase", "Sales"):
            for rate in RATES_UNDER_TEST:
                with self.subTest(direction=direction, rate=rate):
                    xml = ET.fromstring(build_master(_account_master(direction, rate, "Alter")))
                    node = xml.find(".//LEDGER")
                    gst = node.find("GSTDETAILS.LIST")
                    self.assertIsNotNone(gst)
                    self.assertEqual(gst.findtext("SRCOFGSTDETAILS"), "Specify Details Here")
                    self.assertEqual(gst.findtext("TAXABILITY"), "Taxable")
                    state = gst.find("STATEWISEDETAILS.LIST")
                    self.assertIsNotNone(state, "nested STATEWISEDETAILS.LIST must still be sent on Alter")
                    heads = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE") for row in state.findall("RATEDETAILS.LIST")}
                    expected = Decimal(rate)
                    self.assertEqual(Decimal(heads["CGST"]), expected / 2)
                    self.assertEqual(Decimal(heads["SGST/UTGST"]), expected / 2)
                    self.assertEqual(Decimal(heads["IGST"]), expected)

    def test_ledger_name_and_group_are_never_hardcoded(self):
        """Different rate, different name/group -- not the same literal string."""
        purchase = ET.fromstring(build_master(_account_master("Purchase", "18", "Alter"))).find(".//LEDGER")
        sales = ET.fromstring(build_master(_account_master("Sales", "5", "Alter"))).find(".//LEDGER")
        self.assertEqual(purchase.get("NAME"), "GST Purchase 18%")
        self.assertEqual(purchase.findtext("PARENT"), "Purchase Accounts")
        self.assertEqual(sales.get("NAME"), "GST Sales 5%")
        self.assertEqual(sales.findtext("PARENT"), "Sales Accounts")
        self.assertNotEqual(purchase.get("NAME"), sales.get("NAME"))

    def test_every_configured_supported_rate_produces_a_complete_builder_output(self):
        """Not hardcoded to a fixed rate universe -- covers the actual configured set."""
        for rate in SUPPORTED_RATES:
            with self.subTest(rate=rate):
                master = _account_master("Purchase", rate_text(rate), "Alter")
                node = ET.fromstring(build_master(master)).find(".//LEDGER")
                rows = node.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")
                self.assertTrue(rows)


class RealTallyExportShapeTests(SimpleTestCase):
    """"TEST AGAINST REAL EXPORTED DATA": regression fixtures from real Tally
    JSON exports/screens. A fully-nested ledger (Provide breakup of tax rate
    = Yes) is valid. The "flat" shape below (rateoftaxcalculation present,
    but the GST Rate Details popup/Set-Alter/nested-breakdown are all
    confirmed absent from the query-back) was earlier assumed to also be a
    valid, merely-unreliably-echoed shape -- but real Tally screen evidence
    (a live Ledger Alteration screen showing "GST Rate: 0%" for exactly this
    shape, with the outer rate already correct) proves that assumption
    wrong: this exact combination means Tally itself has no GST Rate
    Details configured, and must fail verification with
    GST_RATE_DETAILS_INCOMPLETE rather than being accepted."""

    def _flat_mode_actual(self, rate, parent="Purchase Accounts"):
        # Real export shape: rateoftaxcalculation present, but the GST Rate
        # Details popup/Set-Alter/nested breakdown are all confirmed absent
        # -- this is the exact "GST Rate: 0% on the real Tally screen" shape,
        # not a valid flat-mode configuration.
        return {"exists": True, "parent": parent, "gst_applicable": "Applicable",
                "gst_rate_details": "Specify Details Here", "gst_rate_history_exists": True,
                "gst_rate_details_popup_exists": False, "set_alter_gst_rate_details": "No",
                "taxability": "Taxable", "supply_type": "Goods",
                "gst_rate": rate, "outer_gst_rate": rate, "gst_rates": {}}

    def _breakup_mode_actual(self, rate, parent="Purchase Accounts"):
        # Real export shape: gstdetails.statewisedetails[0].statename == "Any",
        # ratedetails carries CGST/SGST(as SGST/UTGST)/IGST (Cess is present in
        # the export as "Not Applicable" but is not itself a rate to verify).
        half = str(Decimal(rate) / 2)
        return {"exists": True, "parent": parent, "gst_applicable": "Applicable",
                "gst_rate_details": "Specify Details Here", "gst_rate_history_exists": True,
                "gst_rate_details_popup_exists": True, "set_alter_gst_rate_details": "Yes",
                "taxability": "Taxable", "supply_type": "Goods", "gst_rate": rate,
                "gst_rates": {"CGST": half, "SGST/UTGST": half, "IGST": rate}}

    def test_fixture_1_gst_purchase_1_percent_nested_breakup_is_accepted(self):
        master = _account_master("Purchase", "1")
        result = _verify_master_properties(master, self._breakup_mode_actual("1"))
        self.assertTrue(result["valid"], result["reason"])

    def test_fixture_2_gst_purchase_12_percent_with_confirmed_absent_details_is_rejected(self):
        master = _account_master("Purchase", "12")
        result = _verify_master_properties(master, self._flat_mode_actual("12"))
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], "GST_RATE_DETAILS_INCOMPLETE")

    def test_fixture_3_gst_purchase_18_percent_with_confirmed_absent_details_is_rejected(self):
        master = _account_master("Purchase", "18")
        result = _verify_master_properties(master, self._flat_mode_actual("18"))
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], "GST_RATE_DETAILS_INCOMPLETE")

    def test_fixture_4_gst_purchase_28_percent_with_confirmed_absent_details_is_rejected(self):
        master = _account_master("Purchase", "28")
        result = _verify_master_properties(master, self._flat_mode_actual("28"))
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], "GST_RATE_DETAILS_INCOMPLETE")

    def test_sales_flow_uses_sales_accounts_group_not_purchase(self):
        master = _account_master("Sales", "18")
        # A Sales ledger read back under the Purchase group must still fail --
        # Sales/Purchase group selection must never be mixed.
        wrong_group = self._breakup_mode_actual("18", parent="Purchase Accounts")
        result = _verify_master_properties(master, wrong_group)
        self.assertFalse(result["valid"])
        self.assertIn("parent expected 'Sales Accounts'", result["reason"])

        right_group = self._breakup_mode_actual("18", parent="Sales Accounts")
        result = _verify_master_properties(master, right_group)
        self.assertTrue(result["valid"], result["reason"])

    def test_nested_breakup_mode_passes_for_every_regression_rate(self):
        for rate in RATES_UNDER_TEST:
            for direction in ("Purchase", "Sales"):
                master = _account_master(direction, rate)
                parent = "Purchase Accounts" if direction == "Purchase" else "Sales Accounts"
                with self.subTest(direction=direction, rate=rate):
                    result = _verify_master_properties(master, self._breakup_mode_actual(rate, parent))
                    self.assertTrue(result["valid"], result["reason"])

    def test_confirmed_absent_details_is_rejected_for_every_regression_rate(self):
        for rate in RATES_UNDER_TEST:
            for direction in ("Purchase", "Sales"):
                master = _account_master(direction, rate)
                parent = "Purchase Accounts" if direction == "Purchase" else "Sales Accounts"
                with self.subTest(direction=direction, rate=rate):
                    result = _verify_master_properties(master, self._flat_mode_actual(rate, parent))
                    self.assertFalse(result["valid"])
                    self.assertEqual(result["error_code"], "GST_RATE_DETAILS_INCOMPLETE")


class AccountMasterVerificationRegressionTests(SimpleTestCase):
    """(N) A single GST Rate Details signal being absent from a query-back
    (popup/Set-Alter/nested-breakdown key simply not present in the
    response) is genuinely ambiguous -- an exact Ledger object query-back
    can omit one collection even for a valid ledger -- so that alone stays
    diagnostic-only. But when all three are actually PRESENT in the
    response and all three explicitly say "not configured", that is
    convergent, definitive evidence the nested structure is genuinely
    absent -- confirmed against a real Tally Ledger Alteration screen
    showing "GST Rate: 0%" for exactly this shape despite the outer rate
    already being correct -- and must fail with GST_RATE_DETAILS_INCOMPLETE,
    not be accepted as valid."""

    def _reported_actual(self, direction, rate, **overrides):
        # Exact shape from the bug report: outer/effective rate, taxability,
        # supply type and GST Rate Details (SRCOFGSTDETAILS) are all already
        # correct; popup/Set-Alter/nested-breakdown are all confirmed absent
        # from the read-back -- the real Tally screen for this exact shape
        # shows GST Rate = 0%.
        parent = "Purchase Accounts" if direction == "Purchase" else "Sales Accounts"
        return {"exists": True, "parent": parent, "gst_applicable": "Applicable",
                "gst_rate_details": "Specify Details Here", "gst_rate_history_exists": True,
                "gst_rate_details_popup_exists": False, "set_alter_gst_rate_details": "No",
                "taxability": "Taxable", "supply_type": "Goods", "gst_rate": rate, "outer_gst_rate": rate,
                "gst_rates": {}, "gst_applicable_from": "99999999", **overrides}

    def test_reported_shape_is_rejected_with_gst_rate_details_incomplete(self):
        for direction in ("Purchase", "Sales"):
            for rate in RATES_UNDER_TEST:
                with self.subTest(direction=direction, rate=rate):
                    master = _account_master(direction, rate)
                    result = _verify_master_properties(master, self._reported_actual(direction, rate))

                    self.assertFalse(result["valid"])
                    self.assertEqual(result["error_code"], "GST_RATE_DETAILS_INCOMPLETE")
                    self.assertIn(f"outer GST rate {Decimal(rate):g}%", result["reason"])
                    self.assertIn("GST Rate Details rows are missing", result["reason"])

    def test_single_signal_absent_from_response_stays_a_warning_only(self):
        """The genuinely ambiguous case: only ONE of the three signals is
        missing from the response (the other two keys are simply not
        present at all, as an exact Ledger object query-back can omit them
        even for a valid ledger) -- must not block on its own."""
        master = _account_master("Purchase", "18")
        actual = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "gst_rate_details": "Specify Details Here", "gst_rate_history_exists": True,
                  "gst_rate_details_popup_exists": False,
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "18", "outer_gst_rate": "18"}
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])
        self.assertIn("GST Rate Details popup rate rows are missing", result["warnings"])

    def test_fully_repaired_ledger_with_nested_history_is_also_valid_and_has_no_warnings(self):
        for direction in ("Purchase", "Sales"):
            for rate in RATES_UNDER_TEST:
                with self.subTest(direction=direction, rate=rate):
                    half = str(Decimal(rate) / 2)
                    master = _account_master(direction, rate)
                    actual = self._reported_actual(direction, rate, gst_rate_details_popup_exists=True,
                                                    set_alter_gst_rate_details="Yes",
                                                    gst_rates={"CGST": half, "SGST/UTGST": half, "IGST": rate},
                                                    gst_applicable_from=_applicable_from({"applicable_from": "2025-04-01"}))
                    result = _verify_master_properties(master, actual)

                    self.assertTrue(result["valid"], result["reason"])
                    self.assertEqual(result["warnings"], "")

    def test_genuinely_wrong_applicability_still_fails(self):
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", gst_applicable="Not Applicable", taxability="")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("GST applicability", result["reason"])

    def test_genuinely_wrong_supply_type_still_fails(self):
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", supply_type="Services")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("Type of Supply", result["reason"])

    def test_genuinely_wrong_effective_rate_still_fails(self):
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", gst_rate="28", outer_gst_rate="28")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("rate expected 18", result["reason"])

    def test_unreadable_effective_rate_still_fails(self):
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", gst_rate="", outer_gst_rate="")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("GST rate could not be verified", result["reason"])

    def test_wrong_account_group_still_fails(self):
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", parent="Sales Accounts")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("parent expected", result["reason"])

    def test_nested_rate_actually_present_but_wrong_still_fails(self):
        """Once Tally's query-back DOES return nested rate rows, a wrong
        split (e.g. CGST/SGST read back as 0 while the ledger is genuinely
        used for intra-state vouchers) is real, actionable evidence -- unlike
        the ambiguous all-absent case above, this must still block, since
        Tally would apply the wrong tax amounts."""
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", gst_rate_details_popup_exists=True,
                                        set_alter_gst_rate_details="Yes",
                                        gst_rates={"CGST": "0", "SGST/UTGST": "0.00", "IGST": "18.000"})
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("nested CGST GST rate expected 9", result["reason"])
        self.assertIn("nested SGST/UTGST GST rate expected 9", result["reason"])
        self.assertNotIn("IGST", result["reason"])

    def test_nested_rate_partially_present_with_one_head_missing_still_fails(self):
        """CGST/SGST correctly present is real evidence the breakdown CAN be
        read for this ledger -- so IGST being the one head missing is a real
        gap (e.g. inter-state postings against this ledger would be wrong),
        not the same ambiguous "nothing returned at all" case."""
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", gst_rate_details_popup_exists=True,
                                        set_alter_gst_rate_details="Yes",
                                        gst_rates={"CGST": "9", "SGST/UTGST": "9"})
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])
        self.assertIn("nested IGST GST rate is missing", result["reason"])

    def test_write_response_counters_alone_never_imply_valid(self):
        """(S) ALTERED=1 is not evidence of semantic correctness -- only the
        explicit re-read/verify step decides. The verification function
        itself never sees write-response counters at all (only the re-read
        `actual` properties) -- a genuinely wrong rate still fails even
        though a real "ALTERED=1, no error" write would have preceded it."""
        master = _account_master("Purchase", "18")
        actual = self._reported_actual("Purchase", "18", gst_rate="12", outer_gst_rate="12")
        result = _verify_master_properties(master, actual)
        self.assertFalse(result["valid"])


class AccountVsTaxMasterVerificationIndependenceTests(SimpleTestCase):
    """(G)(H) Account-ledger verification must not require tax-ledger-only
    fields (tax_type/duty_type), and must not be satisfiable by tax-ledger
    shaped data -- the two builders/verifiers stay independent."""

    def test_account_verification_does_not_require_duty_type_or_tax_type(self):
        master = _account_master("Purchase", "18")
        actual = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "gst_rate_details": "Specify Details Here", "gst_rate_history_exists": True,
                  "gst_rate_details_popup_exists": True, "set_alter_gst_rate_details": "Yes",
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "18", "outer_gst_rate": "18",
                  "gst_rates": {"CGST": "9", "SGST/UTGST": "9", "IGST": "18"}, "gst_applicable_from": "20250401"}
        # No "tax_type"/"duty_type" keys at all -- must still pass.
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])

    def test_tax_ledger_verification_requires_duty_head_and_percentage_not_gst_details_popup(self):
        master = {"master_type": "Tax", "name": "Input CGST 9%", "tax_type": "CGST", "gst_rate": "9"}
        actual = {"exists": True, "duty_type": "GST", "tax_type": "CGST", "gst_rate": "9",
                  "rounding_method": "Not Applicable"}
        result = _verify_master_properties(master, actual)
        self.assertTrue(result["valid"], result["reason"])
        # None of the account-ledger-only GST-details-popup fields are required here.
        self.assertNotIn("GST Rate Details popup", result["reason"])


class MasterCreationFormatIndependenceTests(TestCase):
    """(A)(Q) Excel/CSV/JSON all converge on the same GSTInvoice model
    (see services/import_service.py FORMAT_PARSERS) before any master or
    voucher logic runs. Prove that batches built from logically-identical
    rows -- as if separately parsed from Excel, CSV, and JSON -- produce
    the same normalized vouchers and the same required masters."""

    COMPANY_GSTIN = "33AFHPM6103Q1Z8"
    GSTIN = "33AAACB2894G1ZJ"

    def _batch_with_file_type(self, file_type):
        return GSTImportBatch.objects.create(
            file_name=f"batch.{file_type.lower()}", file_type=file_type, gst_return_type="GSTR2B",
            company_gstin=self.COMPANY_GSTIN,
            company_details={"company_name": "SRI MAHALAKSHMI TRADERS", "gstin": self.COMPANY_GSTIN, "state": "Tamil Nadu"},
            source_parties={self.GSTIN: {"party_name": "Source Supplier"}})

    def _add_invoice(self, batch):
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="1", invoice_date=__import__("datetime").date(2025, 4, 1),
            customer_gstin=self.GSTIN, taxable_value=Decimal("6150.00"), tax_percent=Decimal("18"),
            cgst=Decimal("541.15"), sgst=Decimal("541.15"), igst=Decimal("0"), cess=Decimal("0"),
            invoice_value=Decimal("7232.30"), place_of_supply="33")

    def test_excel_csv_json_batches_with_identical_rows_yield_identical_masters(self):
        company = {"company": "SRI MAHALAKSHMI TRADERS", "state": "Tamil Nadu", "gstin": self.COMPANY_GSTIN}
        required_masters_by_format = {}
        for file_type in ("EXCEL", "CSV", "JSON"):
            batch = self._batch_with_file_type(file_type)
            self._add_invoice(batch)
            vouchers = normalized_vouchers(batch, company)
            required_masters_by_format[file_type] = sorted(
                (m["master_type"], m["name"]) for m in masters_for(vouchers))

        excel_masters = required_masters_by_format["EXCEL"]
        self.assertTrue(excel_masters)
        self.assertEqual(excel_masters, required_masters_by_format["CSV"])
        self.assertEqual(excel_masters, required_masters_by_format["JSON"])


class UniqueMasterPerBatchTests(TestCase):
    """(J)(K) A master required by several vouchers must be collected once,
    not repaired independently per voucher."""

    def test_masters_for_deduplicates_across_many_vouchers_sharing_a_ledger(self):
        batch = GSTImportBatch.objects.create(
            file_name="many.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
            company_details={"company_name": "SRI MAHALAKSHMI TRADERS", "state": "Tamil Nadu", "gstin": "33AFHPM6103Q1Z8"},
            source_parties={"33AAACB2894G1ZJ": {"party_name": "Source Supplier"}})
        import datetime
        for day in range(1, 6):
            GSTInvoice.objects.create(
                import_batch=batch, invoice_no=str(day), invoice_date=datetime.date(2025, 4, day),
                customer_gstin="33AAACB2894G1ZJ", taxable_value=Decimal("1000.00"), tax_percent=Decimal("18"),
                cgst=Decimal("90.00"), sgst=Decimal("90.00"), igst=Decimal("0"), cess=Decimal("0"),
                invoice_value=Decimal("1180.00"), place_of_supply="33")

        company = {"company": "SRI MAHALAKSHMI TRADERS", "state": "Tamil Nadu", "gstin": "33AFHPM6103Q1Z8"}
        vouchers = normalized_vouchers(batch, company)
        self.assertEqual(len(vouchers), 5, "sanity: five distinct invoices")

        masters = masters_for(vouchers)
        names = [m["name"] for m in masters]
        self.assertEqual(names.count("GST Purchase 18%"), 1, "one required master, not one per voucher")
        self.assertEqual(names.count("Input CGST 9%"), 1)
        self.assertEqual(names.count("Input SGST 9%"), 1)


class RateParsedFromLedgerNameTests(SimpleTestCase):
    """"GST Rate = 0%" root cause: the ledger's own name is the source of
    truth for its rate (return_mapping.extract_rate_from_name, already
    implemented and unit-tested there, but never actually used anywhere in
    the master write/verify pipeline). Sections 1/4/11/17: one generic
    parser, every supported rate, harmless spacing/case variations, never
    hardcoded to only 12 or 18."""

    def test_every_generic_test_matrix_rate_parses_from_purchase_and_sales_names(self):
        for rate in GENERIC_TEST_MATRIX_RATES:
            for prefix in ("Purchase", "Sales"):
                with self.subTest(prefix=prefix, rate=rate):
                    self.assertEqual(extract_rate_from_name(f"GST {prefix} {rate}%"), Decimal(rate))

    def test_harmless_spacing_and_case_variations_still_parse(self):
        for name in ("GST PURCHASE 18%", "GST Purchase 18 %", "GST SALES 12%", "gst purchase 5%", "GST Purchase  28 %"):
            with self.subTest(name=name):
                self.assertIsNotNone(extract_rate_from_name(name))

    def test_account_ledger_rate_is_never_hardcoded_to_12_or_18_only(self):
        """The parser/writer must be generic -- not special-cased to the two
        rates that happened to appear in the bug report."""
        for rate in GENERIC_TEST_MATRIX_RATES:
            master = _account_master("Purchase", rate)
            self.assertEqual(account_ledger_rate(master), Decimal(rate))


class AccountLedgerRateAuthorityTests(SimpleTestCase):
    """The ledger name overrides a wrong/missing gst_rate field -- this is
    what actually prevents "GST Rate = 0%" for a canonically-named ledger
    regardless of any upstream inconsistency in how that field got set."""

    def test_name_wins_even_when_gst_rate_field_is_wrong(self):
        master = {**_account_master("Purchase", "12"), "gst_rate": "0"}
        self.assertEqual(account_ledger_rate(master), Decimal("12"))

    def test_name_wins_even_when_gst_rate_field_is_missing(self):
        master = _account_master("Purchase", "18")
        del master["gst_rate"]
        self.assertEqual(account_ledger_rate(master), Decimal("18"))

    def test_a_wrong_gst_rate_field_never_reaches_the_xml_write(self):
        master = {**_account_master("Purchase", "12"), "gst_rate": "0"}
        node = ET.fromstring(build_master(master)).find(".//LEDGER")
        self.assertEqual(node.findtext("RATEOFTAXCALCULATION"), "12")
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                 for row in node.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}
        self.assertEqual(rates["IGST"], "12")

    def test_non_standard_ledger_name_falls_back_to_the_supplied_gst_rate(self):
        """A custom/mapped ledger name that doesn't encode a percentage (e.g.
        an item-level override) must still work -- the name is authoritative
        only when it actually contains one."""
        master = {"master_type": "Purchase", "name": "Freight Purchase Account",
                  "group": "Purchase Accounts", "gst_rate": "18", "supply_type": "Goods"}
        self.assertEqual(account_ledger_rate(master), Decimal("18"))


class JsonToXmlRateConsistencyTests(SimpleTestCase):
    """(10) The GST rate must survive JSON canonical model -> XML transport
    conversion unchanged: JSON master rate = 12 -> transport payload rate =
    12. Both builders derive the rate the same way (account_ledger_rate),
    so they can never silently diverge."""

    def test_json_and_xml_agree_on_rateoftaxcalculation_for_every_rate(self):
        for rate in GENERIC_TEST_MATRIX_RATES:
            for direction in ("Purchase", "Sales"):
                with self.subTest(direction=direction, rate=rate):
                    master = _account_master(direction, rate, "Alter")

                    json_message = build_json_master(master, "Test Company")["tallymessage"][0]
                    xml_node = ET.fromstring(build_master(master, "Test Company")).find(".//LEDGER")

                    self.assertEqual(Decimal(json_message["rateoftaxcalculation"]), Decimal(rate))
                    self.assertEqual(Decimal(xml_node.findtext("RATEOFTAXCALCULATION")), Decimal(rate))
                    self.assertEqual(Decimal(json_message["rateoftaxcalculation"]), Decimal(xml_node.findtext("RATEOFTAXCALCULATION")))

                    json_rates = {row["gstratedutyhead"]: row["gstrate"]
                                 for row in json_message["gstdetails"][0]["statewisedetails"][0]["ratedetails"]}
                    xml_rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                                for row in xml_node.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}
                    self.assertEqual(Decimal(json_rates["IGST"]), Decimal(xml_rates["IGST"]))
                    self.assertEqual(Decimal(json_rates["CGST"]), Decimal(xml_rates["CGST"]))

    def test_end_to_end_rate_survives_json_model_through_xml_write_to_reread(self):
        """(18) End-to-end for GST Purchase 12%: parse name -> 12 -> JSON
        canonical model contains 12 -> XML transport payload contains 12 ->
        a synthetic Tally re-read of 12 passes verification."""
        master = _account_master("Purchase", "12", "Alter")

        parsed_rate = extract_rate_from_name(master["name"])
        self.assertEqual(parsed_rate, Decimal("12"))

        json_message = build_json_master(master, "Test Company")["tallymessage"][0]
        self.assertEqual(json_message["rateoftaxcalculation"], " 12")

        xml_node = ET.fromstring(build_master(master, "Test Company")).find(".//LEDGER")
        self.assertEqual(xml_node.findtext("RATEOFTAXCALCULATION"), "12")

        # Simulate Tally's re-read after this exact payload was actually
        # applied -- including the nested breakdown, since a real Tally
        # screen confirms an empty one means GST Rate displays as 0% despite
        # a correct outer rate (see GST_RATE_DETAILS_INCOMPLETE).
        reread = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "gst_rate_details": "Specify Details Here", "gst_rate_history_exists": True,
                  "gst_rate_details_popup_exists": True, "set_alter_gst_rate_details": "Yes",
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "12", "outer_gst_rate": "12",
                  "gst_rates": {"CGST": "6", "SGST/UTGST": "6", "IGST": "12"}}
        result = _verify_master_properties(master, reread)
        self.assertTrue(result["valid"], result["reason"])


class GstDetailsFieldOrderTests(SimpleTestCase):
    """Field order inside GSTDETAILS.LIST matches a real TallyPrime XML
    export verbatim (SUPPLYTYPE/TAXABILITY precede GSTNOTIFICATIONNUMBER/
    GSTNATUREOFTRANSACTION/NATUREOFGOODS, not after) -- Tally's XML import
    is already known to be order-sensitive for accounting/GST aggregates
    elsewhere in this integration (voucher_builder._taxable_amount), so a
    wrong sequence here is a plausible reason Tally accepts the write
    (ALTERED=1) but doesn't apply the nested rate details."""

    def test_gst_details_children_appear_in_the_real_export_order(self):
        master = _account_master("Purchase", "12", "Alter")
        node = ET.fromstring(build_master(master, "Test Company")).find(".//GSTDETAILS.LIST")
        tags = [child.tag for child in node]
        supply_type_index = tags.index("SUPPLYTYPE")
        taxability_index = tags.index("TAXABILITY")
        notification_number_index = tags.index("GSTNOTIFICATIONNUMBER")
        nature_of_goods_index = tags.index("NATUREOFGOODS")
        src_of_gst_details_index = tags.index("SRCOFGSTDETAILS")
        self.assertLess(supply_type_index, notification_number_index)
        self.assertLess(taxability_index, notification_number_index)
        self.assertLess(supply_type_index, nature_of_goods_index)
        self.assertLess(nature_of_goods_index, src_of_gst_details_index)


class DetailedGstRateBreakupTests(SimpleTestCase):
    """When 'Set/Alter GST Rate Details' is Yes, the outer flat rate alone is
    not enough -- Tally must also receive nested statewisedetails/ratedetails
    (IGST = full rate, CGST/SGST = half rate, Cess = Not Applicable). Real
    Tally export evidence: GST Purchase 1% has this nested breakup populated
    with real CGST/SGST/IGST values, not zeros -- the same must hold for
    every rate, in both the XML and JSON builders (which share one function,
    account_ledger_rate/_gst_rate_detail_heads, so they can't diverge)."""

    def test_nested_breakup_is_igst_full_cgst_sgst_half_for_every_rate_both_directions(self):
        for rate in GENERIC_TEST_MATRIX_RATES:
            for direction in ("Purchase", "Sales"):
                with self.subTest(direction=direction, rate=rate):
                    master = _account_master(direction, rate, "Alter")
                    expected_half = Decimal(rate) / 2

                    xml_node = ET.fromstring(build_master(master, "Test Company")).find(".//LEDGER")
                    xml_rows = {row.findtext("GSTRATEDUTYHEAD"): row
                               for row in xml_node.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}
                    self.assertEqual(Decimal(xml_rows["IGST"].findtext("GSTRATE")), Decimal(rate))
                    self.assertEqual(Decimal(xml_rows["CGST"].findtext("GSTRATE")), expected_half)
                    self.assertEqual(Decimal(xml_rows["SGST/UTGST"].findtext("GSTRATE")), expected_half)

                    json_message = build_json_master(master, "Test Company")["tallymessage"][0]
                    json_rows = {row["gstratedutyhead"]: row
                                for row in json_message["gstdetails"][0]["statewisedetails"][0]["ratedetails"]}
                    self.assertEqual(Decimal(json_rows["IGST"]["gstrate"]), Decimal(rate))
                    self.assertEqual(Decimal(json_rows["CGST"]["gstrate"]), expected_half)
                    self.assertEqual(Decimal(json_rows["SGST/UTGST"]["gstrate"]), expected_half)

    def test_cess_row_uses_the_exact_tally_not_applicable_enum_with_a_space(self):
        """Regression for the actual bug: 'NotApplicable' (no space) is not a
        Tally enum value Tally recognises -- every other 'Not Applicable'
        write in this codebase (ROUNDTYPE, rounding_method, LEDSTATENAME on a
        non-GST party) uses the spaced form. A wrong enum on just the Cess
        row previously caused Tally to accept the ledger (outer rate = 12)
        but silently drop the entire nested RATEDETAILS.LIST -- exactly the
        "IGST/CGST/SGST show 0 in the popup" symptom being fixed here."""
        master = _account_master("Purchase", "12", "Alter")

        xml_node = ET.fromstring(build_master(master, "Test Company")).find(".//LEDGER")
        cess_row = next(row for row in xml_node.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")
                        if row.findtext("GSTRATEDUTYHEAD") == "Cess")
        self.assertEqual(cess_row.findtext("GSTRATEVALUATIONTYPE"), "Not Applicable")
        self.assertNotEqual(cess_row.findtext("GSTRATEVALUATIONTYPE"), "NotApplicable")

        json_message = build_json_master(master, "Test Company")["tallymessage"][0]
        json_cess = next(row for row in json_message["gstdetails"][0]["statewisedetails"][0]["ratedetails"]
                         if row["gstratedutyhead"] == "Cess")
        self.assertEqual(json_cess["gstratevaluationtype"], TALLY_NOT_APPLICABLE)

    def test_end_to_end_nested_breakup_survives_json_model_through_xml_write_to_reread(self):
        """Extends the outer-rate end-to-end test to the nested breakup: for
        GST Purchase 12%, IGST=12/CGST=6/SGST=6 must be identical in the JSON
        canonical model, the real XML transport payload, and a synthetic
        Tally re-read -- and that re-read must pass verification."""
        master = _account_master("Purchase", "12", "Alter")

        json_message = build_json_master(master, "Test Company")["tallymessage"][0]
        json_rows = {row["gstratedutyhead"]: row["gstrate"]
                    for row in json_message["gstdetails"][0]["statewisedetails"][0]["ratedetails"]}
        self.assertEqual(json_rows["IGST"], " 12"); self.assertEqual(json_rows["CGST"], " 6"); self.assertEqual(json_rows["SGST/UTGST"], " 6")

        xml_node = ET.fromstring(build_master(master, "Test Company")).find(".//LEDGER")
        xml_rows = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                   for row in xml_node.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}
        self.assertEqual(xml_rows["IGST"], "12"); self.assertEqual(xml_rows["CGST"], "6"); self.assertEqual(xml_rows["SGST/UTGST"], "6")

        reread = {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                  "gst_rate_details": "Specify Details Here", "gst_rate_history_exists": True,
                  "gst_rate_details_popup_exists": True, "set_alter_gst_rate_details": "Yes",
                  "taxability": "Taxable", "supply_type": "Goods", "gst_rate": "12", "outer_gst_rate": "12",
                  "gst_rates": {"IGST": "12", "CGST": "6", "SGST/UTGST": "6"}}
        result = _verify_master_properties(master, reread)
        self.assertTrue(result["valid"], result["reason"])

        # And the regression case this whole fix targets: Set/Alter says Yes
        # but the nested rates actually came back as zero -- that must fail.
        broken_reread = {**reread, "gst_rates": {"IGST": "0", "CGST": "0", "SGST/UTGST": "0"}}
        broken_result = _verify_master_properties(master, broken_reread)
        self.assertFalse(broken_result["valid"])


class RateModeVariantTests(SimpleTestCase):
    """rate_mode controls which of the two GST rate representations a write
    contains -- "both" (Strategy A, default and unchanged), "flat_only" and
    "nested_only" (Strategy B's two retry variants, service.py)."""

    def test_flat_only_omits_the_entire_nested_block(self):
        master = _account_master("Purchase", "12", "Alter")
        node = ET.fromstring(build_master(master, "Test Company", rate_mode="flat_only")).find(".//LEDGER")
        self.assertEqual(node.findtext("RATEOFTAXCALCULATION"), "12")
        self.assertIsNone(node.find("GSTDETAILS.LIST"))

    def test_nested_only_omits_the_flat_scalar(self):
        master = _account_master("Purchase", "12", "Alter")
        node = ET.fromstring(build_master(master, "Test Company", rate_mode="nested_only")).find(".//LEDGER")
        self.assertIsNone(node.find("RATEOFTAXCALCULATION"))
        rates = {row.findtext("GSTRATEDUTYHEAD"): row.findtext("GSTRATE")
                for row in node.findall(".//STATEWISEDETAILS.LIST/RATEDETAILS.LIST")}
        self.assertEqual(rates["IGST"], "12"); self.assertEqual(rates["CGST"], "6")

    def test_both_is_still_the_default_and_unchanged(self):
        master = _account_master("Purchase", "12", "Alter")
        default_xml = build_master(master, "Test Company")
        explicit_xml = build_master(master, "Test Company", rate_mode="both")
        self.assertEqual(default_xml, explicit_xml)
        node = ET.fromstring(default_xml).find(".//LEDGER")
        self.assertEqual(node.findtext("RATEOFTAXCALCULATION"), "12")
        self.assertIsNotNone(node.find("GSTDETAILS.LIST"))

    def test_json_rate_mode_variants_match_xml(self):
        master = _account_master("Sales", "18", "Alter")
        flat_only = build_json_master(master, "Test Company", rate_mode="flat_only")["tallymessage"][0]
        self.assertEqual(flat_only["rateoftaxcalculation"], " 18")
        self.assertNotIn("gstdetails", flat_only)
        nested_only = build_json_master(master, "Test Company", rate_mode="nested_only")["tallymessage"][0]
        self.assertNotIn("rateoftaxcalculation", nested_only)
        self.assertIn("gstdetails", nested_only)


class StrategyBFallbackTests(SimpleTestCase):
    """service._repair_gst_account_rate_with_fallback: Strategy B only
    engages when Strategy A's failure is specifically the GST rate, retries
    with the two single-mode variants, always re-reads real Tally state
    before declaring success, and never claims success from ALTERED=1 alone."""

    def _reread(self, rate, nested=None):
        return {"exists": True, "parent": "Purchase Accounts", "gst_applicable": "Applicable",
                "taxability": "Taxable", "supply_type": "Goods", "gst_rate": rate, "outer_gst_rate": rate,
                "gst_rates": nested or {}}

    def test_strategy_b_never_runs_for_a_non_rate_failure(self):
        from gst_tally.tally.service import _repair_gst_account_rate_with_fallback
        from gst_tally.tally.response_parser import TallyResponse

        master = _account_master("Purchase", "12", "Alter")
        # Nested rate is correct here -- isolates the failure to the parent
        # group alone, so GST_RATE_DETAILS_INCOMPLETE must not also fire.
        wrong_group_reread = {**self._reread("12", {"CGST": "6", "SGST/UTGST": "6", "IGST": "12"}), "parent": "Wrong Group"}
        verification = _verify_master_properties(master, wrong_group_reread)
        self.assertEqual(verification.get("error_code", ""), "")

        class Client:
            last_http_status = 200
            def import_data(self, payload):
                raise AssertionError("Strategy B must not write anything for a non-rate failure")

        response, refreshed, final_verification, strategy, trace = _repair_gst_account_rate_with_fallback(
            Client(), master, "Test Company", TallyResponse(altered=1), wrong_group_reread, verification)
        self.assertEqual(strategy, "A")
        self.assertIs(final_verification, verification)

    def test_strategy_b_nested_only_succeeds_after_strategy_a_leaves_rate_zero(self):
        from unittest.mock import patch
        from gst_tally.tally.service import _repair_gst_account_rate_with_fallback
        from gst_tally.tally.response_parser import TallyResponse

        master = _account_master("Purchase", "12", "Alter")
        strategy_a_reread = self._reread("0")
        verification_a = _verify_master_properties(master, strategy_a_reread)
        self.assertEqual(verification_a.get("error_code"), "GST_ACCOUNT_RATE_WRITE_FAILED")

        class Client:
            last_http_status = 200
            def import_json(self, payload, object_id):
                return TallyResponse(altered=1)

        with patch("gst_tally.tally.service.ledger_details",
                   return_value=self._reread("12", {"IGST": "12", "CGST": "6", "SGST/UTGST": "6"})):
            response, refreshed, verification, strategy, trace = _repair_gst_account_rate_with_fallback(
                Client(), master, "Test Company", TallyResponse(altered=1), strategy_a_reread, verification_a)

        self.assertEqual(strategy, "B_nested_only")
        self.assertTrue(verification["valid"])
        self.assertEqual(refreshed["outer_gst_rate"], "12")
        self.assertEqual(trace["strategy_a_reread_rate"], "0")

    def test_strategy_b_also_engages_for_gst_rate_details_incomplete_not_only_rate_write_failed(self):
        """Regression: Strategy A's write can leave the outer rate correct
        but the nested breakdown genuinely absent (GST_RATE_DETAILS_INCOMPLETE,
        not GST_ACCOUNT_RATE_WRITE_FAILED) -- Strategy B must still attempt
        its nested_only/flat_only variants for this case too, not stop
        immediately and report failure without ever trying to fix it."""
        from unittest.mock import patch
        from gst_tally.tally.service import _repair_gst_account_rate_with_fallback
        from gst_tally.tally.response_parser import TallyResponse

        master = _account_master("Purchase", "12", "Alter")
        # Outer rate correct, nested breakdown confirmed absent.
        strategy_a_reread = {**self._reread("12"), "gst_rate_details_popup_exists": False, "set_alter_gst_rate_details": "No"}
        verification_a = _verify_master_properties(master, strategy_a_reread)
        self.assertEqual(verification_a.get("error_code"), "GST_RATE_DETAILS_INCOMPLETE")

        class Client:
            last_http_status = 200
            def import_json(self, payload, object_id):
                return TallyResponse(altered=1)

        with patch("gst_tally.tally.service.ledger_details",
                   return_value=self._reread("12", {"IGST": "12", "CGST": "6", "SGST/UTGST": "6"})):
            response, refreshed, verification, strategy, trace = _repair_gst_account_rate_with_fallback(
                Client(), master, "Test Company", TallyResponse(altered=1), strategy_a_reread, verification_a)

        self.assertNotEqual(strategy, "A")
        self.assertTrue(verification["valid"], verification.get("reason"))
        self.assertEqual(refreshed["gst_rates"], {"IGST": "12", "CGST": "6", "SGST/UTGST": "6"})
        self.assertEqual(trace["strategy_b_nested_only_reread_rate"], "12")

    def test_both_strategies_failing_reports_diagnostics_not_fake_success(self):
        from unittest.mock import patch
        from gst_tally.tally.service import _repair_gst_account_rate_with_fallback
        from gst_tally.tally.response_parser import TallyResponse

        master = _account_master("Purchase", "12", "Alter")
        strategy_a_reread = self._reread("0")
        verification_a = _verify_master_properties(master, strategy_a_reread)

        class Client:
            last_http_status = 200
            def import_json(self, payload, object_id):
                return TallyResponse(altered=1)

        with patch("gst_tally.tally.service.ledger_details", return_value=self._reread("0")):
            response, refreshed, verification, strategy, trace = _repair_gst_account_rate_with_fallback(
                Client(), master, "Test Company", TallyResponse(altered=1), strategy_a_reread, verification_a)

        self.assertEqual(strategy, "B_FAILED")
        self.assertFalse(verification["valid"])
        self.assertIn("strategy_b_flat_only_reread_rate", trace)
        self.assertIn("strategy_b_nested_only_reread_rate", trace)
        self.assertIn("missing_fields", trace)

    def test_strategy_b_always_alters_the_exact_ledger_never_a_duplicate(self):
        """Section 21: repair must ALTER 'GST Purchase 12%', never create a
        'GST Purchase 12% (1)' duplicate."""
        from unittest.mock import patch
        from gst_tally.tally.service import _repair_gst_account_rate_with_fallback
        from gst_tally.tally.response_parser import TallyResponse

        master = _account_master("Purchase", "12", "Alter")
        strategy_a_reread = self._reread("0")
        verification_a = _verify_master_properties(master, strategy_a_reread)
        sent_names = []

        class Client:
            last_http_status = 200
            def import_json(self, payload, object_id):
                ledger = payload["tallymessage"][0]
                sent_names.append(ledger["metadata"]["name"])
                sent_names.append(ledger["metadata"]["action"])
                return TallyResponse(altered=1)

        with patch("gst_tally.tally.service.ledger_details",
                   return_value=self._reread("12", {"IGST": "12", "CGST": "6", "SGST/UTGST": "6"})):
            _repair_gst_account_rate_with_fallback(Client(), master, "Test Company", TallyResponse(altered=1), strategy_a_reread, verification_a)

        self.assertEqual(set(sent_names), {"GST Purchase 12%", "alter"})


class RequestPayloadNeverBlankTests(TestCase):
    """The exact debugging defect reported: 'View Request Payload' showed
    '-' for a master Tally had already returned ALTERED=1/CREATED=1 for,
    because the master result/diagnostics dict never actually stored the
    native JSON that was sent. request_payload must contain the real final
    JSON for every write attempt, success or failure."""

    def _ledger_from_payload(self, payload):
        return json.loads(payload)["tallymessage"][0]

    def _payload_rates(self, payload):
        ledger = self._ledger_from_payload(payload)
        return [Decimal(row["gstrate"]) for row in ledger["gstdetails"][0]["statewisedetails"][0]["ratedetails"]
                if "gstrate" in row]

    def test_final_master_request_payload_is_never_blank_for_an_attempted_write(self):
        from gst_tally.tally.service import _final_master_request_payload

        master = {"master_type": "Purchase", "name": "GST Purchase 12%", "group": "Purchase Accounts",
                  "gst_rate": "12", "supply_type": "Goods", "applicable_from": "2025-04-01", "action": "Alter"}
        for strategy in ("A", "B_nested_only", "B_flat_only", "B_FAILED"):
            with self.subTest(strategy=strategy):
                payload = _final_master_request_payload(master, "Test Company", strategy)
                self.assertTrue(payload.strip())
                ledger = self._ledger_from_payload(payload)
                self.assertEqual(ledger["metadata"]["name"], "GST Purchase 12%")
                self.assertEqual(ledger["metadata"]["action"], "alter")
                if strategy != "B_nested_only":
                    self.assertEqual(Decimal(ledger["rateoftaxcalculation"]), Decimal("12"))
                if strategy != "B_flat_only":
                    self.assertIn(Decimal("12"), self._payload_rates(payload))

    def test_b_failed_request_payload_shows_the_full_nested_structure_not_the_flat_only_attempt(self):
        """Regression: 'View Request Payload' for a fully-exhausted (both
        strategies failed) Purchase/Sales master previously showed the
        LAST-tried variant, which for the B_FAILED terminal state was
        flat_only -- a payload that, by design, has no nested GST detail
        collections at all. That looked exactly like 'the nested GST builder
        is never used', which is not true: Strategy A (rate_mode="both")
        already sends the full nested structure every time; only the
        diagnostic display was misleading. B_FAILED must now show that full
        "both" payload, proving the nested rows genuinely were sent."""
        from gst_tally.tally.service import _final_master_request_payload

        for rate, expected_half in (("5", "2.5"), ("12", "6"), ("18", "9"), ("28", "14")):
            with self.subTest(rate=rate):
                master = {"master_type": "Purchase", "name": f"GST Purchase {rate}%", "group": "Purchase Accounts",
                          "gst_rate": rate, "supply_type": "Goods", "applicable_from": "2025-04-01", "action": "Alter"}
                payload = _final_master_request_payload(master, "Test Company", "B_FAILED")
                self.assertIn('"gstdetails"', payload)
                self.assertIn('"statewisedetails"', payload)
                self.assertIn('"ratedetails"', payload)
                ledger = self._ledger_from_payload(payload)
                rates = self._payload_rates(payload)
                self.assertEqual(Decimal(ledger["rateoftaxcalculation"]), Decimal(rate))
                self.assertIn(Decimal(expected_half), rates)
                self.assertIn(Decimal(rate), rates)

    def test_write_master_raises_before_http_post_if_a_gst_account_payload_would_be_incomplete(self):
        """Section 6's guard, exercised directly: if a Sales/Purchase
        account ledger with a non-zero rate were ever sent without the
        nested GST Rate Details (rate_mode="both"/"nested_only"), the write
        must never reach Tally -- raise GST_MASTER_BUILDER_INCOMPLETE
        instead. rate_mode="flat_only" (Strategy B's deliberate experiment)
        is the one legitimate exception and must NOT raise."""
        from gst_tally.tally.service import _assert_gst_master_payload_complete

        master = {"master_type": "Purchase", "name": "GST Purchase 18%", "group": "Purchase Accounts", "gst_rate": "18"}
        incomplete_payload = {"tallymessage": [{
            "metadata": {"type": "Ledger", "name": "GST Purchase 18%", "action": "alter"},
            "parent": "Purchase Accounts", "taxtype": "Others", "gstapplicable": TALLY_APPLICABLE,
            "gsttypeofsupply": "Goods", "rateoftaxcalculation": " 18",
        }]}
        with self.assertRaises(ValueError) as raised:
            _assert_gst_master_payload_complete(master, incomplete_payload, rate_mode="both")
        self.assertIn("GST_MASTER_BUILDER_INCOMPLETE", str(raised.exception))
        # A genuinely complete payload never raises.
        complete_payload = build_json_master({**master, "action": "Alter"}, "Test Company")
        _assert_gst_master_payload_complete(master, complete_payload, rate_mode="both")
        # The deliberate flat_only experiment is never flagged as incomplete.
        _assert_gst_master_payload_complete(master, incomplete_payload, rate_mode="flat_only")

    def test_a_failed_step6_gst_account_master_carries_its_real_request_payload(self):
        """End-to-end: when GST Purchase 18% fails repair (both strategies
        exhausted), the returned master result must carry the real native
        JSON that was sent, and the voucher-level tally_error blocked by
        that master must reuse the same value -- never the hardcoded ''
        this bug report found."""
        from datetime import date
        from decimal import Decimal as D
        from unittest.mock import patch
        from gst_tally.models import GSTImportBatch, GSTInvoice
        from gst_tally.tally.service import import_batch
        from gst_tally.tally.response_parser import TallyResponse

        class Client:
            last_http_status = 200
            base_url = "http://127.0.0.1:9000"
            def import_json(self, payload, object_id):
                return TallyResponse(altered=1)
            def post(self, payload):
                return b"<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"

        def always_broken(name, company="", client=None):
            if name == "GST Purchase 18%":
                # Exact live evidence from the bug report: GSTDETAILS.LIST
                # (history) exists with the outer scalars, but the nested
                # StatewiseDetails/RateDetails breakdown never persisted.
                return {"exists": True, "name": name, "parent": "Purchase Accounts",
                        "gst_applicable": "Applicable", "taxability": "Taxable", "supply_type": "Goods",
                        "gst_rate": "18", "outer_gst_rate": "18", "gst_rate_history_exists": True,
                        "gst_rate_details_popup_exists": False, "gst_rates": {}}
            # Every other required master (party, tax ledgers) is genuinely
            # valid -- only the account ledger under test must fail, so the
            # single failed dependency is unambiguous.
            tax_type = "SGST/UTGST" if "SGST" in name.upper() else "CGST"
            return {"exists": True, "name": name, "parent": "Sundry Creditors", "gstin": "33AAACB2894G1ZJ",
                    "state": "Tamil Nadu", "country": "India", "registration_type": "Regular",
                    "duty_type": "GST", "rounding_method": "Not Applicable", "tax_type": tax_type, "gst_rate": "9"}

        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.json", file_type="JSON", gst_return_type="GSTR2B", company_gstin="33AFHPM6103Q1Z8")
        GSTInvoice.objects.create(
            import_batch=batch, invoice_no="1", invoice_date=date(2025, 4, 1),
            customer_gstin="33AAACB2894G1ZJ", taxable_value=D("100.00"), tax_percent=D("18"),
            cgst=D("9.00"), sgst=D("9.00"), igst=D("0.00"), invoice_value=D("118.00"), place_of_supply="Tamil Nadu")

        status = {"read_connected": True, "company_detected": True, "company_open": True,
                  "company_name": "SRI MAHALAKSHMI TRADERS", "company_gstin": "33AFHPM6103Q1Z8",
                  "company_state": "Tamil Nadu", "can_import": True, "message": "ok"}
        with patch("gst_tally.tally.service.odbc_company_status", return_value=status), \
             patch("gst_tally.tally.service.odbc_company_period",
                   return_value={"company": "SRI MAHALAKSHMI TRADERS", "financial_year_from": date(2025, 4, 1),
                                 "books_from": date(2025, 4, 1), "ending_at": date(2026, 3, 31)}), \
             patch("gst_tally.tally.service.odbc_existing_masters",
                   return_value=({"gst purchase 18%": "GST Purchase 18%"}, {}, {})), \
             patch("gst_tally.tally.service.ledger_details", side_effect=always_broken):
            result = import_batch(batch, Client())

        purchase_master = next(row for row in result["masters"] if row["name"] == "GST Purchase 18%")
        self.assertEqual(purchase_master["status"], "Failed")
        self.assertTrue(purchase_master["request_payload"].strip())
        self.assertIn('"name": "GST Purchase 18%"', purchase_master["request_payload"])
        self.assertNotEqual(purchase_master["request_payload"], "")
        # The gst_ledger_master_trace diagnostic correctly names the exact
        # missing collection instead of leaving the reader to diff by eye.
        trace = purchase_master["diagnostics"]["gst_ledger_master_trace"]
        self.assertEqual(trace["first_structural_difference"],
                         "statewisedetails was sent but is absent from the Tally re-read")

        blocked_voucher = next(row for row in result["results"] if row["status"] == "Master Setup Failed")
        self.assertEqual(blocked_voucher["tally_error"]["request_payload"], purchase_master["request_payload"])
        self.assertNotEqual(blocked_voucher["tally_error"]["request_payload"], "")


class GstLedgerMasterTraceNullSafetyTests(SimpleTestCase):
    """Regression for the exact crash reported against POST
    .../tally-masters/prepare/: _gst_ledger_master_trace unconditionally did
    payload["tallymessage"][0], so any payload shape it didn't expect --
    including _native_master_payload legitimately returning None -- turned
    a valid master-prepare request into an unhandled TypeError (HTTP 500).
    This is a diagnostics-only helper; it must degrade to empty/blank
    diagnostics for a bad payload, never raise."""

    def _master(self, rate="12", action="Alter"):
        return {"master_type": "Purchase", "name": f"GST Purchase {rate}%", "group": "Purchase Accounts",
                "gst_rate": rate, "supply_type": "Goods", "applicable_from": "2025-04-04", "action": action}

    def _response(self, altered=1, created=0, errors=0, exceptions=0):
        from gst_tally.tally.response_parser import TallyResponse
        return TallyResponse(altered=altered, created=created, errors=errors, exceptions=exceptions)

    def _reread(self, outer_rate="12", nested=None, popup_exists=False, history_exists=True):
        return {"exists": True, "name": "GST Purchase 12%", "parent": "Purchase Accounts",
                "gst_applicable": "Applicable", "taxability": "Taxable", "supply_type": "Goods",
                "gst_rate": outer_rate, "outer_gst_rate": outer_rate,
                "gst_rate_details": "Specify Details Here",
                "gst_rate_details_popup_exists": popup_exists, "gst_rate_history_exists": history_exists,
                "gst_rates": nested or {}}

    def test_a_native_master_payload_returning_none_does_not_raise(self):
        from unittest.mock import patch
        from gst_tally.tally.service import _gst_ledger_master_trace, _verify_master_properties

        master = self._master()
        reread = self._reread()
        verification = _verify_master_properties(master, reread)
        with patch("gst_tally.tally.service._native_master_payload", return_value=None):
            trace = _gst_ledger_master_trace(master, "Test Company", self._response(), reread, verification)
        self.assertFalse(trace["payload_available"])
        self.assertEqual(trace["payload_source"], "XML_OR_NON_NATIVE_WRITE")
        self.assertEqual(trace["actual_transport"], "XML")
        self.assertEqual(trace["rate_details_count"], 0)

    def test_b_empty_dict_payload_does_not_raise(self):
        from unittest.mock import patch
        from gst_tally.tally.service import _gst_ledger_master_trace, _verify_master_properties

        master = self._master()
        reread = self._reread()
        verification = _verify_master_properties(master, reread)
        with patch("gst_tally.tally.service._native_master_payload", return_value={}):
            trace = _gst_ledger_master_trace(master, "Test Company", self._response(), reread, verification)
        self.assertTrue(trace["payload_available"])
        self.assertEqual(trace["rate_details_count"], 0)
        self.assertIsNone(trace["outer_rate_written"])

    def test_c_empty_tallymessage_list_does_not_raise(self):
        from unittest.mock import patch
        from gst_tally.tally.service import _gst_ledger_master_trace, _verify_master_properties

        master = self._master()
        reread = self._reread()
        verification = _verify_master_properties(master, reread)
        with patch("gst_tally.tally.service._native_master_payload", return_value={"tallymessage": []}):
            trace = _gst_ledger_master_trace(master, "Test Company", self._response(), reread, verification)
        self.assertTrue(trace["payload_available"])
        self.assertEqual(trace["rate_details_count"], 0)

    def test_d_missing_nested_gst_collections_does_not_raise(self):
        from unittest.mock import patch
        from gst_tally.tally.service import _gst_ledger_master_trace, _verify_master_properties

        master = self._master()
        reread = self._reread()
        verification = _verify_master_properties(master, reread)
        # tallymessage[0] exists but has none of gstdetails/statewisedetails/ratedetails.
        broken_payload = {"tallymessage": [{"name": "GST Purchase 12%"}]}
        with patch("gst_tally.tally.service._native_master_payload", return_value=broken_payload):
            trace = _gst_ledger_master_trace(master, "Test Company", self._response(), reread, verification)
        self.assertTrue(trace["payload_available"])
        self.assertFalse(trace["gst_details_written"])
        self.assertFalse(trace["statewise_details_written"])
        self.assertEqual(trace["rate_details_count"], 0)

    def test_e_real_alter_response_with_no_nested_rate_details_is_structured_incomplete_not_500(self):
        """The exact real-world case from the bug report: GST Purchase 12%
        Alter comes back ALTERED=1/ERRORS=0/EXCEPTIONS=0 (Tally accepted the
        write), but the reread shows only the outer rate survived --
        gst_rates is empty and the GST Rate Details popup never persisted.
        That is GST_RATE_DETAILS_INCOMPLETE, not a fake success and not a
        crash, regardless of whether the diagnostic JSON payload trace is
        available."""
        from unittest.mock import patch
        from gst_tally.tally.service import _gst_ledger_master_trace, _verify_master_properties

        master = self._master(rate="12", action="Alter")
        reread = self._reread(outer_rate="12", nested={}, popup_exists=False, history_exists=True)
        verification = _verify_master_properties(master, reread)

        self.assertFalse(verification["valid"])
        self.assertEqual(verification.get("error_code"), "GST_RATE_DETAILS_INCOMPLETE")

        response = self._response(altered=1, created=0, errors=0, exceptions=0)
        with patch("gst_tally.tally.service._native_master_payload", return_value=None):
            trace = _gst_ledger_master_trace(master, "Test Company", response, reread, verification)
        self.assertFalse(trace["verified"])
        self.assertEqual(trace["write_response"]["altered"], 1)
        self.assertEqual(trace["write_response"]["errors"], 0)

    def test_f_proper_nested_rate_details_present_verification_succeeds(self):
        from gst_tally.tally.service import _gst_ledger_master_trace, _verify_master_properties

        master = self._master(rate="12", action="Alter")
        reread = self._reread(outer_rate="12", nested={"IGST": "12", "CGST": "6", "SGST/UTGST": "6"},
                               popup_exists=True, history_exists=True)
        verification = _verify_master_properties(master, reread)
        self.assertTrue(verification["valid"], verification.get("reason"))

        response = self._response(altered=1, created=0, errors=0, exceptions=0)
        trace = _gst_ledger_master_trace(master, "Test Company", response, reread, verification)
        self.assertTrue(trace["verified"])
        self.assertTrue(trace["payload_available"])
        self.assertEqual(trace["rate_details_count"], 5)

    def test_ledger_rate_trace_also_survives_a_none_payload(self):
        """_ledger_rate_trace has the same payload["tallymessage"][0] shape
        and is called from the same diagnostic paths -- it must not raise
        either."""
        from unittest.mock import patch
        from gst_tally.tally.service import _ledger_rate_trace

        master = self._master()
        reread = self._reread()
        with patch("gst_tally.tally.service._native_master_payload", return_value=None):
            trace = _ledger_rate_trace(master, "Test Company", reread)
        self.assertFalse(trace["payload_available"])
        self.assertIsNone(trace["json_model.rateoftaxcalculation"])
