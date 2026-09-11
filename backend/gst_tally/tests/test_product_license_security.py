from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from gst_tally.auth_service import secret_hash
from gst_tally.models import (
    DeviceActivationRequest,
    GSTImportBatch,
    LicensedDevice,
    LicenseAuditLog,
    ProductLicense,
    TallyCompanyMapping,
    TallyImportJob,
)
from gst_tally.services.product_license import (
    pre_import_security_check,
    verify_license_snapshot,
)
from subscriptions.models import Subscription
from superadmin.models import CustomerProfile, SuperAdminProfile


User = get_user_model()


def make_user(username="arun", email="arun@example.com"):
    user = User.objects.create_user(username=username, email=email, password="x")
    Subscription.objects.create(
        user=user,
        plan="Professional",
        purchase_date=timezone.localdate(),
        is_activated=True,
        activation_date=timezone.localdate(),
        expiry_date=timezone.localdate() + timedelta(days=365),
        subscription_status=Subscription.ACTIVE,
    )
    return user


def make_license(user, *, key="G2T-PRO-2026-0001", serial="735149529", gstin="33AFHPM6103Q1Z8", allowed_devices=1, status="ACTIVE"):
    return ProductLicense.objects.create(
        customer=user,
        activation_key_hash=secret_hash(key),
        display_activation_key=key,
        display_activation_key_suffix=key[-4:],
        licensed_gstin=gstin,
        licensed_tally_serial=serial,
        plan="Professional",
        allowed_devices=allowed_devices,
        purchase_date=timezone.localdate(),
        expiry_date=timezone.localdate() + timedelta(days=365),
        status=status,
    )


class ProductLicenseSecurityTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.license = make_license(self.user)

    def test_same_gstin_different_tally_serial_is_hard_blocked_before_device_check(self):
        result = verify_license_snapshot(
            activation_key="G2T-PRO-2026-0001",
            device_fingerprint="DEV-NEW",
            device_name="OFFICE-PC-01",
            detected_tally_serial="845621773",
            current_company_gstin="33AFHPM6103Q1Z8",
            source="activation",
        )

        self.assertEqual(result["verification_result"], "TALLY_SERIAL_MISMATCH")
        self.assertFalse(result["ready"])
        self.assertEqual(result["registered_tally_serial"], "735149529")
        self.assertEqual(result["detected_tally_serial"], "845621773")
        self.assertFalse(LicensedDevice.objects.filter(license=self.license, device_fingerprint="DEV-NEW").exists())
        self.assertTrue(LicenseAuditLog.objects.filter(license=self.license, event_type="TALLY_SERIAL_MISMATCH").exists())

    def test_same_tally_serial_different_gstin_blocks_current_company(self):
        result = verify_license_snapshot(
            activation_key="G2T-PRO-2026-0001",
            device_fingerprint="DEV-81F4A2C9",
            device_name="OFFICE-PC-01",
            detected_tally_serial="735149529",
            current_company_gstin="33ABCDE1234F1Z5",
            source="activation",
        )

        self.assertEqual(result["verification_result"], "COMPANY_GSTIN_MISMATCH")
        self.assertFalse(result["ready"])
        self.assertFalse(LicensedDevice.objects.filter(license=self.license).exists())
        self.assertTrue(LicenseAuditLog.objects.filter(license=self.license, event_type="GSTIN_MISMATCH").exists())

    def test_matching_serial_and_gstin_registers_first_device_and_verifies_license(self):
        result = verify_license_snapshot(
            activation_key="G2T-PRO-2026-0001",
            device_fingerprint="DEV-81F4A2C9",
            device_name="OFFICE-PC-01",
            windows_version="Windows 11",
            app_version="1.0.0",
            detected_tally_serial="735149529",
            tally_edition="Gold",
            tss_status="Active",
            license_administrator="admin@example.com",
            current_company_name="SRI MAHALAKSHMI TRADERS",
            current_company_gstin="33AFHPM6103Q1Z8",
            state="Tamil Nadu",
            financial_year="2026-27",
            source="activation",
        )

        self.assertEqual(result["verification_result"], "LICENSE_VERIFIED")
        self.assertTrue(result["ready"])
        device = LicensedDevice.objects.get(license=self.license, device_fingerprint="DEV-81F4A2C9")
        self.assertEqual(device.status, LicensedDevice.ACTIVE)
        self.assertEqual(device.device_name, "OFFICE-PC-01")
        self.assertEqual(device.windows_version, "Windows 11")
        self.assertEqual(device.app_version, "1.0.0")
        self.assertTrue(LicenseAuditLog.objects.filter(license=self.license, event_type="DEVICE_REGISTERED").exists())

    def test_new_device_when_slot_full_creates_pending_request_not_active_device(self):
        LicensedDevice.objects.create(
            license=self.license,
            device_fingerprint="DEV-OLD",
            device_name="OFFICE-PC-01",
            status=LicensedDevice.ACTIVE,
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )

        result = verify_license_snapshot(
            activation_key="G2T-PRO-2026-0001",
            device_fingerprint="DEV-NEW",
            device_name="LAPTOP-02",
            detected_tally_serial="735149529",
            current_company_gstin="33AFHPM6103Q1Z8",
            source="activation",
        )

        self.assertEqual(result["verification_result"], "DEVICE_LIMIT_REACHED")
        self.assertFalse(result["ready"])
        self.assertFalse(LicensedDevice.objects.filter(license=self.license, device_fingerprint="DEV-NEW", status=LicensedDevice.ACTIVE).exists())
        request = DeviceActivationRequest.objects.get(license=self.license, requested_device_fingerprint="DEV-NEW")
        self.assertEqual(request.status, DeviceActivationRequest.PENDING)
        self.assertEqual(request.old_device.device_fingerprint, "DEV-OLD")

    def test_existing_device_updates_last_seen_and_verifies(self):
        old_time = timezone.now() - timedelta(days=1)
        device = LicensedDevice.objects.create(
            license=self.license,
            device_fingerprint="DEV-81F4A2C9",
            device_name="OFFICE-PC-01",
            status=LicensedDevice.ACTIVE,
            first_seen=old_time,
            last_seen=old_time,
        )

        result = verify_license_snapshot(
            license_id=self.license.id,
            user=self.user,
            device_fingerprint="DEV-81F4A2C9",
            device_name="OFFICE-PC-01",
            detected_tally_serial="735149529",
            current_company_gstin="33AFHPM6103Q1Z8",
            source="startup",
        )

        self.assertEqual(result["verification_result"], "LICENSE_VERIFIED")
        device.refresh_from_db()
        self.assertGreater(device.last_seen, old_time)


class ProductLicensePreImportTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.license = make_license(self.user)
        LicensedDevice.objects.create(
            license=self.license,
            device_fingerprint="DEV-81F4A2C9",
            device_name="OFFICE-PC-01",
            status=LicensedDevice.ACTIVE,
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx",
            file_type="EXCEL",
            gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
        )

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_reads_current_tally_and_blocks_switched_serial(self, mock_connection, mock_license):
        mock_connection.return_value = {
            "read_connected": True,
            "can_import": True,
            "company_name": "SRI MAHALAKSHMI TRADERS",
            "company_gstin": "33AFHPM6103Q1Z8",
        }
        mock_license.return_value = {
            "license_available": True,
            "serial_number": "845621773",
            "edition": "Gold",
            "tally_software_services": "Active",
            "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["verification_result"], "TALLY_SERIAL_MISMATCH")
        self.assertFalse(result["ready"])
        self.assertEqual(result["registered_tally_serial"], "735149529")
        self.assertEqual(result["detected_tally_serial"], "845621773")
        self.assertFalse(result["ready_for_master_preparation"])
        self.assertTrue(LicenseAuditLog.objects.filter(license=self.license, event_type="PRE_IMPORT_LICENSE_FAILED").exists())

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_license_unavailable_keeps_connection_company_separate_and_not_ready(self, mock_connection, mock_license):
        mock_connection.return_value = {
            "read_connected": True,
            "can_import": True,
            "company_name": "SRI MAHALAKSHMI TRADERS,",
            "company_gstin": "33AFHPM6103Q1Z8",
        }
        mock_license.return_value = {
            "license_available": False,
            "license_verified": False,
            "serial_number": "",
            "edition": "",
            "tally_software_services": "",
            "license_administrator": "",
            "license_error": "TALLY_LICENSE_DATA_UNAVAILABLE",
            "license_error_detail": "SerialNumber returned an empty value.",
            "message": "Connected to Tally, but license information could not be read.",
        }

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertTrue(result["tally_connected"])
        self.assertTrue(result["company_verified"])
        self.assertFalse(result["license_available"])
        self.assertFalse(result["license_verified"])
        self.assertFalse(result["ready_for_master_preparation"])
        self.assertEqual(result["license_error"], "TALLY_LICENSE_DATA_UNAVAILABLE")
        self.assertEqual(result["license_error_detail"], "SerialNumber returned an empty value.")
        self.assertEqual(result["serial_number"], "")
        self.assertEqual(result["registered_tally_serial"], "735149529")

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_after_db_reset_recreates_the_license_and_verifies(self, mock_connection, mock_license):
        # The literal DB-reset scenario: the ProductLicense row is gone, but
        # the live Tally installation is still genuinely licensed and the
        # user still holds a real (active) Subscription -- the app must
        # self-heal rather than permanently report PRODUCT_LICENSE_NOT_CONFIGURED.
        ProductLicense.objects.filter(pk=self.license.pk).delete()
        mock_connection.return_value = {
            "read_connected": True,
            "can_import": True,
            "company_name": "SRI MAHALAKSHMI TRADERS,",
            "company_gstin": "33AFHPM6103Q1Z8",
        }
        mock_license.return_value = {
            "license_available": True,
            "serial_number": "735149529",
            "edition": "Gold",
            "tally_software_services": "Active",
            "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["verification_result"], "LICENSE_VERIFIED")
        self.assertTrue(result["ready"])
        self.assertTrue(result["ready_for_master_preparation"])
        recovered = ProductLicense.objects.get(customer=self.user, licensed_tally_serial="735149529")
        self.assertEqual(result["license_id"], recovered.id)
        self.assertEqual(recovered.status, ProductLicense.ACTIVE)
        self.assertTrue(LicensedDevice.objects.filter(license=recovered, device_fingerprint="DEV-81F4A2C9", status=LicensedDevice.ACTIVE).exists())

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_after_db_reset_without_active_subscription_stays_not_configured(self, mock_connection, mock_license):
        # Same DB-reset scenario, but the account has no real commercial
        # entitlement (subscription suspended) -- recovery must refuse to
        # fabricate a license rather than silently granting one.
        ProductLicense.objects.filter(pk=self.license.pk).delete()
        self.user.subscription.suspend()
        mock_connection.return_value = {
            "read_connected": True,
            "can_import": True,
            "company_name": "SRI MAHALAKSHMI TRADERS,",
            "company_gstin": "33AFHPM6103Q1Z8",
        }
        mock_license.return_value = {
            "license_available": True,
            "serial_number": "735149529",
            "edition": "Gold",
            "tally_software_services": "Active",
            "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["license_error"], "PRODUCT_LICENSE_NOT_CONFIGURED")
        self.assertFalse(result["ready_for_master_preparation"])
        self.assertEqual(result["serial_number"], "735149529")
        self.assertFalse(ProductLicense.objects.filter(customer=self.user).exists())

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_with_empty_registered_tally_serial_reports_not_configured(self, mock_connection, mock_license):
        self.license.licensed_tally_serial = ""
        self.license.save(update_fields=["licensed_tally_serial", "updated_at"])
        mock_connection.return_value = {
            "read_connected": True,
            "can_import": True,
            "company_name": "SRI MAHALAKSHMI TRADERS,",
            "company_gstin": "33AFHPM6103Q1Z8",
        }
        mock_license.return_value = {
            "license_available": True,
            "serial_number": "77611",
            "edition": "Silver",
            "tally_software_services": "Active",
            "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["verification_result"], "PRODUCT_LICENSE_NOT_CONFIGURED")
        self.assertEqual(result["license_error"], "PRODUCT_LICENSE_NOT_CONFIGURED")
        self.assertEqual(result["registered_tally_serial"], "")
        self.assertEqual(result["detected_tally_serial"], "77611")
        self.assertFalse(result["license_verified"])
        self.assertFalse(result["ready_for_master_preparation"])
        self.license.refresh_from_db()
        self.assertEqual(self.license.licensed_tally_serial, "")

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_configured_tally_serial_matching_detected_serial_verifies(self, mock_connection, mock_license):
        self.license.licensed_tally_serial = "77611"
        self.license.save(update_fields=["licensed_tally_serial", "updated_at"])
        mock_connection.return_value = {
            "read_connected": True,
            "can_import": True,
            "company_name": "SRI MAHALAKSHMI TRADERS,",
            "company_gstin": "33AFHPM6103Q1Z8",
            "company_state": "Tamil Nadu",
            "financial_year": "2026-27",
        }
        mock_license.return_value = {
            "license_available": True,
            "serial_number": "77611",
            "edition": "Silver",
            "tally_software_services": "Active",
            "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["verification_result"], "LICENSE_VERIFIED")
        self.assertEqual(result["registered_tally_serial"], "77611")
        self.assertEqual(result["detected_tally_serial"], "77611")
        self.assertTrue(result["license_verified"])
        self.assertTrue(result["ready_for_master_preparation"])

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_same_serial_second_company_gstin_is_authorized(self, mock_connection, mock_license):
        # One valid Tally installation may legitimately serve more than one
        # company: a second, different GSTIN under the SAME already-used
        # serial must be accepted, not treated as a mismatch.
        self.license.licensed_tally_serial = "77611"
        self.license.save(update_fields=["licensed_tally_serial", "updated_at"])
        second_batch = GSTImportBatch.objects.create(
            file_name="gstr2b_companyB.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="29XYZDE5678G1Z2",
        )
        mock_connection.return_value = {
            "read_connected": True, "can_import": True,
            "company_name": "ABC ENTERPRISES", "company_gstin": "29XYZDE5678G1Z2",
        }
        mock_license.return_value = {
            "license_available": True, "serial_number": "77611", "edition": "Silver",
            "tally_software_services": "Active", "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(second_batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["verification_result"], "LICENSE_VERIFIED")
        self.assertTrue(result["ready_for_master_preparation"])
        self.assertTrue(TallyCompanyMapping.objects.filter(gstin="29XYZDE5678G1Z2", license_serial="77611").exists())

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_gstin_already_bound_to_a_different_serial_is_rejected(self, mock_connection, mock_license):
        # A GSTIN already licensed under one Tally serial must never be
        # silently reused under a different, unlicensed serial.
        TallyCompanyMapping.objects.create(gstin="29XYZDE5678G1Z2", license_serial="111111111",
                                           tally_company_name="ABC ENTERPRISES")
        self.license.licensed_tally_serial = "77611"
        self.license.save(update_fields=["licensed_tally_serial", "updated_at"])
        second_batch = GSTImportBatch.objects.create(
            file_name="gstr2b_companyB.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="29XYZDE5678G1Z2",
        )
        mock_connection.return_value = {
            "read_connected": True, "can_import": True,
            "company_name": "ABC ENTERPRISES", "company_gstin": "29XYZDE5678G1Z2",
        }
        mock_license.return_value = {
            "license_available": True, "serial_number": "77611", "edition": "Silver",
            "tally_software_services": "Active", "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(second_batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["license_error"], "GSTIN_LICENSE_SERIAL_MISMATCH")
        self.assertFalse(result["ready_for_master_preparation"])

    @patch("gst_tally.services.product_license.read_tally_license")
    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_cosmetic_company_name_change_never_blocks_verification(self, mock_connection, mock_license):
        # Company NAME is display-only -- once the GSTIN already matches, a
        # trailing comma/branch suffix/year appended must never fail
        # verification, though the display name is still updated.
        self.license.licensed_tally_serial = "77611"
        self.license.save(update_fields=["licensed_tally_serial", "updated_at"])
        TallyCompanyMapping.objects.create(gstin="33AFHPM6103Q1Z8", license_serial="77611",
                                           tally_company_name="SRI MAHALAKSHMI TRADERS")
        mock_connection.return_value = {
            "read_connected": True, "can_import": True,
            "company_name": "SRI MAHALAKSHMI TRADERS - HO", "company_gstin": "33AFHPM6103Q1Z8",
        }
        mock_license.return_value = {
            "license_available": True, "serial_number": "77611", "edition": "Silver",
            "tally_software_services": "Active", "license_administrator": "admin@example.com",
        }

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["verification_result"], "LICENSE_VERIFIED")
        mapping = TallyCompanyMapping.objects.get(gstin="33AFHPM6103Q1Z8")
        self.assertEqual(mapping.tally_company_name, "SRI MAHALAKSHMI TRADERS - HO")

    @patch("gst_tally.views.run_job_in_background")
    @patch("gst_tally.views.pre_import_security_check")
    def test_step6_does_not_start_job_when_pre_import_license_check_fails(self, mock_check, mock_run):
        mock_check.return_value = {
            "ready": False,
            "verification_result": "TALLY_SERIAL_MISMATCH",
            "registered_tally_serial": "735149529",
            "detected_tally_serial": "845621773",
            "licensed_gstin": "33AFHPM6103Q1Z8",
            "current_company_gstin": "33AFHPM6103Q1Z8",
            "message": "No vouchers were sent to Tally.",
        }
        client = APIClient()
        client.force_authenticate(user=self.user)

        response = client.post(f"/api/gst-tally/import-batches/{self.batch.id}/tally-import/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "PRE_IMPORT_LICENSE_FAILED")
        self.assertEqual(response.data["verification_result"], "TALLY_SERIAL_MISMATCH")
        self.assertEqual(response.data["message"], "No vouchers were sent to Tally.")
        self.assertFalse(TallyImportJob.objects.filter(batch=self.batch).exists())
        mock_run.assert_not_called()

    @patch("gst_tally.views.resume_job")
    @patch("gst_tally.views.pre_import_security_check")
    def test_paused_import_resume_does_not_start_when_pre_import_license_check_fails(self, mock_check, mock_resume):
        job = TallyImportJob.objects.create(batch=self.batch, status="PAUSED")
        mock_check.return_value = {
            "ready": False,
            "verification_result": "TALLY_SERIAL_MISMATCH",
            "registered_tally_serial": "735149529",
            "detected_tally_serial": "845621773",
            "message": "No vouchers were sent to Tally.",
        }
        client = APIClient()
        client.force_authenticate(user=self.user)

        response = client.post(f"/api/gst-tally/tally-import/jobs/{job.job_id}/resume/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "PRE_IMPORT_LICENSE_FAILED")
        mock_resume.assert_not_called()


class ProductLicensePreImportTransactionTests(TransactionTestCase):
    def setUp(self):
        self.user = make_user(username="atomicuser", email="atomic@example.com")
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx",
            file_type="EXCEL",
            gst_return_type="GSTR2B",
            company_gstin="33AFHPM6103Q1Z8",
        )

    @patch("gst_tally.services.product_license.step3_connection_check")
    def test_pre_import_security_check_enters_atomic_block_before_locking_license_rows(self, mock_connection):
        def disconnected_response(*args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            return {"read_connected": False, "can_import": False}

        mock_connection.side_effect = disconnected_response

        result = pre_import_security_check(self.batch, self.user, device_fingerprint="DEV-81F4A2C9")

        self.assertEqual(result["verification_result"], "TALLY_NOT_CONNECTED")


class ProductLicenseActivationApiTests(TestCase):
    def setUp(self):
        self.user = make_user(username="apiuser", email="api@example.com")
        self.license = make_license(self.user, key="G2T-PRO-2026-API1")
        self.client = APIClient()

    def test_first_exe_activation_uses_activation_key_and_returns_tokens_only_after_backend_verification(self):
        response = self.client.post("/api/gst-tally/license/activate/", {
            "activation_key": "G2T-PRO-2026-API1",
            "device_fingerprint": "DEV-API",
            "device_name": "OFFICE-PC-01",
            "windows_version": "Windows 11",
            "app_version": "1.0.0",
            "detected_tally_serial": "735149529",
            "tally_edition": "Gold",
            "tss_status": "Active",
            "license_administrator": "admin@example.com",
            "current_company_name": "SRI MAHALAKSHMI TRADERS,",
            "current_company_gstin": "33AFHPM6103Q1Z8",
            "state": "Tamil Nadu",
            "financial_year": "2026-27",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["verification_result"], "LICENSE_VERIFIED")
        self.assertTrue(response.data["ready"])
        self.assertIn("access", response.data)
        self.assertEqual(response.data["user"]["email"], "api@example.com")

    def test_activation_api_rejects_same_gstin_with_wrong_tally_serial(self):
        response = self.client.post("/api/gst-tally/license/activate/", {
            "activation_key": "G2T-PRO-2026-API1",
            "device_fingerprint": "DEV-API",
            "device_name": "OFFICE-PC-01",
            "detected_tally_serial": "845621773",
            "current_company_gstin": "33AFHPM6103Q1Z8",
        }, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["verification_result"], "TALLY_SERIAL_MISMATCH")
        self.assertNotIn("access", response.data)


class SuperAdminProductLicenseApiTests(TestCase):
    def setUp(self):
        self.customer = make_user(username="customer", email="customer@example.com")
        self.license = make_license(self.customer)
        self.old_device = LicensedDevice.objects.create(
            license=self.license,
            device_fingerprint="DEV-OLD",
            device_name="OFFICE-PC-01",
            status=LicensedDevice.ACTIVE,
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        self.request = DeviceActivationRequest.objects.create(
            license=self.license,
            old_device=self.old_device,
            requested_device_fingerprint="DEV-NEW",
            requested_device_name="LAPTOP-02",
            detected_tally_serial="735149529",
            current_company_gstin="33AFHPM6103Q1Z8",
        )
        self.admin = User.objects.create_user(username="super", email="super@example.com", password="x")
        SuperAdminProfile.objects.create(user=self.admin, display_name="Super Admin", must_change_password=False)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_superadmin_dashboard_allows_default_password_flag(self):
        flagged_admin = User.objects.create_user(username="bootstrap", email="bootstrap@example.com", password="x")
        SuperAdminProfile.objects.create(user=flagged_admin, display_name="Bootstrap Admin", must_change_password=True)
        client = APIClient()
        client.force_authenticate(user=flagged_admin)

        response = client.get("/api/superadmin/dashboard/")

        self.assertEqual(response.status_code, 200)

    def test_product_license_list_exposes_business_fields_and_computed_status(self):
        CustomerProfile.objects.create(user=self.customer, business_name="SRI MAHALAKSHMI TRADERS")
        expired = make_license(
            self.customer,
            key="G2T-PRO-2025-OLD1",
            serial="992381425",
            gstin="33XYZDE5678G1Z2",
            status=ProductLicense.ACTIVE,
        )
        expired.expiry_date = timezone.localdate() - timedelta(days=1)
        expired.save(update_fields=["expiry_date"])
        needs_setup = make_license(
            self.customer,
            key="G2T-PRO-2026-NULL",
            serial="111222333",
            gstin="33NULLD1234F1Z5",
            status=ProductLicense.ACTIVE,
        )
        needs_setup.expiry_date = None
        needs_setup.save(update_fields=["expiry_date"])

        response = self.client.get("/api/superadmin/product-licenses/")

        self.assertEqual(response.status_code, 200)
        rows = {row["licensed_tally_serial"]: row for row in response.data["results"]}
        self.assertEqual(rows["735149529"]["company"], "SRI MAHALAKSHMI TRADERS")
        self.assertEqual(rows["735149529"]["activation_key"], "G2T-PRO-2026-0001")
        self.assertIn("days_remaining", rows["735149529"])
        self.assertEqual(rows["992381425"]["computed_status"], "EXPIRED")
        self.assertEqual(rows["111222333"]["computed_status"], "NEEDS_SETUP")
        self.assertIsNone(rows["111222333"]["expiry_date"])

    def test_customer_detail_includes_license_and_device_summary(self):
        CustomerProfile.objects.create(user=self.customer, business_name="SRI MAHALAKSHMI TRADERS")

        response = self.client.get(f"/api/superadmin/customers/{self.customer.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["license_summary"]["activation_key"], "G2T-PRO-2026-0001")
        self.assertEqual(response.data["license_summary"]["registered_tally_serial"], "735149529")
        self.assertIn("expiry_date", response.data["license_summary"])
        self.assertEqual(response.data["device_summary"]["device_name"], "OFFICE-PC-01")
        self.assertEqual(response.data["device_summary"]["device_fingerprint"], "DEV-OLD")

    def test_reactivate_expired_license_requires_extension_first(self):
        self.license.expiry_date = timezone.localdate() - timedelta(days=1)
        self.license.status = ProductLicense.SUSPENDED
        self.license.save(update_fields=["expiry_date", "status"])

        response = self.client.post(f"/api/superadmin/product-licenses/{self.license.id}/reactivate/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "License expired. Extend the expiry date before reactivation.")
        self.license.refresh_from_db()
        self.assertEqual(self.license.status, ProductLicense.SUSPENDED)

    def test_superadmin_login_bootstraps_default_account_without_serializer_500(self):
        User.objects.filter(username="Superadmin").delete()
        client = APIClient()

        response = client.post("/api/superadmin/auth/login/", {
            "username": "Superadmin",
            "password": "123",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["name"], "Super Admin")
        self.assertEqual(response.data["user"]["role"], "SUPER_ADMIN")
        self.assertNotIn("username", response.data["user"])

    def test_superadmin_login_invalid_default_password_still_returns_4xx(self):
        User.objects.filter(username="Superadmin").delete()
        client = APIClient()

        response = client.post("/api/superadmin/auth/login/", {
            "username": "Superadmin",
            "password": "wrong",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Invalid username or password.")
        self.assertFalse(User.objects.filter(username="Superadmin").exists())

    def test_change_tally_serial_requires_reason_and_writes_audit(self):
        missing_reason = self.client.post(f"/api/superadmin/product-licenses/{self.license.id}/change-tally-serial/", {
            "new_serial": "845621773",
            "reason": "",
        }, format="json")
        self.assertEqual(missing_reason.status_code, 400)

        response = self.client.post(f"/api/superadmin/product-licenses/{self.license.id}/change-tally-serial/", {
            "new_serial": "845621773",
            "reason": "Customer purchased new Tally license",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.license.refresh_from_db()
        self.assertEqual(self.license.licensed_tally_serial, "845621773")
        audit = LicenseAuditLog.objects.get(license=self.license, event_type="TALLY_SERIAL_CHANGED")
        self.assertEqual(audit.old_value["licensed_tally_serial"], "735149529")
        self.assertEqual(audit.new_value["licensed_tally_serial"], "845621773")

    def test_replace_device_keeps_tally_serial_and_gstin_unchanged(self):
        response = self.client.post(f"/api/superadmin/device-requests/{self.request.id}/replace/", {
            "reason": "Old PC damaged",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.license.refresh_from_db()
        self.old_device.refresh_from_db()
        new_device = LicensedDevice.objects.get(license=self.license, device_fingerprint="DEV-NEW")
        self.assertEqual(self.old_device.status, LicensedDevice.REPLACED)
        self.assertEqual(new_device.status, LicensedDevice.ACTIVE)
        self.assertEqual(self.license.licensed_tally_serial, "735149529")
        self.assertEqual(self.license.licensed_gstin, "33AFHPM6103Q1Z8")
        self.assertTrue(LicenseAuditLog.objects.filter(license=self.license, event_type="DEVICE_REPLACED").exists())
