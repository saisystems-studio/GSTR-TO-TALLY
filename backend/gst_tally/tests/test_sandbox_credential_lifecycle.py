import json
from unittest.mock import patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from superadmin.models import SandboxAPIConfiguration, SuperAdminProfile
from superadmin.views import SandboxConfigurationView
from superadmin.services.sandbox_configuration import provider_config, safe_status
from gst_tally.services.gst_lookup.providers.sandbox import SandboxGSTProvider, SandboxAuthenticationFailure

GSTIN = "33AAACB2894G1ZJ"


class SandboxCredentialLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username="config-admin")
        SuperAdminProfile.objects.create(user=self.admin)
        self.user = get_user_model().objects.create_user(username="customer")
        self.factory = APIRequestFactory()
        self.calls = []
        self.invalid = False
        self.expire_once = False
        self.patch = patch.object(SandboxGSTProvider, "_post", autospec=True, side_effect=self.remote)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def remote(self, provider, path, headers, body=None, query=""):
        self.calls.append((path, dict(headers)))
        if path == provider.AUTH_PATH:
            if self.invalid:
                from gst_tally.services.gst_lookup.base import GSTLookupAuthenticationError
                error = GSTLookupAuthenticationError("Rejected")
                error.http_status = 401
                raise error
            return {"access_token": "token-" + headers["x-api-key"], "expires_in": 120}
        if self.expire_once:
            self.expire_once = False
            from gst_tally.services.gst_lookup.base import GSTLookupAuthenticationError
            error = GSTLookupAuthenticationError("Token expired")
            error.http_status = 401
            raise error
        return {"data": {"gstin": GSTIN, "lgnm": "Example Trader", "sts": "Active"}}

    def request(self, method, data=None, user=None):
        request = getattr(self.factory, method)("/api/superadmin/sandbox-configuration/", data or {}, format="json")
        force_authenticate(request, user=user or self.admin)
        return SandboxConfigurationView.as_view()(request)

    def credentials(self, suffix="A"):
        return {"provider": "sandbox", "environment": "test", "api_key": "key-" + suffix, "api_secret": "secret-" + suffix}

    def save(self, suffix="A"):
        response = self.request("put", self.credentials(suffix))
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def test_1_save_test_lookup(self):
        self.save()
        self.assertTrue(self.request("post").data["authenticated"])
        self.assertEqual(SandboxGSTProvider.from_settings().lookup(GSTIN).legal_name, "Example Trader")

    def test_2_restart_preserves_credentials(self):
        self.save()
        cache.clear()  # A new worker has no runtime token; credentials remain in DB.
        self.assertEqual(SandboxGSTProvider.from_settings().lookup(GSTIN).gstin, GSTIN)
        self.assertEqual(provider_config()["api_key"], "key-A")

    def test_3_invalid_credentials_stop_retrying(self):
        self.save()
        self.invalid = True
        self.calls.clear()
        for _ in range(3):
            with self.assertRaises(SandboxAuthenticationFailure):
                SandboxGSTProvider.from_settings().lookup(GSTIN)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(safe_status()["credentials_status"], "invalid")
        self.assertFalse(safe_status()["lookup_ready"])

    def test_4_test_and_save_replacement(self):
        self.save()
        self.assertEqual(self.request("post", self.credentials("B")).status_code, 200)
        self.assertEqual(provider_config()["api_key"], "key-A")
        self.save("B")
        self.assertEqual(provider_config()["api_key"], "key-B")

    def test_5_existing_worker_uses_new_credentials_without_restart(self):
        self.save()
        worker = SandboxGSTProvider.from_settings()
        worker.lookup(GSTIN)
        self.save("B")
        self.calls.clear()
        self.assertEqual(worker.lookup(GSTIN).gstin, GSTIN)
        self.assertEqual(self.calls[0][1]["x-api-key"], "key-B")

    def test_6_old_token_cannot_be_reused(self):
        self.save()
        worker = SandboxGSTProvider.from_settings()
        worker.lookup(GSTIN)
        old_cache_key = worker.cache_key("access-token")
        self.save("B")
        self.assertIsNone(cache.get(old_cache_key))
        self.calls.clear()
        worker.lookup(GSTIN)
        self.assertEqual(self.calls[-1][1]["authorization"], "token-key-B")
        self.assertNotEqual(old_cache_key, worker.cache_key("access-token"))
        self.assertNotIn("token-key-B", cache.get(worker.cache_key("access-token")))

    def test_7_secrets_are_encrypted_and_never_returned(self):
        response = self.save("B")
        for method in ("get", "post", "put"):
            self.assertEqual(self.request(method, self.credentials("B"), self.user).status_code, 403)
        self.assertNotIn("secret-B", json.dumps(response.data, default=str))
        self.assertNotIn("secret-B", json.dumps(self.request("get").data, default=str))
        self.assertNotIn("secret-B", SandboxAPIConfiguration.objects.get().api_secret_encrypted)

    def test_expired_token_refreshes_and_retries_once(self):
        self.save()
        worker = SandboxGSTProvider.from_settings()
        worker.lookup(GSTIN)
        self.calls.clear()
        self.expire_once = True
        worker.lookup(GSTIN)
        self.assertEqual([path for path, _ in self.calls], [worker.PUBLIC_GSTIN_SEARCH_PATH, worker.AUTH_PATH, worker.PUBLIC_GSTIN_SEARCH_PATH])

    @override_settings(GST_LOOKUP_PROVIDER="jamku", GST_LOOKUP_ENABLED=True)
    def test_saved_provider_overrides_bootstrap_provider(self):
        self.save()
        from gst_tally.services.gst_lookup.service import GSTLookupService
        self.assertIsInstance(GSTLookupService.from_settings().provider, SandboxGSTProvider)

    def test_wrong_environment_rejected_without_network(self):
        response = self.request("put", {**self.credentials(), "environment": "other"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_trailing_whitespace_in_secret_is_stripped_before_sending(self):
        # A copy-pasted secret with a trailing newline/space must not be sent
        # byte-for-byte -- Sandbox would reject it and it would be
        # misdiagnosed as "wrong credentials" when the visible value is correct.
        self.request("put", {**self.credentials(), "api_secret": "secret-A\n"})
        self.assertEqual(self.calls[-1][1]["x-api-secret"], "secret-A")

    def test_masked_placeholder_never_sent_falls_back_to_saved_credentials(self):
        self.save()
        self.calls.clear()
        response = self.request("post", {**self.credentials(), "api_key": "••••••••", "api_secret": "••••••••"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1][1]["x-api-key"], "key-A")
        self.assertEqual(self.calls[-1][1]["x-api-secret"], "secret-A")

    def test_saved_configuration_reload_authenticates_from_database_without_exposing_values(self):
        self.save()
        self.calls.clear()
        response = self.request("post", {})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["configuration_source"], "database")
        self.assertTrue(response.data["credentials_present"])
        self.assertTrue(response.data["authentication_attempted"])
        self.assertFalse(response.data["taxpayer_lookup_attempted"])
        self.assertIn("upstream_http_status", response.data)
        self.assertEqual(self.calls[-1][1]["x-api-key"], "key-A")
        self.assertEqual(self.calls[-1][1]["x-api-secret"], "secret-A")
        self.assertNotIn("key-A", json.dumps(response.data, default=str))
        self.assertNotIn("secret-A", json.dumps(response.data, default=str))

    def test_invalid_credentials_error_is_categorized_not_generic(self):
        self.save()
        self.invalid = True
        response = self.request("post", self.credentials("B"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["category"], "AUTHENTICATION_REJECTED")

    def test_401_test_connection_surfaces_real_provider_message_not_generic_contact_admin(self):
        # The generic "Contact the administrator" message is correct for a
        # live GST-import lookup failure shown to a regular user, but Test
        # Connection is a Super Admin diagnostic -- it must show Sandbox's
        # own reason instead of that generic substitution.
        self.save()
        self.invalid = True
        response = self.request("post", self.credentials("B"))
        self.assertNotIn("Contact the administrator", response.data["detail"])
        # A real party-lookup failure against the same saved config must still
        # show the safe, generic, end-user-facing message (untouched by this fix).
        with self.assertRaises(SandboxAuthenticationFailure) as ctx:
            SandboxGSTProvider.from_settings().lookup(GSTIN)
        self.assertIn("Contact the administrator", ctx.exception.safe_message)
