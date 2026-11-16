from threading import Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from gst_tally.models import (DeviceTransfer, GSTImportBatch, LicenseAuditLog, LicensedDevice, NexusOutboxEvent)
from gst_tally.services import device_transfer
from gst_tally.services.product_license import pre_import_security_check
from gst_tally.tests.test_product_license_security import make_license, make_user

User = get_user_model()
BASE = "/api/gst-tally/license/device-transfer/"


class Base:
    def setUp(self):
        self.user = make_user("xfer_owner", "xfer@example.com")
        self.user.set_password("correct-pass")
        self.user.save()
        self.license = make_license(self.user, serial="TEST-SERIAL", allowed_devices=1)
        self.old = LicensedDevice.objects.create(license=self.license, device_fingerprint="OLD-PC", device_name="Old Laptop")
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.user)

    def post(self, name, device="NEW-PC", **extra):
        return self.client_api.post(BASE + name + "/", {"device_fingerprint": device, "device_name": "New Laptop", **extra},
                                    format="json", HTTP_X_DEVICE_ID=device)


class DeviceTransferTests(Base, TestCase):
    def test_preview_offers_transfer_with_display_data(self):
        data = self.post("preview").data
        self.assertTrue(data["transfer_available"])
        self.assertEqual(data["previous_device_name"], "Old Laptop")
        self.assertEqual(data["current_device_name"], "New Laptop")
        self.assertEqual(data["tally_serial"], "TEST-SERIAL")

    def test_active_device_gets_no_popup(self):
        self.assertFalse(self.post("preview", device="OLD-PC").data["transfer_available"])

    def test_success_flow_revokes_old_activates_new_and_queues_nexus_event(self):
        r = self.post("confirm", password="correct-pass")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint="OLD-PC").status, LicensedDevice.REVOKED)
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint="NEW-PC").status, LicensedDevice.ACTIVE)
        self.assertEqual(LicensedDevice.objects.filter(license=self.license, status=LicensedDevice.ACTIVE).count(), 1)
        self.license.refresh_from_db()
        self.assertEqual(self.license.licensed_tally_serial, "TEST-SERIAL")
        self.assertEqual(self.license.customer_id, self.user.id)
        self.assertEqual(DeviceTransfer.objects.filter(result="SUCCESS").count(), 1)
        self.assertTrue(LicenseAuditLog.objects.filter(event_type="DEVICE_TRANSFERRED").exists())
        ev = NexusOutboxEvent.objects.get(event_type="DEVICE_TRANSFER_COMPLETED")
        self.assertEqual(ev.payload["tally_serial"], "TEST-SERIAL")
        self.assertEqual(ev.payload["old_device"]["name"], "Old Laptop")
        blob = str(ev.payload).lower()
        for secret in ("password", "activation", "key_hash"):
            self.assertNotIn(secret, blob)

    def test_cancel_changes_nothing(self):
        self.assertEqual(self.post("cancel").status_code, 200)
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint="OLD-PC").status, LicensedDevice.ACTIVE)
        self.assertFalse(LicensedDevice.objects.filter(device_fingerprint="NEW-PC").exists())
        self.assertFalse(NexusOutboxEvent.objects.exists())

    def test_wrong_password_blocks_and_is_logged(self):
        r = self.post("confirm", password="nope")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data["code"], "REAUTH_FAILED")
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint="OLD-PC").status, LicensedDevice.ACTIVE)
        self.assertEqual(DeviceTransfer.objects.filter(result="AUTH_FAILED").count(), 1)

    def test_rate_limit_after_repeated_failures(self):
        for _ in range(device_transfer.RATE_LIMIT):
            self.post("confirm", password="nope")
        r = self.post("confirm", password="correct-pass")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint="OLD-PC").status, LicensedDevice.ACTIVE)

    def test_other_account_cannot_transfer_someones_license(self):
        other = User.objects.create_user(username="xfer_other", password="pw")
        c = APIClient()
        c.force_authenticate(other)
        r = c.post(BASE + "confirm/", {"device_fingerprint": "NEW-PC", "password": "pw", "tally_serial": "TEST-SERIAL"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data["code"], "LICENSE_ASSIGNED_TO_ANOTHER_ACCOUNT")
        self.license.refresh_from_db()
        self.assertEqual(self.license.customer_id, self.user.id)
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint="OLD-PC").status, LicensedDevice.ACTIVE)

    def test_admin_revoked_device_cannot_self_transfer(self):
        LicensedDevice.objects.create(license=self.license, device_fingerprint="BAD-PC", status=LicensedDevice.REVOKED)
        self.assertFalse(self.post("preview", device="BAD-PC").data["transfer_available"])

    def test_old_laptop_blocked_after_transfer_but_data_kept(self):
        batch = GSTImportBatch.objects.create(file_name="keep.json", file_type="JSON")
        self.post("confirm", password="correct-pass")
        from rest_framework_simplejwt.tokens import AccessToken
        jwt = APIClient()
        jwt.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(self.user)))
        r = jwt.get("/api/gst-tally/batches/", HTTP_X_DEVICE_ID="OLD-PC")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "DEVICE_TRANSFERRED")
        self.assertIn("no longer authorized", r.json()["detail"])
        self.assertEqual(jwt.get("/api/gst-tally/batches/", HTTP_X_DEVICE_ID="NEW-PC").status_code, 200)
        self.assertTrue(GSTImportBatch.objects.filter(pk=batch.pk).exists())

    def test_new_laptop_then_passes_step3_and_old_fails(self):
        self.post("confirm", password="correct-pass")
        batch = GSTImportBatch.objects.create(file_name="r.json", file_type="JSON", company_gstin=self.license.licensed_gstin)

        def verify(device):
            with patch("gst_tally.services.product_license.step3_connection_check", return_value={
                "read_connected": True, "can_import": True, "company_name": "C", "company_gstin": batch.company_gstin}), \
                    patch("gst_tally.services.product_license.read_tally_license",
                          return_value={"license_available": True, "serial_number": "TEST-SERIAL"}):
                return pre_import_security_check(batch, self.user, device_fingerprint=device, device_name=device)
        self.assertTrue(verify("NEW-PC")["ready"])
        self.assertFalse(verify("OLD-PC")["ready"])

    def test_step3_exposes_transfer_offer_for_new_device(self):
        batch = GSTImportBatch.objects.create(file_name="r.json", file_type="JSON", company_gstin=self.license.licensed_gstin)
        with patch("gst_tally.services.product_license.step3_connection_check", return_value={
            "read_connected": True, "can_import": True, "company_name": "C", "company_gstin": batch.company_gstin}), \
                patch("gst_tally.services.product_license.read_tally_license",
                      return_value={"license_available": True, "serial_number": "TEST-SERIAL"}):
            result = pre_import_security_check(batch, self.user, device_fingerprint="NEW-PC", device_name="New Laptop")
        self.assertFalse(result["ready"])
        self.assertTrue(result["device_transfer"]["transfer_available"])


class OutboxTests(Base, TestCase):
    def make_event(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post("confirm", password="correct-pass")
        return NexusOutboxEvent.objects.get()

    def test_nexus_failure_does_not_roll_back_and_is_retried(self):
        with self.settings(NEXUS_ENABLED=True, NEXUS_PRODUCT_KEY="k", NEXUS_EVENT_SIGNING_SECRET="s"), \
                patch("nexus_auth.client.call", side_effect=Exception("down")):
            ev = self.make_event()
        ev.refresh_from_db()
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint="NEW-PC").status, LicensedDevice.ACTIVE)
        self.assertEqual(ev.status, "PENDING")
        self.assertEqual(ev.attempts, 1)
        NexusOutboxEvent.objects.filter(pk=ev.pk).update(next_attempt_at=ev.created_at)
        with self.settings(NEXUS_ENABLED=True, NEXUS_PRODUCT_KEY="k", NEXUS_EVENT_SIGNING_SECRET="s"), patch("nexus_auth.client.call", return_value={}) as call:
            device_transfer.deliver_due()
            device_transfer.deliver_due()  # second pass must not resend
        ev.refresh_from_db()
        self.assertEqual(ev.status, "SENT")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args[0][2]["event_id"], str(ev.event_id))

    def test_unconfigured_nexus_leaves_event_pending(self):
        with self.settings(NEXUS_ENABLED=False):
            ev = self.make_event()
        self.assertEqual(ev.status, "PENDING")


class ConcurrencyTests(TransactionTestCase):
    def test_concurrent_transfers_leave_exactly_one_active_device(self):
        user = make_user("xfer_conc", "c@example.com")
        user.set_password("pw")
        user.save()
        lic = make_license(user, serial="CONC-SERIAL", allowed_devices=1)
        LicensedDevice.objects.create(license=lic, device_fingerprint="OLD", device_name="Old")
        results = []

        def run(device):
            close_old_connections()
            try:
                results.append(device_transfer.confirm(user, device, device, "pw", serial="CONC-SERIAL")["transferred"])
            except device_transfer.TransferError as exc:
                results.append(exc.code)
            finally:
                close_old_connections()
        threads = [Thread(target=run, args=(d,)) for d in ("NEW-A", "NEW-B")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(LicensedDevice.objects.filter(license=lic, status=LicensedDevice.ACTIVE).count(), 1, results)
        # Serialized: each success revokes the previous holder, so exactly one stays ACTIVE.
        self.assertEqual(DeviceTransfer.objects.filter(result="SUCCESS").count(), results.count(True), results)
