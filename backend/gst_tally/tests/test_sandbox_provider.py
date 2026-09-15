from superadmin.services.sandbox_configuration import encrypt
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from gst_tally.services.gst_lookup.base import GSTLookupConfigurationError, GSTLookupProviderError, GSTLookupTimeoutError
from gst_tally.services.gst_lookup.providers import provider_class
from gst_tally.services.gst_lookup.providers.sandbox import (ACCESS_CACHE_KEY, SESSION_CACHE_KEY,
    SandboxAuthenticationFailure, SandboxGSTProvider, SandboxOTPRequestFailure, _session_key)
from gst_tally.services.gst_lookup.service import GSTLookupService
from gst_tally.services.party_lookup import process_gstin, result_row
from gst_tally.models import GSTImportBatch, GSTInvoice, GSTParty
from gst_tally.views import (BatchPartiesView, SandboxAuthenticateView, SandboxRequestOTPView,
                             SandboxStatusView, SandboxVerifyOTPView)

GSTIN = "33AAACB2894G1ZJ"

class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()

class Opener:
    def __init__(self, payloads): self.payloads, self.requests = list(payloads), []
    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        return Response(self.payloads.pop(0))

def config():
    return {"base_url": "https://api.sandbox.co.in", "api_key": "api-key-value", "api_secret": "api-secret-value",
            "api_version": "1.0.0",
            "details_endpoint": "", "timeout": 20, "access_ttl": 300, "session_ttl": 21600}

class SandboxProviderTests(SimpleTestCase):
    def setUp(self): cache.clear()

    def test_authentication_headers_and_access_token_cache(self):
        opener = Opener([{"access_token": "access-token-value", "expires_in": 120}])
        provider = SandboxGSTProvider(config(), opener)
        self.assertEqual(provider.authenticate(), "access-token-value")
        self.assertEqual(provider.authenticate(), "access-token-value")
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0][0]
        self.assertEqual(request.full_url, "https://api.sandbox.co.in/authenticate")
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-api-key"], "api-key-value")
        self.assertEqual(headers["x-api-secret"], "api-secret-value")
        self.assertEqual(headers["x-api-version"], "1.0.0")

    def test_otp_request_uses_company_identity_not_party_gstin(self):
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"success": True}]); provider = SandboxGSTProvider(config(), opener)
        result = provider.request_otp("portal-user", GSTIN); request = opener.requests[0][0]
        self.assertEqual(request.full_url, "https://api.sandbox.co.in/gst/compliance/tax-payer/otp")
        self.assertEqual(json.loads(request.data), {"username": "portal-user", "gstin": GSTIN})
        self.assertEqual(dict((k.lower(), v) for k, v in request.header_items())["authorization"], "access-token-value")
        self.assertTrue(result["otp_sent"]); self.assertEqual(result["code"], "OTP_SENT")
        self.assertTrue(result["sandbox_authenticated"]); self.assertNotIn("access-token-value", json.dumps(result))

    def test_authentication_401_has_safe_specific_code(self):
        def reject(request, timeout=None): raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        provider = SandboxGSTProvider(config(), reject)
        with self.assertRaises(SandboxAuthenticationFailure) as caught: provider.authenticate(force=True)
        self.assertEqual(caught.exception.code, "SANDBOX_AUTH_401")
        self.assertEqual(caught.exception.diagnostics["authenticate_http_status"], 401)
        self.assertFalse(caught.exception.diagnostics["access_token_received"])
        self.assertNotIn("api-secret-value", json.dumps(caught.exception.diagnostics))

    def test_http_500_error_body_is_parsed_into_provider_diagnostics(self):
        """A 500 must never collapse to a bare, undiagnosable status code --
        Sandbox's own {code, message, transaction_id} error envelope has to
        be captured and surfaced so a real outage/account issue is visible
        instead of a generic 'request failed' string."""
        class BodiedHTTPError(HTTPError):
            def __init__(self, body):
                self._body = body.encode()
                super().__init__("https://api.sandbox.co.in/gst/compliance/public/gstin/search",
                                  500, "Internal Server Error", {}, None)
            def read(self): return self._body
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        def reject(request, timeout=None):
            raise BodiedHTTPError(json.dumps({"code": 500, "message": "Internal Server Error",
                                              "transaction_id": "txn-abc-123"}))
        provider = SandboxGSTProvider(config(), reject)
        with self.assertRaises(GSTLookupProviderError) as caught:
            provider.lookup("33AALFE2101R1ZF")
        self.assertIn("Internal Server Error", str(caught.exception))
        self.assertEqual(provider.last_http_status, 500)
        self.assertEqual(provider.last_provider_code, 500)
        self.assertEqual(provider.last_provider_message, "Internal Server Error")
        self.assertEqual(provider.last_provider_transaction_id, "txn-abc-123")

    def test_response_shape_is_tagged_for_diagnostics(self):
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"code": 200, "data": {"gstin": "33AALFE2101R1ZF", "lgnm": "FLAT LEGAL"}}])
        provider = SandboxGSTProvider(config(), opener)
        provider.lookup("33AALFE2101R1ZF")
        self.assertEqual(provider.last_response_shape, "flat")

    def test_otp_rejection_is_not_mislabeled_as_application_auth_failure(self):
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        def responder(request, timeout=None):
            if request.full_url.endswith("/authenticate"): return Response({"access_token": "renewed-token"})
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        provider = SandboxGSTProvider(config(), responder)
        with self.assertRaises(SandboxOTPRequestFailure) as caught: provider.request_otp("wrong-portal-user", GSTIN)
        self.assertEqual(caught.exception.code, "OTP_REQUEST_REJECTED")
        self.assertIn("GST portal username", caught.exception.safe_message)

    def test_verify_otp_stores_session_server_side_and_returns_no_token(self):
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"data": {"taxpayer_session_token": "taxpayer-token-value", "expires_in": 3600}}])
        provider = SandboxGSTProvider(config(), opener); result = provider.verify_otp("123456", "portal-user", GSTIN)
        self.assertEqual(opener.requests[0][0].full_url, "https://api.sandbox.co.in/gst/compliance/tax-payer/otp/verify?otp=123456")
        self.assertTrue(cache.get(SandboxGSTProvider(config()).session_key(GSTIN))["token"])
        self.assertEqual(result, {"verified": True, "session_active": True, "code": "TAXPAYER_SESSION_ACTIVE"})
        self.assertNotIn("taxpayer-token-value", json.dumps(result))

    def test_production_cache_is_shared_across_backend_processes(self):
        from config import settings as production_settings
        backend = production_settings.CACHES["default"]["BACKEND"]
        self.assertNotEqual(backend, "django.core.cache.backends.locmem.LocMemCache")

    def test_party_lookup_uses_application_auth_only_without_taxpayer_session(self):
        party_gstin = "33AALFE2101R1ZF"
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": party_gstin, "lgnm": "EXAMPLE LEGAL NAME", "tradeNam": "EXAMPLE TRADE NAME",
            "sts": "Active", "dty": "Regular", "rgdt": "01/04/2019", "ctb": "Partnership",
            "ctj": "RANGE-I", "stj": "TAMIL NADU STATE", "pradr": {"addr": {
                "bno": "10", "bnm": "Commerce House", "st": "Main Road", "loc": "Chennai",
                "dst": "Chennai", "stcd": "Tamil Nadu", "pncd": "600001"}}}}}])
        provider = SandboxGSTProvider(config(), opener)

        # No taxpayer session is cached for GSTIN (company_gstin) anywhere in this test.
        result = provider.lookup(party_gstin, company_gstin=GSTIN)

        request = opener.requests[0][0]
        self.assertEqual(request.full_url, "https://api.sandbox.co.in/gst/compliance/public/gstin/search")
        self.assertEqual(json.loads(request.data), {"gstin": party_gstin})
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["authorization"], "access-token-value")
        self.assertEqual(headers["x-api-key"], "api-key-value")
        self.assertEqual(result.gstin, party_gstin)
        self.assertEqual(result.legal_name, "EXAMPLE LEGAL NAME")
        self.assertEqual(result.trade_name, "EXAMPLE TRADE NAME")
        self.assertEqual(result.pincode, "600001")
        self.assertEqual(result.state_code, "33")
        self.assertIn("Commerce House", result.principal_address)

    def test_real_party_principal_address_is_split_from_state_and_pincode(self):
        party_gstin = "33AALFE2101R1ZF"
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": party_gstin, "lgnm": "EM VEERU & CO", "tradeNam": "EM VEERU & CO",
            "sts": "Active", "dty": "Regular", "pradr": {"addr": {
                "bno": "No.27", "st": "Manthopu Post", "loc": "Chinnavayampatti",
                "locality": "Manthoppu", "dst": "Virudhunagar", "stcd": "Tamil Nadu",
                "pncd": "626104"}}}}}])
        provider = SandboxGSTProvider(config(), opener)

        result = provider.lookup(party_gstin, company_gstin=GSTIN)

        self.assertEqual(result.trade_name, "EM VEERU & CO")
        self.assertEqual(result.legal_name, "EM VEERU & CO")
        self.assertEqual(result.principal_address, "No.27, Manthopu Post, Chinnavayampatti, Manthoppu, Virudhunagar")
        self.assertEqual(result.state, "Tamil Nadu")
        self.assertEqual(result.pincode, "626104")
        self.assertEqual(result.taxpayer_type, "Regular")

    def test_erp_style_taxpayer_details_shape_is_normalized_from_actual_nested_fields(self):
        party_gstin = "29AAACQ3770E000"
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"code": 200, "data": {"Status": 1, "Data": {
            "Gstin": party_gstin, "LegalName": "Acme Industries Private Limited",
            "TradeName": "Acme Industries", "Status": "ACT", "TxpType": "REG",
            "AddrBno": "42", "AddrBnm": "Commerce Tower", "AddrFlno": "3rd Floor",
            "AddrSt": "Market Road", "AddrLoc": "Bengaluru", "AddrPncd": 560009,
        }}}])
        provider = SandboxGSTProvider(config(), opener)

        result = provider.lookup(party_gstin)

        self.assertEqual(provider.last_response_shape, "erp_nested")
        self.assertEqual(result.trade_name, "Acme Industries")
        self.assertEqual(result.legal_name, "Acme Industries Private Limited")
        self.assertEqual(result.principal_address, "42, Commerce Tower, 3rd Floor, Market Road, Bengaluru")
        self.assertEqual(result.state, "Karnataka")
        self.assertEqual(result.state_code, "29")
        self.assertEqual(result.pincode, "560009")
        self.assertEqual(result.taxpayer_type, "Regular")

    def test_party_lookup_does_not_require_company_gstin_at_all(self):
        party_gstin = "33AALFE2101R1ZF"
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": party_gstin, "lgnm": "EXAMPLE LEGAL NAME", "tradeNam": "EXAMPLE TRADE NAME",
            "sts": "Active", "pradr": {"addr": {"loc": "Chennai", "stcd": "Tamil Nadu", "pncd": "600001"}}}}}])
        provider = SandboxGSTProvider(config(), opener)

        result = provider.lookup(party_gstin)  # no company_gstin supplied

        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(result.trade_name, "EXAMPLE TRADE NAME")

    def test_party_lookup_status_has_no_secrets_and_does_not_require_taxpayer_session(self):
        provider = SandboxGSTProvider(config(), Opener([]))
        status = provider.safe_status(GSTIN)
        self.assertTrue(status["party_lookup_available"])
        self.assertFalse(status["party_lookup_requires_taxpayer_session"])
        self.assertFalse(status["session_required"])
        self.assertFalse(status["otp_required"])
        self.assertTrue(status["lookup_ready"])
        serialized = json.dumps(status)
        for secret in ("api-secret-value", "api-key-value", "taxpayer-token-value"): self.assertNotIn(secret, serialized)
        self.assertIsNot(provider_class("sandbox"), provider_class("jamku"))
        self.assertIsNot(provider_class("sandbox"), provider_class("gstinapi"))

    def test_public_search_is_sent_immediately_without_a_taxpayer_session(self):
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        opener = Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": "33AALFE2101R1ZF", "lgnm": "EXAMPLE LEGAL", "tradeNam": "EXAMPLE TRADE", "sts": "Active"}}}])
        provider = SandboxGSTProvider(config(), opener)
        result = provider.lookup("33AALFE2101R1ZF", company_gstin=GSTIN)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0][0].full_url, "https://api.sandbox.co.in/gst/compliance/public/gstin/search")
        self.assertTrue(provider.last_lookup_attempted)
        self.assertEqual(provider.lookup_request_count, 1)
        self.assertEqual(result.gstin, "33AALFE2101R1ZF")

    def test_invalid_payload_records_real_http_200(self):
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        provider = SandboxGSTProvider(config(), Opener([{"code": 200, "data": {}}]))
        with self.assertRaises(Exception):
            provider.lookup("33AALFE2101R1ZF", company_gstin=GSTIN)
        self.assertTrue(provider.last_lookup_attempted)
        self.assertEqual(provider.last_http_status, 200)

@override_settings(GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="sandbox", GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
                   GST_LOOKUP_FALLBACK_PROVIDER="", GST_PARTY_FRESH_DAYS=30)
class SandboxCacheTests(TestCase):
    def setUp(self): cache.clear()

    @override_settings(SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_public_search_200_is_normalized_saved_and_tally_ready_without_taxpayer_session(self):
        party_gstin = "33AALFE2101R1ZF"
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        provider = SandboxGSTProvider(config(), Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": party_gstin, "lgnm": "EXAMPLE LEGAL", "tradeNam": "EXAMPLE TRADE",
            "sts": "Active", "dty": "Regular", "pradr": {"addr": {
                "bno": "10", "loc": "Chennai", "stcd": "Tamil Nadu", "pncd": "600001"}}}}}]))
        with patch.object(GSTLookupService, "from_settings", return_value=GSTLookupService(provider)):
            status, party = process_gstin(party_gstin, batch=SimpleNamespace(company_gstin=GSTIN, source_parties={}), force=True)
        row = result_row(party_gstin, status, party)
        self.assertEqual((status, party.trade_name, party.state_name, party.pincode),
                         ("Fetched", "EXAMPLE TRADE", "Tamil Nadu", "600001"))
        self.assertEqual(party.address, "10, Chennai")
        self.assertEqual(party.principal_place_of_business, "10, Chennai")
        self.assertEqual(party.state_code, "33")
        self.assertTrue(row["tally_ready"])
        self.assertTrue(row["party_details_complete"])
        self.assertEqual(row["diagnostics"]["http_status"], 200)
        self.assertTrue(row["diagnostics"]["lookup_attempted"])
        self.assertFalse(row["diagnostics"]["taxpayer_session_active"])
        self.assertFalse(row["diagnostics"]["session_required"])
        self.assertTrue(row["diagnostics"]["normalized"])
        self.assertEqual(row["diagnostics"]["api_calls"], 1)
        self.assertEqual(row["sandbox_lookup"]["attempted"], True)
        self.assertEqual(row["sandbox_lookup"]["success"], True)
        self.assertEqual(row["sandbox_lookup"]["http_status"], 200)
        self.assertEqual(row["sandbox_lookup"]["error_code"], "")
        self.assertEqual(row["sandbox_lookup"]["endpoint"], "/gst/compliance/public/gstin/search")

    @override_settings(SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_active_session_provider_rejection_is_session_failed_not_fake_ready(self):
        party_gstin = "33AALFE2101R1ZF"
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        cache.set(SandboxGSTProvider(config()).session_key(GSTIN), {"token": encrypt("taxpayer-token-value")}, 300)
        def reject(request, timeout=None): raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        provider = SandboxGSTProvider(config(), reject)
        with patch.object(GSTLookupService, "from_settings", return_value=GSTLookupService(provider)):
            status, party = process_gstin(party_gstin, batch=SimpleNamespace(company_gstin=GSTIN, source_parties={}), force=True)
        row = result_row(party_gstin, status, party)
        self.assertEqual(status, "Sandbox Session Failed")
        # A failed Sandbox enrichment is not a usable party profile; keep it
        # attention-required instead of silently importing with GSTIN as name.
        self.assertFalse(row["tally_ready"])
        self.assertTrue(row["attention_required"])
        self.assertEqual(row["party_name"], "")
        self.assertTrue(row["diagnostics"]["lookup_attempted"])
        self.assertEqual(row["diagnostics"]["http_status"], 403)
        self.assertEqual(row["diagnostics"]["api_calls"], 1)

    @override_settings(SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_invalid_200_payload_is_not_normalized_or_tally_ready(self):
        party_gstin = "33AALFE2101R1ZF"
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        provider = SandboxGSTProvider(config(), Opener([{"code": 200, "data": {}}]))
        with patch.object(GSTLookupService, "from_settings", return_value=GSTLookupService(provider)):
            status, party = process_gstin(party_gstin, batch=SimpleNamespace(company_gstin=GSTIN, source_parties={}), force=True)
        row = result_row(party_gstin, status, party)
        self.assertEqual(status, "Sandbox Lookup Failed")
        # An unusable payload is an unresolved taxpayer profile, never a
        # ready GSTIN fallback.
        self.assertFalse(row["tally_ready"])
        self.assertTrue(row["attention_required"])
        self.assertEqual(row["party_name"], "")
        # But it must never be presented as a completed party master.
        self.assertFalse(row["party_details_complete"])
        self.assertFalse(row["diagnostics"]["normalized"])
        self.assertEqual(row["diagnostics"]["http_status"], 200)
        self.assertEqual(row["diagnostics"]["api_calls"], 1)
        # The real HTTP 200/empty-payload failure must reach the API response
        # instead of being hidden behind a bare SANDBOX_PARTY_DETAILS_UNAVAILABLE.
        self.assertEqual(row["sandbox_lookup"]["attempted"], True)
        self.assertEqual(row["sandbox_lookup"]["success"], False)
        self.assertEqual(row["sandbox_lookup"]["http_status"], 200)
        self.assertEqual(row["sandbox_lookup"]["error_code"], "SANDBOX_INVALID_RESPONSE")

    @override_settings(SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_sandbox_http_500_keeps_real_diagnostics_and_needs_attention_not_gstin_fallback(self):
        class BodiedHTTPError(HTTPError):
            def __init__(self, body):
                self._body = body.encode()
                super().__init__("https://api.sandbox.co.in/gst/compliance/public/gstin/search",
                                  500, "Internal Server Error", {"x-request-id": "req-500"}, None)
            def read(self): return self._body
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        def reject(request, timeout=None):
            raise BodiedHTTPError(json.dumps({"code": 500, "message": "Internal Server Error",
                                              "transaction_id": "txn-abc-123"}))
        provider = SandboxGSTProvider(config(), reject)

        with patch.object(GSTLookupService, "from_settings", return_value=GSTLookupService(provider)):
            status, party = process_gstin("33AALFE2101R1ZF", force=True)

        row = result_row("33AALFE2101R1ZF", status, party)
        self.assertEqual(status, "Sandbox Lookup Failed")
        self.assertFalse(row["tally_ready"])
        self.assertTrue(row["attention_required"])
        self.assertEqual(row["party_name"], "")
        self.assertEqual(row["name"], "")
        self.assertEqual(row["name_source"], "SANDBOX_LOOKUP_FAILED")
        self.assertEqual(row["sandbox_lookup"]["attempted"], True)
        self.assertEqual(row["sandbox_lookup"]["http_status"], 500)
        self.assertEqual(row["sandbox_lookup"]["error_code"], "SANDBOX_PROVIDER_ERROR")
        self.assertEqual(row["sandbox_lookup"]["error_message"], "Internal Server Error")
        self.assertIn("Internal Server Error", row["sandbox_lookup"]["response_body"])
        self.assertEqual(row["sandbox_lookup"]["endpoint"], "/gst/compliance/public/gstin/search")
        self.assertTrue(row["sandbox_lookup"]["auth_token_attached"])
        self.assertTrue(row["sandbox_lookup"]["api_key_attached"])
        self.assertFalse(row["sandbox_lookup"]["taxpayer_session_attached"])

    @patch.object(GSTLookupService, "from_settings")
    def test_successful_normalized_result_is_cached(self, factory):
        service = Mock(); service.lookup.return_value = {"gstin": GSTIN, "legal_name": "ABC PRIVATE LIMITED", "trade_name": "ABC",
            "status": "Active", "principal_address": "10, Chennai - 600001", "state": "Tamil Nadu",
            "taxpayer_type": "Regular", "pincode": "600001"}; factory.return_value = service
        source, first = GSTLookupService.lookup_cached(GSTIN); cached_source, second = GSTLookupService.lookup_cached(GSTIN)
        self.assertEqual((source, cached_source), ("sandbox", "existing")); self.assertEqual(first["trade_name"], second["trade_name"])
        service.lookup.assert_called_once_with(GSTIN, company_gstin="")

    @patch.object(GSTLookupService, "from_settings")
    def test_partial_sandbox_record_is_fetched_with_separate_warning(self, factory):
        service = Mock(); service.lookup.return_value = {"gstin": GSTIN, "legal_name": "ABC PRIVATE LIMITED",
            "trade_name": "ABC", "status": "Active", "principal_address": None, "state": "Tamil Nadu"}; factory.return_value = service
        source, _ = GSTLookupService.lookup_cached(GSTIN)
        party = GSTParty.objects.get(gstin=GSTIN); row = result_row(GSTIN, "Fetched", party)
        self.assertEqual(source, "sandbox"); self.assertEqual(party.party_data_status, "Complete")
        self.assertEqual(row["status"], "Fetched"); self.assertEqual(row["warning_reason"], "Address unavailable")
        self.assertNotEqual(row["status"], "Ready with Warning")

    @override_settings(SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_recent_gstin_only_fallback_record_is_retried_with_sandbox(self):
        party_gstin = "33AALFE2101R1ZF"
        GSTParty.objects.create(gstin=party_gstin, trade_name=party_gstin, legal_name="",
                                taxpayer_type="Regular", lookup_source="GSTIN",
                                lookup_status="Sandbox Lookup Failed", party_data_status="Complete",
                                last_fetched_at=timezone.now())
        cache.set(SandboxGSTProvider(config()).cache_key("access-token"), encrypt("access-token-value"), 300)
        provider = SandboxGSTProvider(config(), Opener([{"code": 200, "data": {"status_cd": "1", "data": {
            "gstin": party_gstin, "lgnm": "EM VEERU & CO", "tradeNam": "EM VEERU & CO",
            "sts": "Active", "dty": "Regular", "pradr": {"addr": {
                "bno": "No.27", "loc": "Chinnavayampatti", "stcd": "Tamil Nadu",
                "pncd": "626104"}}}}}]))
        with patch.object(GSTLookupService, "from_settings", return_value=GSTLookupService(provider)):
            status, party = process_gstin(party_gstin, force=True)

        self.assertEqual(status, "Fetched")
        self.assertEqual(provider.lookup_request_count, 1)
        self.assertEqual(party.trade_name, "EM VEERU & CO")
        self.assertEqual(party.principal_place_of_business, "No.27, Chinnavayampatti")
        self.assertEqual(party.pincode, "626104")

    @patch.object(GSTLookupService, "lookup_cached", side_effect=GSTLookupTimeoutError())
    @override_settings(SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_timeout_is_sandbox_lookup_failed_and_needs_attention(self, lookup):
        status, party = process_gstin(GSTIN, force=True); row = result_row(GSTIN, status, party)
        self.assertEqual(row["status"], "Sandbox Lookup Failed")
        self.assertEqual(row["diagnostics"]["actual_provider_used"], "sandbox")
        self.assertFalse(row["tally_ready"])
        self.assertTrue(row["attention_required"])
        self.assertEqual(row["party_name"], "")
        self.assertNotIn(row["status"], {"GSTIN Fallback", "Ready with Warning"})

@override_settings(GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="sandbox", GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
                   GST_LOOKUP_FALLBACK_PROVIDER="", SANDBOX_BASE_URL="https://api.sandbox.co.in",
                   SANDBOX_API_KEY="configured", SANDBOX_API_SECRET="configured")
class DynamicTaxpayerIdentityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="dynamic-sandbox")
        self.batch = GSTImportBatch.objects.create(file_name="batch.json", file_type="JSON", company_gstin=GSTIN)
        self.factory = APIRequestFactory()

    def test_status_uses_current_batch_company_gstin_and_is_lookup_ready_without_a_session(self):
        request = self.factory.get("/api/gst/sandbox/status/", {"batch_id": self.batch.id}); force_authenticate(request, user=self.user)
        response = SandboxStatusView.as_view()(request)
        self.assertTrue(response.data["configured"]); self.assertEqual(response.data["company_gstin"], GSTIN)
        self.assertFalse(response.data["requires_username"])
        self.assertTrue(response.data["lookup_ready"])
        self.assertFalse(response.data["session_required"])
        self.assertNotIn("code", response.data)

    def test_status_rejects_trailing_slash_batch_id_as_clean_json_400(self):
        request = self.factory.get("/api/gst/sandbox/status/", {"batch_id": f"{self.batch.id}/"}); force_authenticate(request, user=self.user)
        response = SandboxStatusView.as_view()(request)
        self.assertEqual(response.status_code, 400); self.assertEqual(response.data["code"], "INVALID_BATCH_ID")

    def test_status_rejects_non_numeric_batch_id_as_clean_json_400(self):
        request = self.factory.get("/api/gst/sandbox/status/", {"batch_id": "abc"}); force_authenticate(request, user=self.user)
        response = SandboxStatusView.as_view()(request)
        self.assertEqual(response.status_code, 400); self.assertEqual(response.data["code"], "INVALID_BATCH_ID")

    def test_status_requires_batch_id_as_clean_json_400(self):
        request = self.factory.get("/api/gst/sandbox/status/"); force_authenticate(request, user=self.user)
        response = SandboxStatusView.as_view()(request)
        self.assertEqual(response.status_code, 400); self.assertEqual(response.data["code"], "BATCH_ID_REQUIRED")

    @patch.object(SandboxGSTProvider, "authenticate", return_value="secret-token")
    def test_authenticate_action_calls_real_application_auth_and_does_not_require_otp(self, authenticate):
        request = self.factory.post("/api/gst/sandbox/authenticate/", {"batch_id": self.batch.id}, format="json")
        force_authenticate(request, user=self.user)

        response = SandboxAuthenticateView.as_view()(request)

        authenticate.assert_called_once_with(force=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["authentication_attempted"])
        self.assertTrue(response.data["authenticated"])
        self.assertFalse(response.data["otp_required"])
        self.assertTrue(response.data["lookup_ready"])
        self.assertNotIn("secret-token", json.dumps(response.data))

    @patch.object(SandboxGSTProvider, "request_otp", return_value={"otp_required": True, "code": "OTP_SENT"})
    def test_request_otp_passes_dynamic_username_and_batch_gstin(self, request_otp):
        request = self.factory.post("/api/gst/sandbox/request-otp/", {"batch_id": self.batch.id, "username": "portal-user"}, format="json")
        force_authenticate(request, user=self.user); response = SandboxRequestOTPView.as_view()(request)
        self.assertEqual(response.status_code, 200); request_otp.assert_called_once_with("portal-user", GSTIN)

    @override_settings(GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="sandbox", GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
                       SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_status_reports_lookup_ready_from_configuration_not_taxpayer_session(self):
        request = self.factory.get(f"/api/gst/sandbox/status/?batch_id={self.batch.id}")
        force_authenticate(request, user=self.user)

        response = SandboxStatusView.as_view()(request)

        self.assertTrue(response.data["provider_configured"])
        self.assertFalse(response.data["session_active"])
        self.assertFalse(response.data["session_required"])
        self.assertFalse(response.data["otp_required"])
        self.assertTrue(response.data["lookup_ready"])
        self.assertFalse(response.data["lookup_failed"])
        self.assertNotIn("code", response.data)

    @patch.object(SandboxGSTProvider, "verify_otp", return_value={"verified": True, "session_active": True,
                                                                  "code": "TAXPAYER_SESSION_ACTIVE"})
    def test_verify_endpoint_returns_explicit_active_lookup_state(self, verify_otp):
        request = self.factory.post("/api/gst/sandbox/verify-otp/", {
            "batch_id": self.batch.id, "username": "portal-user", "otp": "123456"}, format="json")
        force_authenticate(request, user=self.user)

        response = SandboxVerifyOTPView.as_view()(request)

        self.assertTrue(response.data["session_active"])
        self.assertFalse(response.data["session_required"])
        self.assertFalse(response.data["session_expired"])
        self.assertFalse(response.data["otp_required"])
        self.assertTrue(response.data["lookup_ready"])

    @override_settings(GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="sandbox", GST_LOOKUP_PRIMARY_PROVIDER="sandbox",
                       SANDBOX_BASE_URL="https://api.sandbox.co.in", SANDBOX_API_KEY="configured",
                       SANDBOX_API_SECRET="configured")
    def test_expired_cached_session_does_not_block_public_lookup_readiness(self):
        from django.core.cache import cache
        cache.set(SandboxGSTProvider.from_settings().session_key(GSTIN), {"token": "expired", "expires_at": (timezone.now() - timedelta(seconds=1)).isoformat()}, 300)
        request = self.factory.get(f"/api/gst/sandbox/status/?batch_id={self.batch.id}")
        force_authenticate(request, user=self.user)

        response = SandboxStatusView.as_view()(request)

        self.assertFalse(response.data["session_active"])
        self.assertTrue(response.data["session_expired"])
        self.assertFalse(response.data["session_required"])
        self.assertTrue(response.data["lookup_ready"])

    @patch.object(SandboxGSTProvider, "request_otp")
    def test_missing_username_returns_specific_400_without_sandbox_call(self, request_otp):
        request = self.factory.post("/api/gst/sandbox/request-otp/", {"batch_id": self.batch.id, "username": ""}, format="json")
        force_authenticate(request, user=self.user); response = SandboxRequestOTPView.as_view()(request)
        self.assertEqual(response.status_code, 400); self.assertEqual(response.data["code"], "GST_USERNAME_REQUIRED")
        request_otp.assert_not_called()

    @patch.object(SandboxGSTProvider, "request_otp", side_effect=SandboxOTPRequestFailure(
        "OTP_REQUEST_REJECTED", "Sandbox rejected the GST portal username or taxpayer session."))
    def test_otp_rejection_message_has_no_duplicate_operation_prefix(self, request_otp):
        request = self.factory.post("/api/gst/sandbox/request-otp/", {
            "batch_id": self.batch.id, "username": "portal-user"}, format="json")
        force_authenticate(request, user=self.user)

        response = SandboxRequestOTPView.as_view()(request)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "OTP_REQUEST_REJECTED")
        self.assertEqual(response.data["message"], "Sandbox rejected the GST portal username or taxpayer session.")

    @patch("gst_tally.views.process_gstin")
    def test_targeted_retry_processes_only_requested_batch_gstin(self, process):
        other_gstin = "29AAACB2894G1ZP"
        GSTInvoice.objects.create(import_batch=self.batch, invoice_no="1", customer_gstin="33AALFE2101R1ZF")
        GSTInvoice.objects.create(import_batch=self.batch, invoice_no="2", customer_gstin=other_gstin)
        GSTParty.objects.create(gstin="33AALFE2101R1ZF", lookup_status="OTP Required", party_data_status="Incomplete")
        GSTParty.objects.create(gstin=other_gstin, lookup_status="OTP Required", party_data_status="Incomplete")
        process.return_value = ("Sandbox Lookup Failed", None)
        request = self.factory.post("/api/import-batches/fetch-parties/", {
            "retry_incomplete": True, "gstins": ["33AALFE2101R1ZF"]}, format="json")
        force_authenticate(request, user=self.user)

        response = BatchPartiesView.as_view()(request, pk=self.batch.id)

        self.assertEqual(response.status_code, 200)
        process.assert_called_once_with("33AALFE2101R1ZF", batch=self.batch, force=True)
        self.assertEqual(response.data["total"], 1)
