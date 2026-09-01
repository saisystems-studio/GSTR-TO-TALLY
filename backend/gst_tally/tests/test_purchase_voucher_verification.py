"""Regression tests for the Step 6 query-back verification of Purchase
vouchers with multiple GST rates.

Root cause of the live bug this guards against: TallyPrime 7.1's Voucher
Collection export (FETCH-filtered) never populates RATEOFINVOICETAX/
RATEDETAILS on LEDGERENTRIES.LIST for these vouchers, and the Day Book
fallback read is not reliably available either. Every taxable "GST Purchase
NN%" ledger entry therefore came back with gst_rate == "", and the old
(ledger, rate) match key rejected every one of them as "missing", even
though the voucher was created correctly and the ledger's own name already
encodes its rate unambiguously. See voucher_verification._controlled_name_rate.
"""
from decimal import Decimal
from django.test import SimpleTestCase

from gst_tally.tally.voucher_verification import verify_voucher


COMPANY = "SRI MAHALAKSHMI TRADERS,"


def _entry(ledger, amount, is_deemed_positive, rate_tag=""):
    rate_xml = f"<RATEOFINVOICETAX.LIST><RATEOFINVOICETAX>{rate_tag}</RATEOFINVOICETAX></RATEOFINVOICETAX.LIST>" if rate_tag else ""
    return (f"<LEDGERNAME TYPE=\"String\">{ledger}</LEDGERNAME>"
            f"<ISDEEMEDPOSITIVE TYPE=\"Logical\">{is_deemed_positive}</ISDEEMEDPOSITIVE>"
            f"<AMOUNT TYPE=\"Amount\">{amount}</AMOUNT>{rate_xml}")


def _voucher_xml(entries, reference="1", number="267", master_id="587",
                 party="RE SUSTAINABILITY IWM SOLUTIONS LIMITED", duplicate_as_all_ledger_entries=True):
    """Build a raw Voucher Collection export matching the exact shape observed
    on the live instance: no explicit rate fields on LEDGERENTRIES.LIST, and
    the same postings mirrored under ALLLEDGERENTRIES.LIST."""
    ledger_list = "".join(f"<LEDGERENTRIES.LIST>{body}</LEDGERENTRIES.LIST>" for body in entries)
    all_ledger_list = "".join(f"<ALLLEDGERENTRIES.LIST>{body}</ALLLEDGERENTRIES.LIST>" for body in entries) if duplicate_as_all_ledger_entries else ""
    return (f'<ENVELOPE><BODY><DATA><COLLECTION>'
            f'<VOUCHER VCHTYPE="Purchase"><DATE TYPE="Date">20250401</DATE>'
            f'<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>'
            f'<PARTYLEDGERNAME TYPE="String">{party}</PARTYLEDGERNAME>'
            f'<VOUCHERNUMBER>{number}</VOUCHERNUMBER><REFERENCE TYPE="String">{reference}</REFERENCE>'
            f'<MASTERID TYPE="Number"> {master_id}</MASTERID>'
            f'{ledger_list}{all_ledger_list}'
            f'</VOUCHER></COLLECTION></DATA></BODY></ENVELOPE>').encode()


def _real_voucher_587():
    """The exact voucher this bug was diagnosed against: invoice #1, two GST
    rates (5% and 18%), each with its own CGST/SGST tax ledgers."""
    return {
        "invoice_number": "1", "invoice_date": "2025-04-01", "voucher_type": "Purchase",
        "party": {"name": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED", "gstin": "X"},
        "rate_allocations": [
            {"gst_rate": "5", "sales_ledger": "GST Purchase 5%", "taxable_value": "190.00"},
            {"gst_rate": "18", "sales_ledger": "GST Purchase 18%", "taxable_value": "5960.00"},
        ],
        "tax_allocations": [
            {"ledger": "Input CGST 2.5%", "component": "CGST", "amount": "4.75", "gst_rate": "2.5"},
            {"ledger": "Input SGST 2.5%", "component": "SGST", "amount": "4.75", "gst_rate": "2.5"},
            {"ledger": "Input CGST 9%", "component": "CGST", "amount": "536.40", "gst_rate": "9"},
            {"ledger": "Input SGST 9%", "component": "SGST", "amount": "536.40", "gst_rate": "9"},
        ],
        "invoice_total": "7232.30", "rounding_adjustment": "0.00",
    }


def _real_entries_587():
    return [
        _entry("RE SUSTAINABILITY IWM SOLUTIONS LIMITED", "7232.30", "No"),
        _entry("GST Purchase 5%", "-190.00", "Yes"),
        _entry("GST Purchase 18%", "-5960.00", "Yes"),
        _entry("Input CGST 2.5%", "-4.75", "Yes"),
        _entry("Input SGST 2.5%", "-4.75", "Yes"),
        _entry("Input CGST 9%", "-536.40", "Yes"),
        _entry("Input SGST 9%", "-536.40", "Yes"),
    ]


class Client:
    def __init__(self, raw):
        self.raw = raw

    def post(self, payload, headers=None):
        return self.raw


class MultipleRateVerificationTests(SimpleTestCase):
    """(1)(3)(8) Multiple GST rates on one Purchase invoice must verify when
    the query-back never carries an explicit rate field, by resolving each
    taxable ledger's rate from its own canonical name instead."""

    def test_multi_rate_purchase_voucher_verifies_without_explicit_rate_fields(self):
        raw = _voucher_xml(_real_entries_587())
        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertTrue(result["found"], result.get("reason"))
        self.assertEqual(result["expected_gst_rates"], ["5", "18"])
        self.assertEqual(result["actual_gst_rates"], ["5", "18"])
        diff = result["verification_difference"]
        self.assertEqual(diff["missing_purchase_allocations"], [])
        self.assertEqual(diff["missing_tax_ledgers"], [])
        self.assertEqual(diff["amount_differences"], [])

    def test_xml_entry_order_does_not_affect_verification(self):
        entries = list(reversed(_real_entries_587()))
        raw = _voucher_xml(entries)
        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertTrue(result["found"], result.get("reason"))

    def test_allledgerentries_and_ledgerentries_duplication_is_not_double_counted(self):
        """The live export mirrors every posting under both LEDGERENTRIES.LIST
        and ALLLEDGERENTRIES.LIST; reading both must not double the entries."""
        raw = _voucher_xml(_real_entries_587(), duplicate_as_all_ledger_entries=True)
        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertTrue(result["found"], result.get("reason"))
        self.assertEqual(len(result["raw_tally_ledger_fields"]), 7)

    def test_decimal_formatting_differences_are_equivalent(self):
        voucher = _real_voucher_587()
        voucher["tax_allocations"][2]["amount"] = "536.4"  # vs Tally's "536.40"
        raw = _voucher_xml(_real_entries_587())

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertTrue(result["found"], result.get("reason"))

    def test_rate_formatting_differences_are_equivalent(self):
        voucher = _real_voucher_587()
        voucher["rate_allocations"][1]["gst_rate"] = "18.00"  # vs ledger name's "18"
        raw = _voucher_xml(_real_entries_587())

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertTrue(result["found"], result.get("reason"))

    def test_custom_ledger_without_a_rate_in_its_name_still_fails_closed(self):
        """The controlled name fallback must never be applied outside the
        exact canonical GST Purchase/Sales/Input/Output naming scheme -- an
        unrelated ledger's missing rate must still be reported missing."""
        entries = [
            _entry("Supplier", "1000.00", "No"),
            _entry("Domestic Purchases A", "-1000.00", "Yes"),
        ]
        raw = _voucher_xml(entries, party="Supplier")
        voucher = {"invoice_number": "1", "invoice_date": "2025-04-01", "voucher_type": "Purchase",
                   "party": {"name": "Supplier"},
                   "rate_allocations": [{"account_ledger": "Domestic Purchases A", "gst_rate": "18", "taxable_value": "1000"}],
                   "tax_allocations": [], "invoice_total": "1000", "rounding_adjustment": "0.00"}

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertFalse(result["found"])
        self.assertEqual(result["verification_difference"]["missing_purchase_allocations"],
                         [{"ledger": "Domestic Purchases A", "gst_rate": "18"}])


class GenuineMismatchDiagnosticsTests(SimpleTestCase):
    """(2)(6)(9)(11) A real accounting difference must still fail, with an
    exact ledger/rate/amount diagnostic -- never the generic summary alone."""

    def test_wrong_tax_ledger_amount_is_reported_precisely(self):
        entries = _real_entries_587()
        entries[5] = _entry("Input CGST 9%", "-500.00", "Yes")  # should be -536.40
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertFalse(result["found"])
        self.assertEqual(result["reason"],
                         "Mismatch type: TAX_LEDGER_AMOUNT. Ledger: Input CGST 9%. "
                         "Expected: 536.40. Actual: 500.00. Difference: -36.40.")
        self.assertEqual(result["verification_difference"]["amount_differences"],
                         [{"ledger": "Input CGST 9%", "kind": "TAX_LEDGER_AMOUNT", "expected": "536.40", "actual": "500.00"}])

    def test_wrong_taxable_ledger_amount_is_reported_precisely(self):
        entries = _real_entries_587()
        entries[2] = _entry("GST Purchase 18%", "-5900.00", "Yes")  # should be -5960.00
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertFalse(result["found"])
        self.assertIn("TAXABLE_LEDGER_AMOUNT", result["reason"])
        self.assertIn("GST Purchase 18%", result["reason"])

    def test_missing_taxable_rate_bucket_is_reported_as_missing_not_generic(self):
        voucher = _real_voucher_587()
        voucher["rate_allocations"].append(
            {"gst_rate": "12", "sales_ledger": "GST Purchase 12%", "taxable_value": "300.00"})
        raw = _voucher_xml(_real_entries_587())

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertFalse(result["found"])
        self.assertIn("MISSING_TAXABLE_LEDGER", result["reason"])
        self.assertIn("GST Purchase 12%", result["reason"])


class RoundOffVerificationTests(SimpleTestCase):
    """(10) Round Off is compared as its own entry, positive or negative,
    never folded into the taxable/tax ledger comparison."""

    def test_positive_round_off_matches_when_present(self):
        voucher = _real_voucher_587()
        voucher["rounding_adjustment"] = "0.30"
        entries = _real_entries_587() + [_entry("Round Off", "-0.30", "Yes")]
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertTrue(result["found"], result.get("reason"))

    def test_negative_round_off_matches_when_present(self):
        voucher = _real_voucher_587()
        voucher["rounding_adjustment"] = "-0.30"
        entries = _real_entries_587() + [_entry("Round Off", "0.30", "Yes")]
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertTrue(result["found"], result.get("reason"))

    def test_missing_round_off_ledger_fails_with_round_off_mismatch(self):
        voucher = _real_voucher_587()
        voucher["rounding_adjustment"] = "0.30"
        raw = _voucher_xml(_real_entries_587())  # no Round Off entry at all

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertFalse(result["found"])
        self.assertIn("ROUND_OFF", result["reason"])

    def test_round_off_is_not_treated_as_a_taxable_ledger(self):
        voucher = _real_voucher_587()
        voucher["rounding_adjustment"] = "0.30"
        entries = _real_entries_587() + [_entry("Round Off", "-0.30", "Yes")]
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, voucher)

        self.assertNotIn("Round Off", result["actual_purchase_ledgers"])
        self.assertNotIn("Round Off", result["actual_tax_ledgers"])


class PartyLedgerVerificationTests(SimpleTestCase):
    """(5)(12) The party ledger is checked separately from GST allocations:
    amount must match the invoice total, and Dr/Cr direction (via Tally's own
    ISDEEMEDPOSITIVE flag, not a raw amount sign) must be internally
    consistent -- but only when that data is actually present."""

    def test_correct_party_amount_and_direction_verifies(self):
        raw = _voucher_xml(_real_entries_587())
        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertTrue(result["found"], result.get("reason"))

    def test_wrong_party_amount_fails_as_party_mismatch(self):
        entries = _real_entries_587()
        entries[0] = _entry("RE SUSTAINABILITY IWM SOLUTIONS LIMITED", "7000.00", "No")  # should be 7232.30
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertFalse(result["found"])
        self.assertIn("PARTY_LEDGER", result["reason"])

    def test_party_and_taxable_entries_on_the_same_accounting_side_fails(self):
        entries = _real_entries_587()
        # Corrupt the party entry's direction flag so it matches the taxable
        # side instead of opposing it -- a structurally broken voucher.
        entries[0] = _entry("RE SUSTAINABILITY IWM SOLUTIONS LIMITED", "7232.30", "Yes")
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertFalse(result["found"])
        self.assertIn("PARTY_LEDGER", result["reason"])
        self.assertIn("Dr/Cr", result["reason"])

    def test_absent_direction_data_is_not_treated_as_a_direction_conflict(self):
        """A query shape that never populates ISDEEMEDPOSITIVE (some report
        types omit it) must not be misread as every entry sharing one side."""
        entries = [_entry(row["ledger"], row.get("amount", "0"), "")
                  for row in [{"ledger": "RE SUSTAINABILITY IWM SOLUTIONS LIMITED", "amount": "7232.30"},
                              {"ledger": "GST Purchase 5%", "amount": "-190.00"},
                              {"ledger": "GST Purchase 18%", "amount": "-5960.00"},
                              {"ledger": "Input CGST 2.5%", "amount": "-4.75"},
                              {"ledger": "Input SGST 2.5%", "amount": "-4.75"},
                              {"ledger": "Input CGST 9%", "amount": "-536.40"},
                              {"ledger": "Input SGST 9%", "amount": "-536.40"}]]
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertTrue(result["found"], result.get("reason"))

    def test_absent_party_ledger_entry_is_not_reported_as_a_mismatch(self):
        """A query shape that doesn't carry a party ledger row at all (e.g. a
        summary Day Book record) is simply not checked here, matching
        find_voucher's existing rule that an absent field is not a mismatch."""
        entries = _real_entries_587()[1:]  # drop the party ledger row entirely
        raw = _voucher_xml(entries)

        result = verify_voucher(Client(raw), COMPANY, _real_voucher_587())

        self.assertTrue(result["found"], result.get("reason"))
