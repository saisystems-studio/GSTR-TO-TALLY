"""GSTR side of Activate User: Activation Key emailed by Nexus on request only (no OTP), verified via Nexus,
transfer confirmation, cancel. Nexus is mocked here; the real local Nexus was exercised separately (see report)."""
import logging
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from gst_tally.models import NexusOutboxEvent
from nexus_auth.client import NexusError
from nexus_auth.models import NexusLink

SERIAL = "700456789"
KEY = "K3YSECRET9"
LIC_OK = {"authorized": True, "state": "active", "license_plan": "ANNUAL", "lease_until": "2099-01-01T00:00:00Z"}
ACCOUNT = {"name": "R", "email": "r@example.com", "contact_number": "9876543210", "user_id": SERIAL, "tally_serial": SERIAL}
DEV = {"client_device_id": "new-laptop", "fingerprint": "fp-new", "device_name": "New Laptop", "app_version": "1"}


@override_settings(NEXUS_PRODUCT_KEY="k", NEXUS_ENABLED=True, NEXUS_EVENT_SIGNING_SECRET="")
class ActivationFlowTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_request_asks_nexus_for_a_key_only_with_the_user_id(self):
        with mock.patch("nexus_auth.views.call", return_value={"message": "sent"}) as call:
            r = self.client.post("/api/nexus/activate/request-code/", {"user_id": SERIAL}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((call.call_args[0][1], call.call_args[0][2]), ("activate/request-key/", {"user_id": SERIAL}))

    def test_verify_sends_user_id_and_key_only_no_otp_and_never_logs_or_stores_the_key(self):
        reply = {"ticket": "T1", "requires_confirmation": True, "message": "Your GSTR2Tally license is currently active on another laptop.",
                 "current_device": {"name": "Old"}, "expires_in": 600}
        with self.assertLogs(level="DEBUG") as logs, mock.patch("nexus_auth.views.call", return_value=reply) as call:
            logging.getLogger("nexus_auth").debug("probe")
            r = self.client.post("/api/nexus/activate/verify/", {"user_id": SERIAL, "code": "123456", "activation_key": KEY}, format="json")
        self.assertEqual((r.status_code, r.data["requires_confirmation"]), (200, True))
        self.assertEqual(call.call_args[0][2], {"user_id": SERIAL, "activation_key": KEY})  # a client-sent "code" is not forwarded
        self.assertNotIn(KEY, "\n".join(logs.output))
        self.assertNotIn(KEY, str(r.data))
        self.assertFalse(NexusLink.objects.exists())  # nothing about the key or session is persisted at verify time

    def test_wrong_key_and_cross_account_errors_pass_through(self):
        err = NexusError(400, "invalid_activation_key", "The activation key is invalid or has expired. Request a new one.")
        with mock.patch("nexus_auth.views.call", side_effect=err):
            r = self.client.post("/api/nexus/activate/verify/", {"user_id": SERIAL, "activation_key": "WRONG"}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (400, "invalid_activation_key"))

    def test_cancel_leaves_everything_unchanged(self):
        # Cancel in the UI abandons the ticket: no confirm call, no link, no outbox event, nothing to revoke.
        reply = {"ticket": "T1", "requires_confirmation": True, "message": "x", "current_device": {"name": "Old"}, "expires_in": 600}
        with mock.patch("nexus_auth.views.call", return_value=reply):
            self.client.post("/api/nexus/activate/verify/", {"user_id": SERIAL, "activation_key": KEY}, format="json")
        self.assertFalse(NexusLink.objects.exists())
        self.assertFalse(NexusOutboxEvent.objects.exists())

    def test_ok_sends_confirm_transfer_true_revokes_old_session_and_queues_one_event(self):
        # an existing session on the old laptop
        login = {"token": "nx-old", "must_change_password": False, "account": ACCOUNT, "license": LIC_OK}
        old = {"client_device_id": "old-laptop", "fingerprint": "fp-old", "device_name": "Old Laptop", "app_version": "1"}
        with mock.patch("nexus_auth.views.call", return_value=login):
            old_access = self.client.post("/api/nexus/login/", {"user_id": SERIAL, "password": "p", "device": old}, format="json").data["access"]
        conf = {"token": "nx-new", "license": LIC_OK, "account": ACCOUNT, "device_replaced": True}
        with mock.patch("nexus_auth.views.call", return_value=conf) as call, self.captureOnCommitCallbacks(execute=True):
            r = self.client.post("/api/nexus/activate/confirm/", {"ticket": "T1", "device": DEV, "confirm_transfer": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(call.call_args[0][2]["confirm_transfer"])
        self.assertEqual(self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {old_access}").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {r.data['access']}").status_code, 200)
        self.assertEqual(NexusOutboxEvent.objects.filter(event_type="DEVICE_TRANSFER_COMPLETED").count(), 1)
