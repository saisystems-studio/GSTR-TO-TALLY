"""Tally license identity binding/comparison -- the part of the license
verification flow that does not depend on a live TallyPrime instance (the
XML/TDL field extraction in tally/license_reader.py is a separate, explicitly
unverified concern -- see its module docstring).
"""
from unittest.mock import patch

from django.test import TestCase

from gst_tally.models import CompanyDetails, GSTImportBatch, TallyCompanyMapping
from gst_tally.services.tally_license import verify_batch_license

COMPANY_GSTIN = "33AFHPM6103Q1Z8"
READING = {"license_available": True, "serial_number": "123456789", "edition": "Gold",
           "tally_software_services": "Active", "license_administrator": "admin@example.com",
           "license_verified": None, "license_error": None, "message": ""}


class TallyLicenseBindingTests(TestCase):
    def setUp(self):
        self.batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin=COMPANY_GSTIN)

    def test_first_verification_binds_the_license_identity(self):
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=READING):
            result = verify_batch_license(self.batch)
        self.assertTrue(result["license_verified"])
        self.assertEqual(result["license_error"], "")
        self.assertEqual(result["message"], "License verified successfully.")
        self.assertEqual(result["edition"], "Gold")
        self.assertEqual(result["tally_software_services"], "Active")
        mapping = TallyCompanyMapping.objects.get(gstin=COMPANY_GSTIN)
        self.assertEqual(mapping.license_serial, "123456789")
        self.assertEqual(mapping.license_administrator, "admin@example.com")

    def test_same_license_pair_on_a_later_import_is_verified(self):
        TallyCompanyMapping.objects.create(gstin=COMPANY_GSTIN, tally_company_name="ABC TRADERS",
                                           license_serial="123456789", license_administrator="admin@example.com")
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=READING):
            result = verify_batch_license(self.batch)
        self.assertTrue(result["license_verified"])
        self.assertEqual(result["license_error"], "")
        self.assertEqual(result["message"], "License verified successfully.")

    def test_different_license_pair_is_blocked_as_a_mismatch(self):
        TallyCompanyMapping.objects.create(gstin=COMPANY_GSTIN, tally_company_name="ABC TRADERS",
                                           license_serial="987654321", license_administrator="other@example.com")
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=READING):
            result = verify_batch_license(self.batch)
        self.assertFalse(result["license_verified"])
        self.assertEqual(result["license_error"], "LICENSE_IDENTITY_MISMATCH")
        self.assertEqual(result["expected_serial"], "987654321")
        self.assertEqual(result["expected_administrator"], "other@example.com")
        # A blocked mismatch must never overwrite the originally bound identity.
        mapping = TallyCompanyMapping.objects.get(gstin=COMPANY_GSTIN)
        self.assertEqual(mapping.license_serial, "987654321")

    def test_license_unavailable_is_reported_honestly_not_faked(self):
        unavailable = {"license_available": False, "serial_number": "", "edition": "",
                       "tally_software_services": "", "license_administrator": "",
                       "license_verified": False, "license_error": "TALLY_LICENSE_DATA_UNAVAILABLE",
                       "message": "Tally did not return a license serial number for the active instance."}
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=unavailable):
            result = verify_batch_license(self.batch)
        self.assertFalse(result["license_available"])
        self.assertFalse(result["license_verified"])
        self.assertEqual(result["license_error"], "TALLY_LICENSE_DATA_UNAVAILABLE")
        self.assertFalse(TallyCompanyMapping.objects.filter(gstin=COMPANY_GSTIN).exists())

    def test_missing_administrator_blocks_verification_but_keeps_the_fetched_fields(self):
        partial = {**READING, "license_administrator": ""}
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=partial):
            result = verify_batch_license(self.batch)
        self.assertTrue(result["license_available"])
        self.assertFalse(result["license_verified"])
        self.assertEqual(result["license_error"], "TALLY_LICENSE_ADMINISTRATOR_UNAVAILABLE")
        self.assertEqual(result["message"], "Tally License Administrator could not be read from the active Tally instance.")
        # Serial/edition/TSS that Tally DID return must still be visible, not blanked out.
        self.assertEqual(result["serial_number"], "123456789")
        self.assertEqual(result["edition"], "Gold")
        self.assertEqual(result["tally_software_services"], "Active")
        self.assertFalse(TallyCompanyMapping.objects.filter(gstin=COMPANY_GSTIN).exists())

    def test_missing_edition_blocks_verification_but_keeps_the_fetched_fields(self):
        partial = {**READING, "edition": ""}
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=partial):
            result = verify_batch_license(self.batch)
        self.assertTrue(result["license_available"])
        self.assertFalse(result["license_verified"])
        self.assertEqual(result["license_error"], "TALLY_LICENSE_EDITION_UNAVAILABLE")
        self.assertEqual(result["message"], "Tally license edition could not be read from the active Tally instance.")
        self.assertEqual(result["serial_number"], "123456789")
        self.assertEqual(result["license_administrator"], "admin@example.com")
        self.assertFalse(TallyCompanyMapping.objects.filter(gstin=COMPANY_GSTIN).exists())

    def test_missing_tss_blocks_verification_but_keeps_the_fetched_fields(self):
        partial = {**READING, "tally_software_services": ""}
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=partial):
            result = verify_batch_license(self.batch)
        self.assertTrue(result["license_available"])
        self.assertFalse(result["license_verified"])
        self.assertEqual(result["license_error"], "TALLY_LICENSE_TSS_UNAVAILABLE")
        self.assertEqual(result["message"], "Tally Software Services status could not be read from the active Tally instance.")
        self.assertEqual(result["serial_number"], "123456789")
        self.assertEqual(result["edition"], "Gold")
        self.assertEqual(result["license_administrator"], "admin@example.com")
        self.assertFalse(TallyCompanyMapping.objects.filter(gstin=COMPANY_GSTIN).exists())


class LicenseCompanyGstinResolutionTests(TestCase):
    """Regression coverage for the COMPANY_GSTIN_REQUIRED bug: license
    verification must find the GSTIN Company Verification already resolved,
    without the frontend ever having to send it again."""

    def test_falls_back_to_the_companydetails_record_verified_for_this_batch(self):
        # batch.company_gstin is blank -- the only thing this batch knows is
        # which Tally company name Company Verification succeeded against
        # (exactly what services/company_verification.py persists).
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="", company_details={"selected_tally_company": "Vasantham Agencies"})
        CompanyDetails.objects.create(company_name="VASANTHAM AGENCIES", gstin="33AJJPD4912E1ZQ",
                                      company_verified=True, tally_connected=True)
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=READING):
            result = verify_batch_license(batch)
        self.assertTrue(result["license_verified"])
        self.assertEqual(result["license_error"], "")
        self.assertTrue(TallyCompanyMapping.objects.filter(gstin="33AJJPD4912E1ZQ").exists())

    def test_falls_back_to_batch_company_details_gstin(self):
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="", company_details={"gstin": "33ajjpd4912e1zq"})
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=READING):
            result = verify_batch_license(batch)
        self.assertTrue(result["license_verified"])
        self.assertTrue(TallyCompanyMapping.objects.filter(gstin="33AJJPD4912E1ZQ").exists())

    def test_still_reports_company_gstin_required_when_genuinely_unavailable_everywhere(self):
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B", company_gstin="")
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=READING):
            result = verify_batch_license(batch)
        self.assertFalse(result["license_verified"])
        self.assertEqual(result["license_error"], "COMPANY_GSTIN_REQUIRED")

    def test_exact_reported_scenario_is_verified_true(self):
        # company_verified/tally_connected/company_details_saved were all
        # already true from Company Verification; license_available/serial/
        # edition/tss/administrator are all present -- only the GSTIN lookup
        # was broken.
        batch = GSTImportBatch.objects.create(
            file_name="gstr2b.xlsx", file_type="EXCEL", gst_return_type="GSTR2B",
            company_gstin="", company_details={"selected_tally_company": "Vasantham Agencies"})
        CompanyDetails.objects.create(company_name="VASANTHAM AGENCIES", gstin="33AJJPD4912E1ZQ",
                                      company_verified=True, tally_connected=True)
        reading = {"license_available": True, "serial_number": "735149529", "edition": "Gold",
                   "tally_software_services": "Active", "license_administrator": "saiassociatesmdu19@gmail.com",
                   "license_verified": None, "license_error": None, "message": ""}
        with patch("gst_tally.services.tally_license.read_tally_license", return_value=reading):
            result = verify_batch_license(batch)
        self.assertEqual(result["license_verified"], True)
        self.assertEqual(result["license_error"], "")
        self.assertEqual(result["serial_number"], "735149529")
        self.assertEqual(result["edition"], "Gold")
        self.assertEqual(result["tally_software_services"], "Active")
        self.assertEqual(result["license_administrator"], "saiassociatesmdu19@gmail.com")
