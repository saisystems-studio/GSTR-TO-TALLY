"""Forgot / resend / reset password through the GSTR backend. Nexus is mocked here; these tests prove GSTR's own
behaviour (neutral answers, cooldown, no false success, no secrets in logs). They do NOT prove email delivery."""
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from nexus_auth.client import NexusError
from nexus_auth.views import RESET_CODE_HOURLY_LIMIT

NEUTRAL = {"message": "If the account exists, a password reset link and code have been emailed to the registered address."}
URL = "/api/nexus/password/forgot/"


@override_settings(NEXUS_PRODUCT_KEY="k", NEXUS_ENABLED=True)
class PasswordRecoveryTests(APITestCase):
    def setUp(self):
        cache.clear()

    def forgot(self, identifier="ravi@example.com"):
        return self.client.post(URL, {"identifier": identifier}, format="json")

    def test_success_is_neutral_and_returns_cooldown(self):
        with mock.patch("nexus_auth.views.call", return_value=NEUTRAL) as call:
            r = self.forgot()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["message"], NEUTRAL["message"])
        self.assertEqual(r.data["retry_after"], 60)
        self.assertEqual(call.call_args[0][1], "auth/forgot-password/")

    def test_same_answer_for_unknown_and_known_accounts(self):
        with mock.patch("nexus_auth.views.call", return_value=NEUTRAL):
            known = self.forgot("known@example.com")
            unknown = self.forgot("nobody@example.com")
        self.assertEqual((known.status_code, known.data["message"]), (unknown.status_code, unknown.data["message"]))

    def test_upstream_failure_is_an_error_not_a_false_success_and_does_not_lock_the_user_out(self):
        with mock.patch("nexus_auth.views.call", side_effect=NexusError(503, "nexus_unreachable", "Nexus server is unreachable.")):
            r = self.forgot()
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("message", r.data)
        with mock.patch("nexus_auth.views.call", return_value=NEUTRAL):
            self.assertEqual(self.forgot().status_code, 200)  # immediate retry allowed: nothing was sent

    def test_mail_or_server_error_from_nexus_is_not_reported_as_sent(self):
        with mock.patch("nexus_auth.views.call", side_effect=NexusError(500, "server_error", "Email could not be sent.")):
            r = self.forgot()
        self.assertEqual(r.status_code, 500)

    def test_resend_cooldown_blocks_rapid_repeat(self):
        with mock.patch("nexus_auth.views.call", return_value=NEUTRAL) as call:
            self.assertEqual(self.forgot().status_code, 200)
            r = self.forgot()
        self.assertEqual((r.status_code, r.data["code"]), (429, "resend_too_soon"))
        self.assertEqual(call.call_count, 1)  # no second email request reached Nexus

    def test_hourly_limit(self):
        with mock.patch("nexus_auth.views.call", return_value=NEUTRAL) as call:
            for _ in range(RESET_CODE_HOURLY_LIMIT):
                cache.delete_many([k for k in ("nexus-fp-cd:" + __import__("nexus_auth.views", fromlist=["digest"]).digest("ravi@example.com|127.0.0.1"),)])
                self.assertEqual(self.forgot().status_code, 200)
            cache.delete("nexus-fp-cd:" + __import__("nexus_auth.views", fromlist=["digest"]).digest("ravi@example.com|127.0.0.1"))
            r = self.forgot()
        self.assertEqual((r.status_code, r.data["code"]), (429, "resend_limit"))
        self.assertEqual(call.call_count, RESET_CODE_HOURLY_LIMIT)

    def test_empty_identifier_is_rejected_without_calling_nexus(self):
        with mock.patch("nexus_auth.views.call") as call:
            r = self.forgot("  ")
        self.assertEqual((r.status_code, r.data["code"]), (400, "identifier_required"))
        call.assert_not_called()

    def test_reset_passes_code_and_password_and_maps_invalid_or_used_code(self):
        with mock.patch("nexus_auth.views.call", return_value={"message": "Password updated. You can sign in now."}) as call:
            r = self.client.post("/api/nexus/password/reset/", {"identifier": "700456789", "token": "123456", "new_password": "Fresh#Pass9"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(call.call_args[0][1], "auth/reset-password/")
        self.assertEqual(call.call_args[0][2], {"identifier": "700456789", "token": "123456", "new_password": "Fresh#Pass9"})
        used = NexusError(400, "invalid_token", "This reset link is invalid or has expired.")
        with mock.patch("nexus_auth.views.call", side_effect=used):
            r = self.client.post("/api/nexus/password/reset/", {"identifier": "700456789", "token": "123456", "new_password": "Fresh#Pass9"}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (400, "invalid_token"))

    def test_codes_passwords_and_keys_never_reach_the_logs(self):
        with self.assertLogs(level="DEBUG") as logs, mock.patch("nexus_auth.views.call", return_value=NEUTRAL):
            import logging
            logging.getLogger("nexus_auth").debug("probe")  # ensures assertLogs has a record to inspect
            self.forgot()
            self.client.post("/api/nexus/password/reset/", {"token": "654321", "new_password": "Fresh#Pass9"}, format="json")
        text = "\n".join(logs.output)
        for secret in ("654321", "Fresh#Pass9"):
            self.assertNotIn(secret, text)
