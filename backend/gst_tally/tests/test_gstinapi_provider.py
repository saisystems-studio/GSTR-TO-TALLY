import json

from django.test import SimpleTestCase, override_settings

from gst_tally.services.gst_lookup.providers.gstinapi import GSTINAPIProvider
from gst_tally.services.gst_lookup.service import GSTLookupService


GSTIN = "33FRWPM7826N1ZP"


class FakeResponse:
    status = 200
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()


class GSTINAPIProviderTests(SimpleTestCase):
    def config(self):
        return {"base_url": "https://www.gstinapi.in", "endpoint": "/v1/gstin/{gstin}",
                "api_key": "test-only", "api_key_header": "x-api-key", "timeout": 20}

    def test_documented_response_is_normalized(self):
        payload = {"success": True, "data": {
            "gstin": GSTIN, "legal_name": "SIVAKUMAR MUTHIRULAPPAN",
            "trade_name": "M. SIVAKUMAR", "status": "Active", "taxpayer_type": "Regular",
            "business_constitution": "Proprietorship", "registration_date": "2019-10-23",
            "cancellation_date": None, "address": "1/6 NORTH STREET, TAMIL NADU",
            "pincode": "626106", "state_jurisdiction": "VIRUDHUNAGAR",
        }}
        captured = {}
        def opener(request, timeout):
            captured["request"] = request
            return FakeResponse(payload)
        result = GSTINAPIProvider(self.config(), opener=opener).lookup(GSTIN).as_dict()
        self.assertEqual(result["trade_name"], "M. SIVAKUMAR")
        self.assertEqual(result["principal_address"], "1/6 NORTH STREET, TAMIL NADU")
        self.assertEqual(result["pincode"], "626106")
        self.assertEqual(captured["request"].headers["X-api-key"], "test-only")

    def test_flat_response_without_success_flag_is_normalized(self):
        payload = {
            "gstin": GSTIN, "legal_name": "SIVAKUMAR MUTHIRULAPPAN",
            "trade_name": "M. SIVAKUMAR", "status": "Active",
            "taxpayer_type": "Regular", "registration_date": "2019-10-23",
            "address": "1/6 NORTH STREET, TAMIL NADU",
        }
        provider = GSTINAPIProvider(self.config(), opener=lambda *args, **kwargs: FakeResponse(payload))

        result = provider.lookup(GSTIN).as_dict()

        self.assertEqual(result["gstin"], GSTIN)
        self.assertEqual(result["trade_name"], "M. SIVAKUMAR")
        self.assertEqual(result["principal_address"], "1/6 NORTH STREET, TAMIL NADU")

    @override_settings(
        GST_LOOKUP_ENABLED=True, GST_LOOKUP_PROVIDER="jamku", GST_LOOKUP_PRIMARY_PROVIDER="jamku",
        GST_LOOKUP_FALLBACK_PROVIDER="gstinapi", GSTINAPI_BASE_URL="https://www.gstinapi.in",
        GSTINAPI_ENDPOINT="/v1/gstin/{gstin}", GSTINAPI_API_KEY="",
        GSTINAPI_API_KEY_HEADER="x-api-key", JAMKU_BASE_URL="https://example.test",
        JAMKU_GSTIN_ENDPOINT="/free/gstin/{gstin}", JAMKU_RAPIDAPI_HOST="example.test",
        JAMKU_RAPIDAPI_KEY="primary-key",
    )
    def test_missing_fallback_key_does_not_break_primary_configuration(self):
        status = GSTLookupService.status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["provider"], "jamku")
        self.assertEqual(status["fallback_provider"], "gstinapi")
        self.assertTrue(status["fallback_registered"])
        self.assertFalse(status["fallback_configured"])
