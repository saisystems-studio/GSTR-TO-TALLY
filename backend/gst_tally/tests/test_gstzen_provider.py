from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from gst_tally.services.gst_lookup.base import GSTLookupConfigurationError
from gst_tally.services.gst_lookup.providers import provider_class
from gst_tally.services.gst_lookup.providers.gstzen import GSTZenProvider


class GSTZenProviderTests(SimpleTestCase):
    def test_provider_is_registered(self):
        self.assertIs(provider_class("gstzen"), GSTZenProvider)

    @override_settings(GSTZEN_BASE_URL="", GSTZEN_API_ENDPOINT="", GSTZEN_API_KEY="",
                       GSTZEN_CLIENT_ID="", GSTZEN_CLIENT_SECRET="")
    def test_missing_configuration_reports_names_only(self):
        self.assertEqual(GSTZenProvider.configuration_issues(), [
            "GSTZEN_BASE_URL", "GSTZEN_API_ENDPOINT", "GSTZEN_API_KEY", "GSTZEN_OFFICIAL_API_CONTRACT",
        ])

    @override_settings(GSTZEN_BASE_URL="https://vendor.invalid", GSTZEN_API_ENDPOINT="/documented",
                       GSTZEN_API_KEY="secret", GSTZEN_CLIENT_ID="", GSTZEN_CLIENT_SECRET="")
    @patch("urllib.request.urlopen")
    def test_no_http_request_until_official_contract_is_implemented(self, opener):
        with self.assertRaises(GSTLookupConfigurationError): GSTZenProvider.from_settings()
        opener.assert_not_called()

