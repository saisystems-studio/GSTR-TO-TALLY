"""Two-device Nexus transfer, outbox delivery, enforcement rollout and recovery flows.
Nexus itself is mocked; no real Nexus, customer data or production setting is touched."""
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from gst_tally.models import NexusOutboxEvent
from gst_tally.services import device_transfer
from nexus_auth.client import NexusError
from nexus_auth.models import NexusLink

SERIAL = "700456789"
LIC_OK = {"authorized": True, "state": "active", "license_plan": "ANNUAL", "license_status": "ACTIVE",
          "expires_at": "2027-11-14T08:02:47Z", "lease_until": "2099-01-01T00:00:00Z"}
ACCOUNT = {"name": "R", "email": "r@example.com", "contact_number": "9876543210", "user_id": SERIAL, "tally_serial": SERIAL}
OLD = {"client_device_id": "old-laptop", "fingerprint": "fp-old", "device_name": "Old Laptop", "app_version": "1"}
NEW = {"client_device_id": "new-laptop", "fingerprint": "fp-new", "device_name": "New Laptop", "app_version": "1"}


def bearer(tok):
    return {"HTTP_AUTHORIZATION": f"Bearer {tok}"}


@override_settings(NEXUS_PRODUCT_KEY="k", NEXUS_ENABLED=True, NEXUS_EVENT_SIGNING_SECRET="")  # no real network from tests
class TwoDeviceTransferTests(APITestCase):
    def setUp(self):
        cache.clear()

    def login(self, device, lic=LIC_OK):
        with mock.patch("nexus_auth.views.call", return_value={"token": "nx-" + device["client_device_id"], "must_change_password": False,
                                                              "account": ACCOUNT, "license": lic}):
            return self.client.post("/api/nexus/login/", {"user_id": SERIAL, "password": "p", "device": device}, format="json")

    def confirm_on_new_laptop(self, replaced=True):
        conf = {"token": "nx-new", "license": LIC_OK, "account": ACCOUNT, "device_replaced": replaced}
        with mock.patch("nexus_auth.views.call", return_value=conf) as call:
            r = self.client.post("/api/nexus/activate/confirm/", {"ticket": "t", "device": NEW, "confirm_transfer": True}, format="json")
        return r, call

    def test_full_old_to_new_laptop_flow(self):
        old_access = self.login(OLD).data["access"]
        self.assertEqual(self.client.get("/api/auth/me/", **bearer(old_access)).status_code, 200)

        with self.captureOnCommitCallbacks(execute=True):
            r, call = self.confirm_on_new_laptop()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["device_replaced"])
        sent = call.call_args[0][2]
        self.assertTrue(sent["confirm_transfer"])  # explicit user confirmation, no staff approval step
        new_access = r.data["access"]

        # old laptop is blocked immediately -- no waiting for the 5-minute poll
        blocked = self.client.get("/api/auth/me/", **bearer(old_access))
        self.assertEqual(blocked.status_code, 401)
        self.assertEqual(NexusLink.objects.get(client_device_id="old-laptop").revoked_reason, "device_replaced")
        # new laptop works and a fresh login on it succeeds
        self.assertEqual(self.client.get("/api/auth/me/", **bearer(new_access)).status_code, 200)
        self.assertEqual(self.login(NEW).status_code, 200)

        ev = NexusOutboxEvent.objects.get(event_type="DEVICE_TRANSFER_COMPLETED")
        self.assertEqual(ev.payload["tally_serial"], SERIAL)
        self.assertEqual(ev.payload["old_device"]["client_device_ids"], ["old-laptop"])
        self.assertEqual(ev.payload["new_device"]["name"], "New Laptop")
        blob = str(ev.payload).lower()
        for secret in ("token", "password", "activation_key", "nx-"):
            self.assertNotIn(secret, blob)

    def test_first_activation_without_replacement_sends_no_event(self):
        self.confirm_on_new_laptop(replaced=False)
        self.assertFalse(NexusOutboxEvent.objects.exists())

    def test_nexus_linked_user_gets_no_local_popup_and_no_password_prompt(self):
        access = self.login(OLD).data["access"]
        h = bearer(access)
        body = {"device_fingerprint": "fp-new", "device_name": "New Laptop"}
        pre = self.client.post("/api/gst-tally/license/device-transfer/preview/", body, format="json", **h)
        self.assertFalse(pre.data["transfer_available"])
        self.assertEqual(pre.data["reason"], "USE_NEXUS_FLOW")
        conf = self.client.post("/api/gst-tally/license/device-transfer/confirm/", {**body, "password": ""}, format="json", **h)
        self.assertEqual((conf.status_code, conf.data["code"]), (409, "USE_NEXUS_FLOW"))

    def test_expired_license_is_refused_at_login_and_activation(self):
        expired = {"authorized": False, "state": "expired", "license_plan": "ANNUAL"}
        r = self.login(OLD, lic=expired)
        self.assertEqual((r.status_code, r.data["code"]), (403, "license_expired"))
        self.assertNotIn("access", r.data)
        conf = {"token": "t", "license": expired, "account": ACCOUNT, "device_replaced": False}
        with mock.patch("nexus_auth.views.call", return_value=conf):
            r = self.client.post("/api/nexus/activate/confirm/", {"ticket": "t", "device": NEW, "confirm_transfer": True}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (403, "license_expired"))

    def test_other_device_still_active_is_reported_not_silently_replaced(self):
        mismatch = {"authorized": False, "state": "device_not_authorized"}
        r = self.login(NEW, lic=mismatch)
        self.assertEqual((r.status_code, r.data["code"]), (403, "device_mismatch"))

    def test_password_recovery_passthrough(self):
        with mock.patch("nexus_auth.views.call", return_value={"detail": "sent"}) as call:
            self.assertEqual(self.client.post("/api/nexus/password/forgot/", {"identifier": SERIAL}, format="json").status_code, 200)
            self.assertEqual(call.call_args[0][1], "auth/forgot-password/")
            self.assertEqual(self.client.post("/api/nexus/password/reset/", {"identifier": SERIAL, "token": "abc", "new_password": "N3w-pass!"}, format="json").status_code, 200)
            self.assertEqual(call.call_args[0][1], "auth/reset-password/")
        bad = NexusError(400, "invalid_token", "bad", {})
        with mock.patch("nexus_auth.views.call", side_effect=bad):
            self.assertEqual(self.client.post("/api/nexus/password/reset/", {"identifier": SERIAL, "token": "x", "new_password": "y"}, format="json").status_code, 400)


@override_settings(NEXUS_PRODUCT_KEY="k", NEXUS_ENABLED=True, NEXUS_EVENT_SIGNING_SECRET="s", NEXUS_EVENTS_PATH="events/device-transfer/")
class OutboxDeliveryTests(APITestCase):
    def event(self):
        user = get_user_model().objects.create_user("nexus_" + SERIAL)
        return device_transfer.queue_nexus_transfer_event(SERIAL, user, ["old-laptop"], "New Laptop")

    def test_signed_delivery_marks_sent_once(self):
        ev = self.event()
        with mock.patch("nexus_auth.client.call", return_value={}) as call:
            device_transfer.deliver_event(ev.pk)
            device_transfer.deliver_event(ev.pk)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(call.call_args.kwargs["sign"])
        self.assertEqual(call.call_args[0][2]["event_id"], str(ev.event_id))
        ev.refresh_from_db()
        self.assertEqual(ev.status, "SENT")

    def test_endpoint_not_deployed_keeps_event_pending_without_failing_it(self):
        ev = self.event()
        with mock.patch("nexus_auth.client.call", side_effect=NexusError(404, "nexus_error", "nf")):
            for _ in range(device_transfer.MAX_ATTEMPTS + 2):
                NexusOutboxEvent.objects.filter(pk=ev.pk).update(next_attempt_at=ev.created_at)
                device_transfer.deliver_event(ev.pk)
        ev.refresh_from_db()
        self.assertEqual(ev.status, "PENDING")
        self.assertIn("not available", ev.last_error)

    def test_missing_signing_secret_sends_nothing(self):
        ev = self.event()
        with override_settings(NEXUS_EVENT_SIGNING_SECRET=""), mock.patch("nexus_auth.client.call") as call:
            device_transfer.deliver_event(ev.pk)
        call.assert_not_called()

    def test_command_reports_config_without_secret(self):
        out = StringIO()
        with mock.patch("nexus_auth.client.call", return_value={}):
            call_command("deliver_nexus_events", stdout=out)
        self.assertIn("signing secret configured: yes", out.getvalue())
        self.assertNotIn("=s", out.getvalue())


class EnforcementRolloutTests(APITestCase):
    def token(self, user):
        return str(RefreshToken.for_user(user).access_token)

    def test_enforce_blocks_only_unlinked_non_staff(self):
        User = get_user_model()
        legacy = User.objects.create_user("legacy", password="x")
        staff = User.objects.create_user("staffer", password="x", is_staff=True)
        linked = User.objects.create_user("nexus_1", password="x")
        link = NexusLink.objects.create(user=linked, nexus_user_id="1", client_device_id="d", license=LIC_OK)
        linked_refresh = RefreshToken.for_user(linked)
        linked_refresh["nexus_link"] = link.pk
        for enforce, expected in ((False, (200, 200, 200)), (True, (401, 200, 200))):
            with override_settings(NEXUS_ENFORCE=enforce):
                got = (self.client.get("/api/auth/me/", **bearer(self.token(legacy))).status_code,
                       self.client.get("/api/auth/me/", **bearer(self.token(staff))).status_code,
                       self.client.get("/api/auth/me/", **bearer(str(linked_refresh.access_token))).status_code)
            self.assertEqual(got, expected, f"enforce={enforce}")

    def test_readiness_report_is_read_only_and_lists_legacy_accounts(self):
        User = get_user_model()
        User.objects.create_user("legacy2", password="x")
        before = list(User.objects.values_list("id", "username", "password"))
        out = StringIO()
        call_command("nexus_enforce_readiness", stdout=out)
        self.assertIn("WOULD BE BLOCKED", out.getvalue())
        self.assertIn("legacy2", out.getvalue())
        self.assertEqual(before, list(User.objects.values_list("id", "username", "password")))
   