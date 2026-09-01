from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from gst_tally.models import GSTParty
from gst_tally.services.gst_lookup.base import GSTLookupTimeoutError
from gst_tally.views import GSTLookupBulkView, GSTLookupStatusView, GSTLookupView


GSTIN = "33AAACB2894G1ZJ"


class GSTLookupAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="lookup-api")
        self.factory = APIRequestFactory()

    def get(self, gstin=GSTIN):
        request = self.factory.get(f"/api/gst/lookup/{gstin}/")
        force_authenticate(request, user=self.user)
        return GSTLookupView.as_view()(request, gstin=gstin)

    def get_status(self):
        request = self.factory.get("/api/gst/lookup/status/")
        force_authenticate(request, user=self.user)
        return GSTLookupStatusView.as_view()(request)

    @override_settings(GST_LOOKUP_ENABLED=False, GST_LOOKUP_PROVIDER="vayana", GST_LOOKUP_FALLBACK_PROVIDER="")
    def test_status_endpoint_has_one_safe_authoritative_contract(self):
        response = self.get_status()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"enabled": False, "provider": "vayana", "registered": True,
                                         "configured": False, "missing": [], "fallback_provider": None,
                                         "fallback_registered": False, "fallback_configured": False})
        self.assertNotIn("secret", str(response.data).lower())

    def test_invalid_gstin_is_rejected_without_lookup(self):
        with patch("gst_tally.views.GSTLookupService.lookup_cached") as lookup:
            response = self.get("INVALID")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_GSTIN")
        lookup.assert_not_called()

    @override_settings(GST_LOOKUP_ENABLED=False, GST_LOOKUP_PROVIDER="", GST_LOOKUP_BASE_URL="")
    def test_provider_not_configured_has_clean_response(self):
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {"success": False, "code": "GST_LOOKUP_NOT_CONFIGURED",
                                         "message": "GST lookup provider is not configured."})

    def test_complete_local_party_returns_existing(self):
        GSTParty.objects.create(gstin=GSTIN, trade_name="ABC", principal_place_of_business="Chennai",
                                last_fetched_at=timezone.now())
        response = self.get()
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["source"], "existing")
        self.assertEqual(response.data["status"], "Existing")

    @patch("gst_tally.views.GSTLookupService.lookup_cached")
    @override_settings(GST_LOOKUP_PROVIDER="sandbox", GST_LOOKUP_PRIMARY_PROVIDER="sandbox")
    def test_partial_sandbox_result_is_fetched_with_address_warning(self, lookup):
        lookup.return_value = ("sandbox", {
            "gstin": GSTIN,
            "trade_name": "M. SIVAKUMAR",
            "principal_address": None,
            "status": "Active",
            "lookup_status": "Fetched",
            "party_data_status": "Incomplete",
        })
        response = self.get()
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["status"], "Fetched")
        self.assertEqual(response.data["lookup_status"], "Fetched")
        self.assertEqual(response.data["party_data_status"], "Incomplete")
        self.assertEqual(response.data["gst_status"], "Active")
        self.assertEqual(response.data["warning_reason"], "Address unavailable")

    @override_settings(
        GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="generic_rest",
        GST_LOOKUP_PRIMARY_PROVIDER="generic_rest", GST_LOOKUP_FALLBACK_PROVIDER="",
        GST_LOOKUP_BASE_URL="https://example.test/{gstin}", GST_PARTY_FRESH_DAYS=30,
    )
    @patch("gst_tally.services.gst_lookup.service.GSTLookupService.from_settings")
    def test_bulk_17_uses_cache_for_13_and_calls_provider_for_only_4(self, service_factory):
        gstins = [f"{number:02d}AAACB2894G1ZJ" for number in range(1, 18)]
        for gstin in gstins[:13]:
            GSTParty.objects.create(
                gstin=gstin, trade_name=f"Cached {gstin[:2]}",
                principal_place_of_business="Cached address", party_data_status="Complete",
                lookup_status="Fetched", last_fetched_at=timezone.now(),
            )
        service_factory.return_value.lookup.side_effect = lambda gstin: {
            "gstin": gstin, "trade_name": f"Fetched {gstin[:2]}",
            "principal_address": "Provider address", "status": "Active",
        }
        request = self.factory.post("/api/gst/lookup/bulk/", {"gstins": gstins}, format="json")
        force_authenticate(request, user=self.user)

        response = GSTLookupBulkView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 17)
        self.assertEqual(response.data["existing"], 13)
        self.assertEqual(response.data["fetched"], 4)
        self.assertEqual(response.data["pending"], 0)
        self.assertEqual(response.data["total_accounted_for"], 17)
        self.assertTrue(response.data["all_tally_ready"])
        self.assertEqual(service_factory.return_value.lookup.call_count, 4)

    @patch("gst_tally.views.GSTLookupService.lookup_cached", side_effect=GSTLookupTimeoutError())
    def test_timeout_is_normalized_without_internal_details(self, _lookup):
        response = self.get()
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.data["code"], "PROVIDER_TIMEOUT")
        self.assertNotIn("secret", str(response.data).lower())
