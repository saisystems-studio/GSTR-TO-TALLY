from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from gst_tally.models import CompanyDetails, LicensedDevice, ProductLicense
from gst_tally.services.system_info import collect_system_configuration
from gst_tally.views import MyProfileView


class SystemInformationTests(SimpleTestCase):
    def test_collects_each_machine_value_from_its_local_probe(self):
        probes = {
            "device_name": lambda: "DESKTOP-ABC123",
            "processor": lambda: "Intel Core i5-1135G7",
            "ram": lambda: "16 GB",
            "total_storage": lambda: "512 GB",
            "available_storage": lambda: "268 GB",
            "operating_system": lambda: "Windows 11 Pro",
            "system_type": lambda: "64-bit",
            "app_version": lambda: "1.0.0",
        }

        self.assertEqual(collect_system_configuration(probes), {
            "device_name": "DESKTOP-ABC123",
            "processor": "Intel Core i5-1135G7",
            "ram": "16 GB",
            "total_storage": "512 GB",
            "available_storage": "268 GB",
            "operating_system": "Windows 11 Pro",
            "system_type": "64-bit",
            "app_version": "1.0.0",
        })

    def test_one_failed_probe_does_not_hide_the_other_machine_values(self):
        def unavailable():
            raise OSError("not readable")

        result = collect_system_configuration({
            "device_name": lambda: "OFFICE-PC",
            "processor": unavailable,
        })

        self.assertEqual(result["device_name"], "OFFICE-PC")
        self.assertEqual(result["processor"], "Not Available")
        self.assertEqual(result["ram"], "Not Available")


class ProfileDeviceDetailsApiTests(TestCase):
    @patch("gst_tally.views.collect_system_configuration")
    def test_profile_returns_verified_company_and_machine_configuration_without_tally_license(self, system_info):
        system_info.return_value = {"device_name": "DESKTOP-ABC123", "processor": "Intel CPU"}
        user = get_user_model().objects.create_user(username="profile-user", password="x")
        license_obj = ProductLicense.objects.create(
            customer=user,
            activation_key_hash="profile-key-hash",
            licensed_gstin="33AFHPM6103Q1Z8",
            licensed_tally_serial="735149529",
            status=ProductLicense.ACTIVE,
        )
        CompanyDetails.objects.create(
            company_name="GSTRCOMPANY",
            gstin="33AFHPM6103Q1Z8",
            state="Tamil Nadu",
            financial_year="2025-26",
            tally_serial_number="735149529",
            company_verified=True,
        )
        LicensedDevice.objects.create(
            license=license_obj,
            device_fingerprint="DEV-123",
            device_name="DESKTOP-ABC123",
            status=LicensedDevice.ACTIVE,
            first_seen=timezone.now(),
            last_seen=timezone.now(),
        )
        request = APIRequestFactory().get("/api/gst-tally/me/profile/")
        force_authenticate(request, user=user)

        response = MyProfileView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["company"]["state_code"], "33")
        self.assertTrue(response.data["company"]["verified"])
        self.assertEqual(response.data["system_configuration"]["device_name"], "DESKTOP-ABC123")
        self.assertEqual(
            set(response.data["device_authentication"]),
            {"device_name", "device_id", "first_seen", "last_seen", "status"},
        )
        self.assertEqual(response.data["device_authentication"]["device_id"], "DEV-123")
        self.assertEqual(response.data["device_authentication"]["status"], "AUTHORIZED")
        self.assertNotIn("tally_license", response.data)

    @patch("gst_tally.views.collect_system_configuration")
    def test_unauthorized_device_uses_same_payload_and_status(self, system_info):
        system_info.return_value = {"device_name": "DESKTOP-ABC123"}
        user = get_user_model().objects.create_user(username="unauthorized-profile-user", password="test-password")
        license_obj = ProductLicense.objects.create(
            customer=user,
            activation_key_hash="unauthorized-profile-key-hash",
            licensed_gstin="33AFHPM6103Q1Z8",
            licensed_tally_serial="735149529",
            status=ProductLicense.ACTIVE,
        )
        LicensedDevice.objects.create(
            license=license_obj,
            device_fingerprint="DEV-AUTHORIZED",
            status=LicensedDevice.ACTIVE,
        )
        request = APIRequestFactory().get(
            "/api/gst-tally/me/profile/",
            HTTP_X_DEVICE_FINGERPRINT="DEV-UNAUTHORIZED",
        )
        force_authenticate(request, user=user)

        response = MyProfileView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data["device_authentication"]),
            {"device_name", "device_id", "first_seen", "last_seen", "status"},
        )
        self.assertEqual(response.data["device_authentication"]["device_id"], "DEV-UNAUTHORIZED")
        self.assertEqual(response.data["device_authentication"]["status"], "NOT_AUTHORIZED")
