from django.test import SimpleTestCase, override_settings

from gst_tally.tally.license_reader import read_tally_license
from gst_tally.tally.read_requests import build_license_query_xml


class FakeTallyClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, payload):
        self.requests.append(payload)
        return self.responses.pop(0)


class TallyLicenseReaderTests(SimpleTestCase):
    def test_license_request_exports_license_info_function_for_serial_number(self):
        xml = build_license_query_xml().decode("utf-8")

        self.assertIn("<TALLYREQUEST>EXPORT</TALLYREQUEST>", xml)
        self.assertIn("<TYPE>FUNCTION</TYPE>", xml)
        self.assertIn("<ID>$$LicenseInfo</ID>", xml)
        self.assertIn("<PARAM>SerialNumber</PARAM>", xml)
        self.assertNotIn("<TYPE>Company</TYPE>", xml)
        self.assertNotIn("<FETCH>SERIALNUMBER", xml)

    @override_settings(TALLY_DRY_RUN=False)
    def test_reads_serial_number_from_function_result(self):
        client = FakeTallyClient([
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Long\">123456789</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">Yes</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">Yes</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"String\">admin@example.com</RESULT></DATA></BODY></ENVELOPE>",
        ])

        result = read_tally_license(client)

        self.assertTrue(result["license_available"])
        self.assertEqual(result["serial_number"], "123456789")
        self.assertEqual(result["edition"], "Silver")
        self.assertEqual(result["tally_software_services"], "Active")
        self.assertEqual(result["license_administrator"], "admin@example.com")
        self.assertIsNone(result["license_verified"])
        self.assertIsNone(result["license_error"])

    @override_settings(TALLY_DRY_RUN=False)
    def test_empty_serial_result_keeps_license_unavailable(self):
        client = FakeTallyClient([
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Long\"></RESULT></DATA></BODY></ENVELOPE>",
        ])

        result = read_tally_license(client)

        self.assertFalse(result["license_available"])
        self.assertFalse(result["license_verified"])
        self.assertEqual(result["license_error"], "TALLY_LICENSE_DATA_UNAVAILABLE")
        self.assertEqual(result["license_error_detail"], "SerialNumber returned an empty value.")
        self.assertIn("serial number", result["message"])

    @override_settings(TALLY_DRY_RUN=False)
    def test_reads_license_values_from_named_tally_tags_when_result_is_absent(self):
        client = FakeTallyClient([
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><SERIALNUMBER>735149529</SERIALNUMBER></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><ISGOLD>Yes</ISGOLD></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><ISSILVER>No</ISSILVER></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><ISEDUCATIONALMODE>No</ISEDUCATIONALMODE></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><ISLICENSEDMODE>Yes</ISLICENSEDMODE></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><ADMINEMAILID>sai@example.com</ADMINEMAILID></DATA></BODY></ENVELOPE>",
        ])

        result = read_tally_license(client)

        self.assertTrue(result["license_available"])
        self.assertEqual(result["serial_number"], "735149529")
        self.assertEqual(result["edition"], "Gold")
        self.assertEqual(result["tally_software_services"], "Active")
        self.assertEqual(result["license_administrator"], "sai@example.com")

    @override_settings(TALLY_DRY_RUN=False)
    def test_normalizes_expired_tss_result_without_guessing_active(self):
        client = FakeTallyClient([
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Long\">123456789</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">Yes</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"String\">Expired</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"String\">admin@example.com</RESULT></DATA></BODY></ENVELOPE>",
        ])

        result = read_tally_license(client)

        self.assertEqual(result["edition"], "Gold")
        self.assertEqual(result["tally_software_services"], "Expired")

    @override_settings(TALLY_DRY_RUN=False)
    def test_empty_tss_result_remains_empty_for_service_to_block(self):
        client = FakeTallyClient([
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Long\">123456789</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">Yes</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"String\"></RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"String\">admin@example.com</RESULT></DATA></BODY></ENVELOPE>",
        ])

        result = read_tally_license(client)

        self.assertEqual(result["tally_software_services"], "")

    @override_settings(TALLY_DRY_RUN=True)
    def test_dry_run_never_blocks_the_live_license_read(self):
        """Regression test: Step 3 Product License verification must always
        read Tally's real serial number, even when TALLY_DRY_RUN=True (which
        only suppresses voucher/master writes -- see tally/service.py's
        import_batch). Before this fix, read_tally_license short-circuited
        under dry run and always reported an empty serial/license_available,
        which made Step 3 fail with a misleading PRODUCT_LICENSE_NOT_CONFIGURED
        even though a real, valid product license existed."""
        client = FakeTallyClient([
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Long\">123456789</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">Yes</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">No</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"Logical\">Yes</RESULT></DATA></BODY></ENVELOPE>",
            b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><RESULT TYPE=\"String\">admin@example.com</RESULT></DATA></BODY></ENVELOPE>",
        ])

        result = read_tally_license(client)

        self.assertTrue(client.requests, "no request was sent to Tally under dry run")
        self.assertTrue(result["license_available"])
        self.assertEqual(result["serial_number"], "123456789")
        self.assertNotEqual(result.get("license_error_detail"), "Tally dry run is enabled; no request was sent to Tally.")
