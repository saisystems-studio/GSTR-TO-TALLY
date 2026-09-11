"""Regression coverage for the Sandbox GSTIN lookup DEBUG LOG (task spec's
DEBUG LOG section): _log_party_lookup() must emit exactly the requested
per-GSTIN field set, so it's obvious where Sandbox data is being lost
between the raw API response and the final party master fields --
sandbox_* fields must never be fabricated for a party whose identity did
not actually come from a completed Sandbox lookup.
"""
import io
from contextlib import redirect_stdout
from types import SimpleNamespace

from django.test import SimpleTestCase

from gst_tally.services.party_lookup import _log_party_lookup

GSTIN = "33BKLPB9944A1ZD"

REQUIRED_DEBUG_LINES = (
    "gstin:", "sandbox_lookup_attempted:", "sandbox_status_code:", "sandbox_success:",
    "sandbox_lgnm:", "sandbox_tradeNam:", "sandbox_pradr_present:", "sandbox_pincode:",
    "mapped_ledger_name:", "mapped_address:", "mapped_state:", "mapped_pincode:",
    "sandbox_response_present:", "mapped_trade_name:", "mapped_legal_name:",
)


def _capture(gstin, status, party):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _log_party_lookup(gstin, status, party)
    return buffer.getvalue()


class DebugLogFieldsPresentTests(SimpleTestCase):
    def test_every_requested_field_is_logged(self):
        party = SimpleNamespace(
            gstin=GSTIN, trade_name="AARTHI AGENCIES", legal_name="AARTHI ENTERPRISES PRIVATE LIMITED",
            principal_place_of_business="Door No 12, Anna Nagar Main Road, Madurai", address="",
            state_name="Tamil Nadu", pincode="625020", taxpayer_type="Regular",
            lookup_source="sandbox", lookup_status="Success",
            _lookup_diagnostics={"lookup_attempted": True, "http_status": 200},
        )
        output = _capture(GSTIN, "Fetched", party)
        self.assertIn("=== SANDBOX LOOKUP DEBUG ===", output)
        for line in REQUIRED_DEBUG_LINES:
            self.assertIn(line, output)


class DebugLogAccurateForSuccessfulSandboxLookupTests(SimpleTestCase):
    def setUp(self):
        self.party = SimpleNamespace(
            gstin=GSTIN, trade_name="AARTHI AGENCIES", legal_name="AARTHI ENTERPRISES PRIVATE LIMITED",
            principal_place_of_business="Door No 12, Anna Nagar Main Road, Madurai", address="",
            state_name="Tamil Nadu", pincode="625020", taxpayer_type="Regular",
            lookup_source="sandbox", lookup_status="Success",
            _lookup_diagnostics={"lookup_attempted": True, "http_status": 200},
        )
        self.output = _capture(GSTIN, "Fetched", self.party)

    def test_sandbox_success_is_true(self):
        self.assertIn("sandbox_success: True", self.output)

    def test_sandbox_raw_fields_match_the_sandbox_response(self):
        self.assertIn("sandbox_lgnm: AARTHI ENTERPRISES PRIVATE LIMITED", self.output)
        self.assertIn("sandbox_tradeNam: AARTHI AGENCIES", self.output)
        self.assertIn("sandbox_pradr_present: True", self.output)
        self.assertIn("sandbox_pincode: 625020", self.output)

    def test_mapped_fields_reflect_the_resolved_party_master(self):
        self.assertIn("mapped_ledger_name: AARTHI AGENCIES", self.output)
        self.assertIn("mapped_address: Door No 12, Anna Nagar Main Road, Madurai", self.output)
        self.assertIn("mapped_state: Tamil Nadu", self.output)
        self.assertIn("mapped_pincode: 625020", self.output)

    def test_mapped_trade_and_legal_name_and_response_present(self):
        self.assertIn("sandbox_response_present: True", self.output)
        self.assertIn("mapped_trade_name: AARTHI AGENCIES", self.output)
        self.assertIn("mapped_legal_name: AARTHI ENTERPRISES PRIVATE LIMITED", self.output)


class DebugLogNeverFakesSandboxOnFailureTests(SimpleTestCase):
    """Sandbox lookup failure / source-fallback identity must show blank
    sandbox_* fields -- never fabricated, even though mapped_* fields can
    still be populated from source data."""

    def test_source_fallback_persisted_party_shows_blank_sandbox_fields(self):
        party = SimpleNamespace(
            gstin="33ABCDE1234F1Z5", trade_name="Fresh Source Name", legal_name="",
            principal_place_of_business="", address="", state_name="", pincode="", taxpayer_type="",
            lookup_source="IMPORT_SOURCE", lookup_status="Sandbox Lookup Failed",
            _lookup_diagnostics={"lookup_attempted": True, "http_status": 500},
        )
        output = _capture("33ABCDE1234F1Z5", "Sandbox Lookup Failed", party)

        self.assertIn("sandbox_success: False", output)
        self.assertIn("sandbox_lgnm: \n", output)
        self.assertIn("sandbox_tradeNam: \n", output)
        self.assertIn("sandbox_pradr_present: False", output)
        self.assertIn("sandbox_pincode: \n", output)
        # mapped_ledger_name/mapped_trade_name still reflect the real
        # source-fallback name -- only the sandbox_* raw fields are
        # suppressed. mapped_legal_name stays blank since none was ever
        # supplied by source or a completed Sandbox lookup.
        self.assertIn("mapped_ledger_name: Fresh Source Name", output)
        self.assertIn("mapped_trade_name: Fresh Source Name", output)
        self.assertIn("mapped_legal_name: \n", output)

    def test_no_party_record_at_all_shows_blank_sandbox_and_gstin_mapped_name(self):
        output = _capture("33WHATEVER00Z1Z9", "Pending", None)

        self.assertIn("sandbox_lookup_attempted: False", output)
        self.assertIn("sandbox_status_code: None", output)
        self.assertIn("sandbox_success: False", output)
        self.assertIn("mapped_ledger_name: 33WHATEVER00Z1Z9", output)

    def test_sandbox_status_present_but_lookup_source_not_sandbox_still_blank(self):
        # A party with a genuine HTTP status recorded (e.g. a later attempt
        # failed) but whose CURRENT identity is source-derived must not
        # retroactively claim sandbox_success.
        party = SimpleNamespace(
            gstin=GSTIN, trade_name="Source Name", legal_name="", principal_place_of_business="", address="",
            state_name="", pincode="", taxpayer_type="", lookup_source="IMPORT_SOURCE",
            lookup_status="Sandbox Lookup Failed", _lookup_diagnostics={"lookup_attempted": True, "http_status": 401},
        )
        output = _capture(GSTIN, "Sandbox Lookup Failed", party)
        self.assertIn("sandbox_success: False", output)
        self.assertIn("sandbox_status_code: 401", output)
