"""Party Master Sandbox enrichment: normalized_party_result() must expose
per-field provenance (data_source) for the enrichment fields the backend
party object / Step 4 master preview surface, and an existing Tally party
ledger's optional fields (Pincode, like the existing Address rule) must only
ever be filled when genuinely blank -- never overwritten just because
Sandbox/source now reports something different (task spec's SAFE UPDATE
RULE). Sandbox lookup caching, structured address normalization, and the
core name-resolution priority were already correct before this change and
are untouched here (verified against the existing test suite).
"""
from types import SimpleNamespace

from django.test import SimpleTestCase

from gst_tally.services.party_lookup import normalized_party_result
from gst_tally.tally.service import _verify_master_properties

GSTIN = "33BKLPB9944A1ZD"


def _sandbox_party(**overrides):
    defaults = dict(gstin=GSTIN, trade_name="AARTHI AGENCIES", legal_name="AARTHI ENTERPRISES PRIVATE LIMITED",
                     principal_place_of_business="Door No 12, Anna Nagar Main Road, Madurai", address="",
                     state_name="Tamil Nadu", pincode="625020", taxpayer_type="Regular",
                     lookup_source="sandbox", lookup_status="Success")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class AcceptanceTestPartyObjectTests(SimpleTestCase):
    def test_sandbox_returns_full_details_object_matches_exactly(self):
        result = normalized_party_result(GSTIN, _sandbox_party(), {"party_name": "AARTHI AGENCIES"})

        self.assertEqual(result["name"], "AARTHI AGENCIES")
        self.assertEqual(result["trade_name"], "AARTHI AGENCIES")
        self.assertEqual(result["legal_name"], "AARTHI ENTERPRISES PRIVATE LIMITED")
        self.assertEqual(result["address"], "Door No 12, Anna Nagar Main Road, Madurai")
        self.assertEqual(result["pincode"], "625020")
        self.assertEqual(result["state"], "Tamil Nadu")
        self.assertEqual(result["country"], "India")
        self.assertEqual(result["registration_type"], "Regular")

    def test_data_source_reports_sandbox_for_every_genuinely_fetched_field(self):
        result = normalized_party_result(GSTIN, _sandbox_party(), {"party_name": "AARTHI AGENCIES"})
        self.assertEqual(result["data_source"], {
            "trade_name": "SANDBOX", "legal_name": "SANDBOX", "address": "SANDBOX", "pincode": "SANDBOX",
        })


class DataSourceNeverFakesSandboxTests(SimpleTestCase):
    """"Do not fake SANDBOX source when a value came from the uploaded
    file" -- data_source must accurately distinguish SANDBOX / SOURCE / ""
    (genuinely unavailable), never guessing."""

    def test_no_party_record_at_all_reports_blank_data_source_not_sandbox(self):
        result = normalized_party_result(GSTIN, None, {"party_name": "Source Only Traders"})
        self.assertEqual(result["data_source"]["trade_name"], "")
        self.assertEqual(result["data_source"]["address"], "")
        self.assertEqual(result["data_source"]["pincode"], "")

    def test_source_fallback_persisted_party_reports_source_for_trade_name(self):
        # lookup_source == "IMPORT_SOURCE" is the existing marker for a
        # party record whose trade_name was persisted from source data, not
        # a genuine Sandbox response (see party_lookup._fallback_party_identity).
        party = _sandbox_party(lookup_source="IMPORT_SOURCE", legal_name="", principal_place_of_business="", pincode="")
        result = normalized_party_result(GSTIN, party, {})
        self.assertEqual(result["data_source"]["trade_name"], "SOURCE")
        self.assertEqual(result["data_source"]["legal_name"], "")
        self.assertEqual(result["data_source"]["address"], "")
        self.assertEqual(result["data_source"]["pincode"], "")

    def test_address_and_pincode_from_source_dict_are_labeled_source_not_sandbox(self):
        party = _sandbox_party(lookup_source="IMPORT_SOURCE", principal_place_of_business="", address="", pincode="")
        source = {"address": "12 Market Street, Coimbatore", "pincode": "641001"}
        result = normalized_party_result(GSTIN, party, source)
        self.assertEqual(result["address"], "12 Market Street, Coimbatore")
        self.assertEqual(result["pincode"], "641001")
        self.assertEqual(result["data_source"]["address"], "SOURCE")
        self.assertEqual(result["data_source"]["pincode"], "SOURCE")

    def test_sandbox_lookup_failed_status_never_labeled_sandbox(self):
        party = _sandbox_party(lookup_source="sandbox", lookup_status="Sandbox Lookup Failed",
                                trade_name="", legal_name="", principal_place_of_business="", pincode="")
        result = normalized_party_result(GSTIN, party, {"party_name": "Fallback Source Name"})
        self.assertEqual(result["data_source"], {"trade_name": "", "legal_name": "", "address": "", "pincode": ""})


class ExistingValuesReturnedUnchangedTests(SimpleTestCase):
    """Regression guard: data_source is purely additive -- every existing
    field value/computation must be byte-for-byte unchanged."""

    def test_all_pre_existing_fields_still_present_and_correct(self):
        result = normalized_party_result(GSTIN, _sandbox_party(), {"party_name": "AARTHI AGENCIES"})
        for field in ("gstin", "trade_name", "legal_name", "name", "address", "state", "pincode",
                      "country", "registration_type", "name_source", "lookup_status", "party_details_complete"):
            self.assertIn(field, result)
        self.assertTrue(result["party_details_complete"])
        self.assertEqual(result["name_source"], "SANDBOX_TRADE_NAME")


class PartyLedgerSafeUpdateTests(SimpleTestCase):
    """SAFE UPDATE RULE: fill genuinely blank Pincode/Address on an existing
    ledger; never overwrite an existing non-blank value merely because
    Sandbox/source now reports something different. GSTIN/State/Country/
    Registration Type remain strictly enforced (unaffected by this rule --
    they affect tax classification correctness, not just informational
    completeness)."""

    def _master(self, **overrides):
        base = {"master_type": "Party", "name": "AARTHI AGENCIES", "gstin": GSTIN, "state": "Tamil Nadu",
                "registration_type": "Regular", "place_of_supply": "Tamil Nadu",
                "address": "Door No 12, Anna Nagar Main Road, Madurai", "pincode": "625020"}
        base.update(overrides)
        return base

    def _actual(self, **overrides):
        base = {"exists": True, "name": "AARTHI AGENCIES", "gstin": GSTIN, "state": "Tamil Nadu",
                "country": "India", "registration_type": "Regular", "place_of_supply": "Tamil Nadu",
                "address": "", "pincode": ""}
        base.update(overrides)
        return base

    def test_blank_existing_pincode_and_address_trigger_repair(self):
        result = _verify_master_properties(self._master(), self._actual())
        self.assertFalse(result["valid"])
        self.assertIn("Pincode", result["reason"])
        self.assertIn("Address", result["reason"])

    def test_acceptance_case_blank_address_pincode_alters_the_same_ledger(self):
        # Task spec's acceptance test: existing AARTHI AGENCIES ledger with
        # blank Address/Pincode must be ALTERed (repaired), not duplicated.
        result = _verify_master_properties(self._master(), self._actual(address="", pincode=""))
        self.assertFalse(result["valid"])

    def test_existing_non_blank_pincode_is_preserved_not_overwritten(self):
        result = _verify_master_properties(self._master(), self._actual(pincode="600001", address="Some existing address"))
        self.assertTrue(result["valid"], result.get("reason"))

    def test_existing_non_blank_address_is_preserved_not_overwritten(self):
        result = _verify_master_properties(self._master(), self._actual(address="A different pre-existing address", pincode="625020"))
        self.assertTrue(result["valid"], result.get("reason"))

    def test_gstin_mismatch_is_still_strictly_enforced(self):
        result = _verify_master_properties(self._master(), self._actual(gstin="33WRONG0000X1Z1", address="x", pincode="1"))
        self.assertFalse(result["valid"])
        self.assertIn("GSTIN", result["reason"])

    def test_state_mismatch_is_still_strictly_enforced(self):
        result = _verify_master_properties(self._master(), self._actual(state="Kerala", address="x", pincode="1"))
        self.assertFalse(result["valid"])
        self.assertIn("State", result["reason"])

    def test_no_master_pincode_or_address_available_never_flags_a_blank_actual(self):
        # "log, don't invent": if we never fetched a pincode/address
        # ourselves, an existing blank is not our business to flag.
        result = _verify_master_properties(self._master(address="", pincode=""), self._actual())
        self.assertTrue(result["valid"], result.get("reason"))
