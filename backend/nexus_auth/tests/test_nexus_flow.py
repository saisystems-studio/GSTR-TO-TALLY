from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from nexus_auth.client import NexusError
from nexus_auth.models import NexusLink

LIC_OK = {"authorized": True, "state": "active", "license_plan": "ANNUAL", "license_status": "ACTIVE",
          "expires_at": "2027-11-14T08:02:47Z", "lease_until": "2099-01-01T00:00:00Z"}
ACCOUNT = {"name": "R", "email": "r@example.com", "contact_number": "9876543210", "user_id": "700456789", "tally_serial": "700456789"}
DEVICE = {"client_device_id": "dev-1", "fingerprint": "fp", "device_name": "PC", "app_version": "1"}


@override_settings(NEXUS_PRODUCT_KEY="k")
class NexusFlowTests(APITestCase):
    def setUp(self):
        cache.clear()

    def login(self, license_=LIC_OK):
        with mock.patch("nexus_auth.views.call", return_value={"token": "nx", "must_change_password": False, "account": ACCOUNT, "license": license_}):
            return self.client.post("/api/nexus/login/", {"user_id": "700456789", "password": "p", "device": DEVICE}, format="json")

    def test_authorized_login_issues_local_jwt_and_encrypted_link(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        link = NexusLink.objects.get(nexus_user_id="700456789")
        self.assertNotIn("nx", link.token_enc)
        me = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        self.assertEqual(me.status_code, 200)

    def test_unlicensed_login_gets_no_token(self):
        for state, code in (("pending", "activation_required"), ("expired", "license_expired"), ("cancelled", "license_inactive"), ("device_not_authorized", "device_mismatch")):
            r = self.login({"authorized": False, "state": state})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.data["code"], code)
            self.assertNotIn("access", r.data)
        self.assertFalse(NexusLink.objects.exists())

    def test_nexus_unreachable_never_logs_in(self):
        with mock.patch("nexus_auth.views.call", side_effect=NexusError(503, "nexus_unreachable", "down")):
            r = self.client.post("/api/nexus/login/", {"user_id": "1", "password": "p", "device": DEVICE}, format="json")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("access", r.data)

    def test_invalid_credentials_passthrough(self):
        with mock.patch("nexus_auth.views.call", side_effect=NexusError(401, "invalid_credentials", "Invalid User ID or password.")):
            r = self.client.post("/api/nexus/login/", {"user_id": "1", "password": "x", "device": DEVICE}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (401, "invalid_credentials"))

    def test_temp_password_requires_change_before_session(self):
        with mock.patch("nexus_auth.views.call", return_value={"token": "nx", "must_change_password": True, "account": ACCOUNT, "license": {}}):
            r = self.client.post("/api/nexus/login/", {"user_id": "1", "password": "p", "device": DEVICE}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (403, "password_change_required"))
        self.assertNotIn("access", r.data)

    def test_revocation_by_nexus_ends_local_session(self):
        access = self.login().data["access"]
        h = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
        err = NexusError(401, "session_revoked", "moved", {"reason": ["device_replaced"]})
        with mock.patch("nexus_auth.views.call", side_effect=err):
            self.assertEqual(self.client.get("/api/nexus/session/", **h).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me/", **h).status_code, 401)

    def test_offline_allowed_only_within_lease(self):
        access = self.login().data["access"]
        h = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
        down = NexusError(503, "nexus_unreachable", "down")
        with mock.patch("nexus_auth.views.call", side_effect=down):
            self.assertEqual(self.client.get("/api/nexus/session/", **h).status_code, 200)
        NexusLink.objects.update(lease_until="2020-01-01T00:00:00Z")
        with mock.patch("nexus_auth.views.call", side_effect=down):
            self.assertEqual(self.client.get("/api/nexus/session/", **h).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me/", **h).status_code, 401)  # lease expired

    def test_register_uses_detected_serial_not_client_value(self):
        detected = {"detected": True, "tally_serial": "700111222", "identity": {}}
        with mock.patch("nexus_auth.views.detected_tally", return_value=detected), mock.patch("nexus_auth.views.call", return_value={"user_id": "700111222"}) as c:
            r = self.client.post("/api/nexus/register/", {"name": "A", "email": "a@b.co", "contact_number": "9876543210", "tally_serial": "FAKE"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(c.call_args[0][2]["tally_serial"], "700111222")

    def test_register_blocked_when_tally_not_running(self):
        off = {"detected": False, "code": "tally_not_running", "message": "x"}
        with mock.patch("nexus_auth.views.detected_tally", return_value=off), mock.patch("nexus_auth.views.call") as c:
            r = self.client.post("/api/nexus/register/", {"name": "A"}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (409, "tally_not_running"))
        c.assert_not_called()

    def test_usage_open_deduplicated(self):
        access = self.login().data["access"]
        h = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
        with mock.patch("nexus_auth.views.call", return_value={"recorded": True}) as c:
            self.client.post("/api/nexus/usage/open/", {}, format="json", **h)
            second = self.client.post("/api/nexus/usage/open/", {}, format="json", **h)
        self.assertEqual(c.call_count, 1)
        self.assertFalse(second.data["recorded"])

    def test_replacement_activation_confirm_returns_session(self):
        conf = {"token": "nx2", "license": LIC_OK, "account": ACCOUNT, "device_replaced": True}
        with mock.patch("nexus_auth.views.call", return_value=conf):
            r = self.client.post("/api/nexus/activate/confirm/", {"ticket": "t", "device": DEVICE, "confirm_transfer": True}, format="json")
        self.assertEqual((r.status_code, r.data["device_replaced"]), (200, True))

    def test_enforce_blocks_unlinked_users(self):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.tokens import RefreshToken
        u = get_user_model().objects.create_user("legacy", password="x")
        tok = str(RefreshToken.for_user(u).access_token)
        self.assertEqual(self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {tok}").status_code, 200)
        with override_settings(NEXUS_ENFORCE=True):
            self.assertEqual(self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {tok}").status_code, 401)


class ConnectorDiscoveryTests(APITestCase):
    """First-time serial detection: pair a browser, connector reports the serial, registration reads it."""
    def setUp(self):
        import hashlib, uuid
        from datetime import timedelta
        from django.utils import timezone
        from gst_tally.models import ConnectorEnrollment
        cache.clear()
        h = lambda v: hashlib.sha256(v.encode()).hexdigest()
        self.row = ConnectorEnrollment.objects.create(installation_id=uuid.uuid4(), poll_hash=h("poll"), proof_hash=h("proof"), expires_at=timezone.now() + timedelta(hours=1))
        self.eid = str(self.row.enrollment_id)

    def report(self, **extra):
        return self.client.post("/api/nexus/connector/discovery/", {"enrollment_id": self.eid, "poll_secret": "poll", "tally_reachable": True, "tally_serial": "700123456", **extra}, format="json")

    def test_detect_requires_pairing_then_returns_verified_serial(self):
        self.assertEqual(self.report().status_code, 200)
        unpaired = self.client.get("/api/nexus/tally/detect/", HTTP_X_DEVICE_FINGERPRINT="browser-1")
        self.assertFalse(unpaired.data["detected"])
        bad = self.client.post("/api/nexus/connector/pair/", {"enrollment_id": self.eid, "proof": "wrong"}, format="json", HTTP_X_DEVICE_FINGERPRINT="browser-1")
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/nexus/connector/pair/", {"enrollment_id": self.eid, "proof": "proof"}, format="json", HTTP_X_DEVICE_FINGERPRINT="browser-1")
        self.assertEqual(ok.status_code, 200)
        self.report(edition="TallyPrime Gold")
        det = self.client.get("/api/nexus/tally/detect/", HTTP_X_DEVICE_FINGERPRINT="browser-1")
        self.assertEqual((det.data["detected"], det.data["tally_serial"]), (True, "700123456"))
        other = self.client.get("/api/nexus/tally/detect/", HTTP_X_DEVICE_FINGERPRINT="browser-2")
        self.assertFalse(other.data["detected"])
        with mock.patch("nexus_auth.views.call", return_value={"user_id": "700123456"}) as c:
            r = self.client.post("/api/nexus/register/", {"name": "A", "email": "a@b.co", "contact_number": "9876543210"}, format="json", HTTP_X_DEVICE_FINGERPRINT="browser-1")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(c.call_args[0][2]["tally_serial"], "700123456")

    def test_tally_not_running_has_no_serial_and_rejects_bad_secret(self):
        self.client.post("/api/nexus/connector/pair/", {"enrollment_id": self.eid, "proof": "proof"}, format="json", HTTP_X_DEVICE_FINGERPRINT="b")
        self.report(tally_reachable=False, tally_serial="")
        det = self.client.get("/api/nexus/tally/detect/", HTTP_X_DEVICE_FINGERPRINT="b")
        self.assertEqual((det.data["detected"], det.data["code"]), (False, "tally_not_running"))
        bad = self.client.post("/api/nexus/connector/discovery/", {"enrollment_id": self.eid, "poll_secret": "nope", "tally_serial": "999999"}, format="json")
        self.assertEqual(bad.status_code, 401)
