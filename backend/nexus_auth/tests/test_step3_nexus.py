"""Step 3 for Nexus-linked sessions: Nexus is the license authority; local license rows are never touched.
Nexus and Tally are mocked; only the throwaway test database is used."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from gst_tally.models import GSTImportBatch, LicensedDevice, ProductLicense
from gst_tally.services.product_license import pre_import_security_check
from gst_tally.tests.test_product_license_security import make_license, make_user
from nexus_auth.models import NexusLink

SERIAL = "776776311"
GSTIN = "33AFHPM6103Q1Z8"
LIC_OK = {"authorized": True, "state": "active", "license_plan": "ANNUAL", "expires_at": "2027-11-14T08:02:47Z",
          "lease_until": "2099-01-01T00:00:00Z"}
ACCOUNT = {"name": "R", "email": "r@example.com", "contact_number": "9876543210", "user_id": SERIAL, "tally_serial": SERIAL}
OLD = {"client_device_id": "old-laptop", "fingerprint": "fp-old", "device_name": "Old Laptop", "app_version": "1"}
NEW = {"client_device_id": "new-laptop", "fingerprint": "fp-new", "device_name": "New Laptop", "app_version": "1"}


def tally(serial=SERIAL, gstin=GSTIN):
    return (mock.patch("gst_tally.services.product_license.step3_connection_check", return_value={
        "read_connected": True, "can_import": True, "company_name": "Co", "company_gstin": gstin}),
            mock.patch("gst_tally.services.product_license.read_tally_license",
                       return_value={"license_available": True, "serial_number": serial}))


@override_settings(NEXUS_PRODUCT_KEY="k", NEXUS_ENABLED=True, NEXUS_EVENT_SIGNING_SECRET="")  # no real network from tests
class NexusStep3Tests(APITestCase):
    def setUp(self):
        cache.clear()
        # Legacy local account that already holds this serial (the real-world conflict).
        self.legacy = make_user("legacy_owner", "legacy@example.com")
        self.legacy_license = make_license(self.legacy, serial=SERIAL, gstin=GSTIN, allowed_devices=1)
        LicensedDevice.objects.create(license=self.legacy_license, device_fingerprint="legacy-pc")
        self.batch = GSTImportBatch.objects.create(file_name="r.json", file_type="JSON", company_gstin=GSTIN)

    def login(self, device, lic=LIC_OK):
        with mock.patch("nexus_auth.views.call", return_value={"token": "nx-" + device["client_device_id"], "must_change_password": False,
                                                              "account": ACCOUNT, "license": lic}):
            return self.client.post("/api/nexus/login/", {"user_id": SERIAL, "password": "p", "device": device}, format="json")

    def user(self):
        return get_user_model().objects.get(username="nexus_" + SERIAL)

    def check(self, device_id, **kw):
        a, b = tally(**kw)
        with a, b:
            return pre_import_security_check(self.batch, self.user(), device_fingerprint=device_id, device_name=device_id)

    def snapshot(self):
        return (list(ProductLicense.objects.values_list("id", "customer_id", "licensed_tally_serial", "status")),
                list(LicensedDevice.objects.values_list("id", "license_id", "device_fingerprint", "status")))

    def test_nexus_user_passes_without_touching_legacy_owner(self):
        self.login(OLD)
        before = self.snapshot()
        result = self.check("old-laptop")
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(result["tally_license"], {"registered_serial": SERIAL, "detected_serial": SERIAL, "match": True})
        self.assertEqual(result["license_source"], "nexus")
        self.assertNotIn("LICENSE_ASSIGNED_TO_ANOTHER_ACCOUNT", [e["code"] for e in result["errors"]])
        self.assertEqual(self.snapshot(), before)  # no local license created, moved or re-authorized
        self.legacy_license.refresh_from_db()
        self.assertEqual(self.legacy_license.customer_id, self.legacy.id)

    def test_wrong_tally_serial_is_blocked(self):
        self.login(OLD)
        result = self.check("old-laptop", serial="111111111")
        self.assertFalse(result["ready"])
        self.assertIn("TALLY_SERIAL_MISMATCH", [e["code"] for e in result["errors"]])

    def test_wrong_company_is_blocked(self):
        self.login(OLD)
        self.assertIn("COMPANY_GSTIN_MISMATCH", [e["code"] for e in self.check("old-laptop", gstin="29AAAAA0000A1Z5")["errors"]])

    def test_unactivated_laptop_is_not_authorized(self):
        self.login(OLD)
        result = self.check("some-other-laptop")
        self.assertFalse(result["ready"])
        self.assertEqual(result["errors"][0]["code"], "DEVICE_NOT_AUTHORIZED")

    def test_unauthorized_nexus_license_blocks(self):
        self.login(OLD)
        NexusLink.objects.filter(client_device_id="old-laptop").update(license={"authorized": False, "state": "expired"})
        result = self.check("old-laptop")
        self.assertFalse(result["ready"])
        self.assertIn("NEXUS_LICENSE_NOT_AUTHORIZED", [e["code"] for e in result["errors"]])

    def test_two_laptop_transfer_then_step3_and_import_gate(self):
        old_access = self.login(OLD).data["access"]
        self.assertTrue(self.check("old-laptop")["ready"])
        conf = {"token": "nx-new", "license": LIC_OK, "account": ACCOUNT, "device_replaced": True}
        with mock.patch("nexus_auth.views.call", return_value=conf) as call, self.captureOnCommitCallbacks(execute=True):
            r = self.client.post("/api/nexus/activate/confirm/", {"ticket": "t", "device": NEW, "confirm_transfer": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(call.call_args[0][2]["confirm_transfer"])
        # old laptop: session ended and Step 3 no longer passes
        self.assertEqual(self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {old_access}").status_code, 401)
        self.assertEqual(self.check("old-laptop")["errors"][0]["code"], "DEVICE_NOT_AUTHORIZED")
        # new laptop: Step 3 passes through the real endpoint and import gate with the refreshed session
        new_h = {"HTTP_AUTHORIZATION": "Bearer " + r.data["access"], "HTTP_X_DEVICE_ID": "new-laptop"}
        a, b = tally()
        with a, b:
            verify = self.client.post(f"/api/gst-tally/import-batches/{self.batch.id}/tally-license/verify/", {}, format="json", **new_h)
            gate = self.client.post(f"/api/gst-tally/import-batches/{self.batch.id}/license/pre-import-check/", {}, format="json", **new_h)
        self.assertEqual(verify.status_code, 200, verify.data)
        self.assertTrue(verify.data["ready"])
        self.assertEqual((gate.status_code, gate.data["ready"]), (200, True))

    def test_local_accounts_keep_the_ownership_restriction(self):
        other = get_user_model().objects.create_user("plain_user", password="x")
        a, b = tally()
        with a, b:
            result = pre_import_security_check(self.batch, other, device_fingerprint="pc", device_name="pc")
        self.assertIn("LICENSE_ASSIGNED_TO_ANOTHER_ACCOUNT", [e["code"] for e in result["errors"]])
        self.legacy_license.refresh_from_db()
        self.assertEqual(self.legacy_license.customer_id, self.legacy.id)
