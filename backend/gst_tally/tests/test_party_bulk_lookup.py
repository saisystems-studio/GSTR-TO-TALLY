from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.services.gst_lookup.base import GSTLookupProviderError, GSTLookupRateLimitError
from gst_tally.services.gst_lookup.providers.sandbox import ACCESS_CACHE_KEY, SandboxGSTProvider, _session_key
from gst_tally.services.gst_lookup.service import GSTLookupService
from gst_tally.tests.test_sandbox_provider import Opener, config
from gst_tally.views import BatchPartiesView, BatchPartyDetailView


GSTIN_1 = "33AAACB2894G1ZJ"
GSTIN_2 = "29AAACB2894G1ZP"


@override_settings(GST_LOOKUP_FALLBACK_PROVIDER="")
class BulkPartyLookupTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = get_user_model().objects.create_user(username="bulk-test")
        self.batch = GSTImportBatch.objects.create(file_name="batch.json", file_type="JSON")
        for invoice_no, gstin in (("1", GSTIN_1), ("2", GSTIN_1.lower()), ("3", GSTIN_2), ("4", "")):
            GSTInvoice.objects.create(import_batch=self.batch, invoice_no=invoice_no, customer_gstin=gstin)
        self.factory = APIRequestFactory()

    def request(self, method="get", data=None):
        request = getattr(self.factory, method)("/", data or {}, format="json")
        force_authenticate(request, user=self.user)
        return BatchPartiesView.as_view()(request, pk=self.batch.pk)

    @override_settings(GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="sandbox", GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
                       GST_LOOKUP_FALLBACK_PROVIDER="", SANDBOX_BASE_URL="https://api.sandbox.co.in",
                       SANDBOX_API_KEY="configured", SANDBOX_API_SECRET="configured")
    def test_sandbox_retry_calls_public_search_without_a_taxpayer_session(self):
        GSTParty.objects.create(gstin=GSTIN_1, trade_name="CACHED", principal_place_of_business="Chennai",
                                lookup_status="Existing", party_data_status="Complete", last_fetched_at=timezone.now())
        GSTParty.objects.create(gstin=GSTIN_2, lookup_source="sandbox", lookup_status="Incomplete",
                                party_data_status="Incomplete")
        from django.core.cache import cache
        cache.set(ACCESS_CACHE_KEY, "access-token-value", 300)
        provider = SandboxGSTProvider(config(), Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": GSTIN_2, "lgnm": "KARNATAKA LEGAL", "tradeNam": "KARNATAKA TRADE",
            "sts": "Active", "dty": "Regular", "pradr": {"addr": {
                "loc": "Bengaluru", "stcd": "Karnataka", "pncd": "560001"}}}}}]))
        with patch.object(GSTLookupService, "from_settings", return_value=GSTLookupService(provider)):
            response = self.request("post", {"retry_incomplete": True})
        self.assertEqual(response.data["retry_candidates"], 1)
        self.assertEqual(response.data["api_calls_made"], 1)
        self.assertEqual(response.data["sandbox_not_configured"], 0)
        self.assertEqual(response.data["sandbox_session_required"], 0)
        self.assertEqual(response.data["otp_required"], 0)
        row = next(row for row in response.data["parties"] if row["gstin"] == GSTIN_2)
        self.assertEqual(row["status"], "Fetched")
        self.assertEqual(row["trade_name"], "KARNATAKA TRADE")
        self.assertTrue(row["diagnostics"]["lookup_attempted"])
        self.assertEqual(row["diagnostics"]["api_calls"], 1)
        self.assertTrue(row["diagnostics"]["provider_configured"])
        self.assertFalse(row["diagnostics"]["session_active"])
        self.assertFalse(row["diagnostics"]["session_required"])
        self.assertFalse(row["diagnostics"]["otp_required"])

    @override_settings(GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="sandbox", GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
                       GST_LOOKUP_FALLBACK_PROVIDER="", SANDBOX_BASE_URL="https://api.sandbox.co.in",
                       SANDBOX_API_KEY="configured", SANDBOX_API_SECRET="configured")
    def test_retry_incomplete_counts_one_real_sandbox_http_call(self):
        self.batch.company_gstin = GSTIN_1
        self.batch.save(update_fields=["company_gstin"])
        GSTParty.objects.create(gstin=GSTIN_1, trade_name="COMPLETE", state_name="Tamil Nadu",
                                lookup_status="Fetched", party_data_status="Complete", last_fetched_at=timezone.now())
        GSTParty.objects.create(gstin=GSTIN_2, lookup_source="sandbox", lookup_status="Incomplete",
                                party_data_status="Incomplete")
        from django.core.cache import cache
        cache.set(ACCESS_CACHE_KEY, "access-token-value", 300)
        cache.set(_session_key(GSTIN_1), {"token": "taxpayer-token-value"}, 300)
        provider = SandboxGSTProvider(config(), Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": GSTIN_2, "lgnm": "KARNATAKA LEGAL", "tradeNam": "KARNATAKA TRADE",
            "sts": "Active", "dty": "Regular", "pradr": {"addr": {
                "loc": "Bengaluru", "stcd": "Karnataka", "pncd": "560001"}}}}}]))
        with patch.object(GSTLookupService, "from_settings", return_value=GSTLookupService(provider)):
            response = self.request("post", {"retry_incomplete": True})
        self.assertEqual(response.data["retry_candidates"], 1)
        self.assertEqual(response.data["api_calls_made"], 1)
        self.assertEqual(response.data["completed_after_retry"], 1)
        self.assertEqual(response.data["still_incomplete_after_retry"], 0)
        self.assertEqual(response.data["retry_incomplete_count"], 0)
        row = next(row for row in response.data["parties"] if row["gstin"] == GSTIN_2)
        self.assertTrue(row["diagnostics"]["provider_configured"])
        self.assertTrue(row["diagnostics"]["session_active"])
        self.assertFalse(row["diagnostics"]["session_required"])
        self.assertTrue(row["diagnostics"]["lookup_ready"])
        self.assertTrue(row["diagnostics"]["lookup_attempted"])
        self.assertEqual(row["diagnostics"]["http_status"], 200)
        self.assertTrue(row["diagnostics"]["normalized"])

    @override_settings(
        GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}", GST_PARTY_FRESH_DAYS=30,
    )
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_settings")
    def test_retry_calls_provider_for_only_unresolved_rows(self, service_factory):
        GSTParty.objects.create(
            gstin=GSTIN_1, trade_name="CACHED COMPLETE", principal_place_of_business="Chennai",
            lookup_status="Fetched", party_data_status="Complete", last_fetched_at=timezone.now() - timedelta(days=60),
        )
        GSTParty.objects.create(
            gstin=GSTIN_2, trade_name="PARTIAL", principal_place_of_business="",
            lookup_status="Incomplete", party_data_status="Incomplete", last_fetched_at=timezone.now(),
        )
        service_factory.return_value.lookup.return_value = {
            "gstin": GSTIN_2, "trade_name": "PARTIAL", "principal_address": "Bengaluru",
        }

        response = self.request("post", {"retry_incomplete": True})

        service_factory.return_value.lookup.assert_called_once_with(GSTIN_2)
        self.assertEqual(response.data["retry_candidates"], 1)
        self.assertEqual(response.data["api_calls_made"], 1)
        self.assertEqual(response.data["completed_after_retry"], 1)
        self.assertEqual(response.data["skipped_because_already_complete"], 1)
        self.assertEqual(response.data["retry_incomplete_count"], 0)

    @override_settings(
        GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}", GST_PARTY_FRESH_DAYS=30,
    )
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_settings")
    def test_retry_skips_rate_limited_row_before_retry_window(self, service_factory):
        GSTParty.objects.create(
            gstin=GSTIN_2, lookup_status="Rate Limited", lookup_error="Provider rate limit",
            party_data_status="Incomplete", retry_not_before=timezone.now() + timedelta(minutes=5),
        )

        response = self.request("post", {"retry_incomplete": True})

        service_factory.assert_not_called()
        self.assertEqual(response.data["retry_candidates"], 1)
        self.assertEqual(response.data["api_calls_made"], 0)
        self.assertEqual(response.data["still_rate_limited_after_retry"], 1)
        rate_row = next(row for row in response.data["parties"] if row["gstin"] == GSTIN_2)
        self.assertIn("Retry allowed after", rate_row["reason"])

    @override_settings(GST_LOOKUP_ENABLED=False, GST_LOOKUP_PROVIDER="")
    def test_get_returns_unique_valid_gstins_and_only_required_fields(self):
        response = self.request()
        self.assertEqual([row["gstin"] for row in response.data["parties"]], [GSTIN_2, GSTIN_1, "-"])
        self.assertEqual(
            set(response.data["parties"][0]),
            {"gstin", "trade_name", "legal_name", "principal_place_of_business",
             "name", "address", "state", "pincode", "country", "registration_type",
             "name_source", "lookup_status", "status", "reason", "warning_reason",
             "tally_ready", "party_name", "warning", "diagnostics"},
        )
        self.assertEqual(response.data["configuration_error"], "GSTIN lookup source is not configured.")

    @override_settings(GST_LOOKUP_ENABLED=False, GST_LOOKUP_PROVIDER="", GST_LOOKUP_BASE_URL="")
    def test_post_uses_import_source_before_unconfigured_provider(self):
        self.batch.source_parties = {
            GSTIN_1: {"trade_name": "SOURCE TRADER", "principal_place_of_business": "12 Main Road, Chennai - 600001"}
        }
        self.batch.save(update_fields=["source_parties"])
        response = self.request("post")
        rows = {row["gstin"]: row for row in response.data["parties"]}
        self.assertFalse(response.data["lookup_configured"])
        self.assertEqual(response.data["fetched"], 1)
        self.assertEqual(response.data["failed"], 1)
        self.assertEqual(response.data["pending"], 0)
        self.assertEqual(rows[GSTIN_1]["status"], "Fetched")
        self.assertEqual(rows[GSTIN_2]["status"], "Failed")
        party = GSTParty.objects.get(gstin=GSTIN_1)
        self.assertEqual(party.lookup_source, "IMPORT_SOURCE")
        self.assertEqual(party.trade_name, "SOURCE TRADER")

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="sandbox",
        GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
        GST_LOOKUP_FALLBACK_PROVIDER="",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.party_lookup.GSTLookupService.from_settings")
    def test_sandbox_failure_uses_source_party_name_as_non_blocking_fallback(self, service_factory):
        self.batch.source_parties = {
            GSTIN_1: {"party_name": "Source Supplier One"},
            GSTIN_2: {"trade_name": "Source Supplier Two"},
        }
        self.batch.save(update_fields=["source_parties"])
        service_factory.return_value.lookup.side_effect = GSTLookupProviderError("sandbox down")

        response = self.request("post")

        self.assertEqual(service_factory.return_value.lookup.call_count, 2)
        rows = {row["gstin"]: row for row in response.data["parties"] if row["gstin"] != "-"}
        self.assertEqual(rows[GSTIN_1]["lookup_status"], "Sandbox Lookup Failed")
        self.assertEqual(rows[GSTIN_1]["name"], "Source Supplier One")
        self.assertEqual(rows[GSTIN_1]["name_source"], "SOURCE")
        self.assertEqual(rows[GSTIN_1]["warning"], "Sandbox party enrichment unavailable; source/GSTIN fallback used.")
        self.assertTrue(rows[GSTIN_1]["tally_ready"])
        self.assertEqual(rows[GSTIN_2]["name"], "Source Supplier Two")
        self.assertTrue(response.data["tally_ready"])
        party = GSTParty.objects.get(gstin=GSTIN_1)
        self.assertEqual(party.trade_name, "Source Supplier One")
        self.assertEqual(party.lookup_status, "Sandbox Lookup Failed")
        self.assertEqual(party.party_data_status, "Complete")

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="sandbox",
        GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
        GST_LOOKUP_FALLBACK_PROVIDER="",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.party_lookup.GSTLookupService.from_settings")
    def test_sandbox_success_uses_trade_name_before_legal_name_and_source_name(self, service_factory):
        self.batch.source_parties = {GSTIN_1: {"party_name": "Source Name"}}
        self.batch.save(update_fields=["source_parties"])
        service_factory.return_value.lookup.side_effect = lambda gstin, company_gstin="": {
            "gstin": gstin,
            "trade_name": "Sandbox Trade" if gstin == GSTIN_1 else "",
            "legal_name": "Sandbox Legal",
            "principal_address": "Sandbox address",
            "state": "Tamil Nadu",
            "taxpayer_type": "Regular",
            "pincode": "600001",
        }

        response = self.request("post")

        rows = {row["gstin"]: row for row in response.data["parties"] if row["gstin"] != "-"}
        self.assertEqual(rows[GSTIN_1]["name"], "Sandbox Trade")
        self.assertEqual(rows[GSTIN_1]["name_source"], "SANDBOX_TRADE_NAME")
        self.assertEqual(rows[GSTIN_2]["name"], "Sandbox Legal")
        self.assertEqual(rows[GSTIN_2]["name_source"], "SANDBOX_LEGAL_NAME")
        self.assertEqual(rows[GSTIN_1]["address"], "Sandbox address")
        self.assertEqual(rows[GSTIN_1]["registration_type"], "Regular")


    @override_settings(GST_PARTY_FRESH_DAYS=30)
    @patch("gst_tally.services.party_lookup.GSTLookupService.from_settings")
    def test_fresh_complete_party_is_reused_without_provider_call(self, service_factory):
        GSTParty.objects.create(
            gstin=GSTIN_1,
            trade_name="Existing Trader",
            principal_place_of_business="Chennai",
            last_fetched_at=timezone.now() - timedelta(days=1),
        )
        from gst_tally.services.party_lookup import process_gstin

        status, _ = process_gstin(GSTIN_1)
        self.assertEqual(status, "Existing")
        service_factory.assert_not_called()

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
        GST_PARTY_FRESH_DAYS=30,
    )
    @patch("gst_tally.services.party_lookup.GSTLookupService.from_settings")
    def test_recent_but_incomplete_cache_is_refetched(self, service_factory):
        GSTParty.objects.create(
            gstin=GSTIN_1,
            trade_name="-",
            legal_name="None",
            principal_place_of_business="",
            last_fetched_at=timezone.now() - timedelta(days=1),
        )
        service_factory.return_value.lookup.return_value = {
            "gstin": GSTIN_1,
            "trade_name": "REFRESHED TRADER",
            "legal_name": "REFRESHED LEGAL NAME",
            "principal_address": "Refreshed address",
            "status": "Active",
        }
        from gst_tally.services.party_lookup import process_gstin

        status, party = process_gstin(GSTIN_1)
        self.assertEqual(status, "Fetched")
        self.assertEqual(party.trade_name, "REFRESHED TRADER")
        self.assertEqual(party.principal_place_of_business, "Refreshed address")
        service_factory.return_value.lookup.assert_called_once_with(GSTIN_1)

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.party_lookup.GSTLookupService.from_settings")
    def test_post_looks_up_each_unique_gstin_once_and_returns_summary(self, service_factory):
        service_factory.return_value.lookup.side_effect = lambda gstin: {
            "gstin": gstin,
            "trade_name": f"Trade {gstin[:2]}",
            "principal_address": "Principal address",
        }
        response = self.request("post")
        self.assertEqual(service_factory.return_value.lookup.call_count, 2)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["fetched"], 2)
        self.assertEqual(response.data["existing"], 0)
        self.assertEqual(response.data["failed"], 0)
        self.assertEqual(GSTParty.objects.count(), 2)
        self.assertEqual(response.data["complete_from_cache"], 0)
        self.assertEqual(response.data["complete_from_jamku"], 2)
        self.assertEqual(response.data["sent_to_fallback"], 0)
        self.assertEqual(response.data["completed_by_fallback"], 0)
        self.assertEqual(response.data["still_incomplete"], 0)
        self.assertTrue(response.data["tally_ready"])

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.party_lookup.GSTLookupService.from_settings")
    def test_partial_success_invalid_and_individual_failure_are_all_accounted_for(self, service_factory):
        invalid = "INVALID-GSTIN"
        GSTInvoice.objects.create(import_batch=self.batch, invoice_no="5", customer_gstin=invalid)
        partial = {"gstin": GSTIN_2, "legal_name": "LEGAL ONLY", "trade_name": None,
                   "principal_address": None, "status": "Active"}
        service_factory.return_value.lookup.side_effect = [
            partial,
            Exception("temporary failure"),
            partial,
        ]

        response = self.request("post")
        rows = {row["gstin"]: row for row in response.data["parties"] if row["gstin"] != "-"}
        self.assertEqual(set(rows), {GSTIN_1, GSTIN_2, invalid})
        self.assertEqual(rows[GSTIN_2]["status"], "Incomplete")
        self.assertEqual(rows[GSTIN_2]["trade_name"], "LEGAL ONLY")
        self.assertEqual(rows[GSTIN_2]["principal_place_of_business"], "")
        self.assertEqual(rows[GSTIN_2]["reason"], "Provider did not return address")
        self.assertEqual(rows[GSTIN_1]["status"], "Failed")
        self.assertEqual(rows[invalid]["status"], "Invalid")
        self.assertEqual(response.data["total"], 3)
        self.assertEqual(response.data["processed"], 3)
        self.assertEqual(response.data["missing_unprocessed"], 0)
        self.assertTrue(response.data["all_accounted_for"])
        self.assertEqual(sum(response.data[key] for key in (
            "fetched", "existing", "incomplete", "not_found", "invalid", "rate_limited", "failed", "pending"
        )), response.data["total"])

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
        GST_LOOKUP_MAX_RETRY_DELAY=0,
    )
    @patch("gst_tally.services.party_lookup.GSTLookupService.from_settings")
    def test_rate_limited_row_is_counted_and_does_not_stop_bulk(self, service_factory):
        partial = {"gstin": GSTIN_2, "trade_name": "Fetched Two", "principal_address": None}
        service_factory.return_value.lookup.side_effect = [
            partial,
            GSTLookupRateLimitError(),
            partial,
        ]
        response = self.request("post")
        rows = {row["gstin"]: row for row in response.data["parties"]}
        self.assertEqual(rows[GSTIN_2]["status"], "Incomplete")
        self.assertEqual(rows[GSTIN_1]["status"], "Rate Limited")
        self.assertEqual(rows[GSTIN_1]["reason"], "Provider rate limit")
        self.assertEqual(response.data["rate_limited"], 1)
        self.assertEqual(response.data["incomplete"], 1)

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_settings")
    def test_cancelled_gst_status_is_saved_separately_from_fetched_status(self, service_factory):
        service_factory.return_value.lookup.return_value = {
            "gstin": GSTIN_1,
            "trade_name": "EM VEERU & CO",
            "legal_name": "EM VEERU & CO",
            "principal_address": "No.27, Tamil Nadu, 626104",
            "status": "Cancelled suo-moto",
            "registration_date": "11/06/2024",
            "cancellation_date": "29/05/2026",
            "constitution_of_business": "Partnership",
        }
        from gst_tally.services.party_lookup import process_gstin

        lookup_status, party = process_gstin(GSTIN_1)
        self.assertEqual(lookup_status, "Fetched")
        self.assertEqual(party.lookup_status, "Fetched")
        self.assertEqual(party.registration_status, "Cancelled suo-moto")
        self.assertEqual(party.cancellation_date, "29/05/2026")

    def test_manual_completion_requires_and_saves_tally_ready_fields(self):
        GSTParty.objects.create(gstin=GSTIN_1, trade_name="Fetched Name", party_data_status="Incomplete")
        request = self.factory.put("/", {
            "trade_name": "Fetched Name",
            "legal_name": "Fetched Legal Name",
            "principal_place_of_business": "Manual verified address",
            "state": "Tamil Nadu",
            "pincode": "626106",
        }, format="json")
        force_authenticate(request, user=self.user)
        response = BatchPartyDetailView.as_view()(request, pk=self.batch.pk, gstin=GSTIN_1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["party"]["status"], "Completed Manually")
        party = GSTParty.objects.get(gstin=GSTIN_1)
        self.assertEqual(party.party_data_status, "Complete")
        self.assertEqual(party.lookup_status, "Completed Manually")
        self.assertEqual(party.principal_place_of_business, "Manual verified address")

    @override_settings(
        GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_PRIMARY_PROVIDER="generic_rest", GST_LOOKUP_FALLBACK_PROVIDER="jamku",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_provider_name")
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_settings")
    def test_incomplete_primary_is_merged_with_fallback(self, primary_factory, fallback_factory):
        primary_factory.return_value.lookup.return_value = {
            "gstin": GSTIN_1, "trade_name": "PRIMARY NAME", "legal_name": "PRIMARY LEGAL",
            "principal_address": None, "status": "Active",
        }
        fallback_factory.return_value.lookup.return_value = {
            "gstin": GSTIN_1, "trade_name": None, "legal_name": None,
            "principal_address": "FALLBACK ADDRESS", "status": None,
        }
        from gst_tally.services.party_lookup import process_gstin

        status, party = process_gstin(GSTIN_1)
        self.assertEqual(status, "Fetched via Fallback")
        self.assertEqual(party.trade_name, "PRIMARY NAME")
        self.assertEqual(party.principal_place_of_business, "FALLBACK ADDRESS")
        self.assertEqual(party.registration_status, "Active")
        self.assertEqual(party.party_data_status, "Complete")
        fallback_factory.assert_called_once_with("jamku")

    @override_settings(
        GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_PRIMARY_PROVIDER="generic_rest", GST_LOOKUP_FALLBACK_PROVIDER="jamku",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_provider_name")
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_settings")
    def test_complete_primary_does_not_call_fallback(self, primary_factory, fallback_factory):
        primary_factory.return_value.lookup.return_value = {
            "gstin": GSTIN_1, "trade_name": "PRIMARY NAME",
            "principal_address": "PRIMARY ADDRESS", "status": "Active",
        }
        from gst_tally.services.party_lookup import process_gstin

        status, _party = process_gstin(GSTIN_1)
        self.assertEqual(status, "Fetched")
        fallback_factory.assert_not_called()

    @override_settings(
        GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_PRIMARY_PROVIDER="generic_rest", GST_LOOKUP_FALLBACK_PROVIDER="jamku",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}",
    )
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.status")
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_provider_name")
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_settings")
    def test_primary_429_uses_configured_fallback(self, primary_factory, fallback_factory, lookup_status):
        lookup_status.return_value = {"configured": True, "fallback_configured": True}
        primary_factory.return_value.lookup.side_effect = GSTLookupRateLimitError("generic_rest", "5")
        fallback_factory.return_value.lookup.return_value = {
            "gstin": GSTIN_1, "trade_name": "FALLBACK NAME",
            "principal_address": "FALLBACK ADDRESS", "status": "Active",
        }
        from gst_tally.services.party_lookup import process_gstin

        status, party = process_gstin(GSTIN_1)
        self.assertEqual(status, "Fetched via Fallback")
        self.assertEqual(party.principal_place_of_business, "FALLBACK ADDRESS")
        self.assertEqual(party._lookup_diagnostics["generic_rest_429_count"], 1)
        self.assertTrue(party._lookup_diagnostics["fallback_attempted_after_429"])
