import json

from django.test import SimpleTestCase, override_settings

from gst_tally.services.gst_lookup.base import GSTLookupProviderError
from gst_tally.services.gst_lookup.providers.generic_rest import GenericRESTProvider
from gst_tally.services.gst_lookup.service import GSTLookupService


GSTIN = "33AAACB2894G1ZJ"


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()


class GenericGSTLookupTests(SimpleTestCase):
    def config(self):
        return {"base_url": "https://provider.test/gstin/{gstin}", "api_key": "secret",
                "api_key_header": "X-API-Key", "client_id": "", "client_secret": "",
                "username": "", "password": "", "timeout": 20}

    def test_adapter_normalizes_only_required_fields(self):
        provider = GenericRESTProvider(self.config(), opener=lambda *args, **kwargs: FakeResponse({"data": {
            "gstin": GSTIN, "tradeName": "ABC TRADERS", "legalName": "Not exposed",
            "principalPlaceOfBusiness": {"buildingNumber": "10", "location": "Chennai",
                                         "stateName": "Tamil Nadu", "pincode": "600001"},
        }}))
        result = provider.lookup(GSTIN).as_dict()
        self.assertEqual(result["gstin"], GSTIN)
        self.assertEqual(result["legal_name"], "Not exposed")
        self.assertEqual(result["trade_name"], "ABC TRADERS")
        self.assertEqual(result["principal_address"], "10, Chennai, Tamil Nadu - 600001")

    def test_provider_omitted_fields_are_normalized_to_null(self):
        provider = GenericRESTProvider(self.config(), opener=lambda *args, **kwargs: FakeResponse({"gstin": GSTIN}))
        result = provider.lookup(GSTIN).as_dict()
        self.assertEqual(result["gstin"], GSTIN)
        self.assertIsNone(result["trade_name"])
        self.assertIsNone(result["principal_address"])

    def test_malformed_provider_response_is_rejected(self):
        provider = GenericRESTProvider(self.config(), opener=lambda *args, **kwargs: FakeResponse({"data": "bad"}))
        with self.assertRaises(GSTLookupProviderError): provider.lookup(GSTIN)

    @override_settings(GST_LOOKUP_ENABLED=False, GST_LOOKUP_PROVIDER="", GST_LOOKUP_BASE_URL="")
    def test_disabled_lookup_is_not_configured(self):
        self.assertFalse(GSTLookupService.configured())
