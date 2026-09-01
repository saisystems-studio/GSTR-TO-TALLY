import json

from django.test import SimpleTestCase, override_settings

from gst_tally.services.gst_lookup.providers.jamku import JamkuProvider
from gst_tally.services.gst_lookup.service import GSTLookupService


GSTIN = "27AAJCM9929L1ZM"


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()


class JamkuProviderTests(SimpleTestCase):
    def config(self):
        return {
            "base_url": "https://gst-return-status.p.rapidapi.com",
            "endpoint": "/free/gstin/{gstin}",
            "host": "gst-return-status.p.rapidapi.com",
            "api_key": "test-secret",
            "timeout": 20,
        }

    def test_normalizes_response_and_extracts_address_pincode(self):
        payload = {"success": True, "data": {
            "gstin": GSTIN,
            "tradeName": "MADRECHA SOLUTIONS PRIVATE LIMITED",
            "lgnm": "MADRECHA SOLUTIONS PRIVATE LIMITED",
            "sts": "Active",
            "dty": "Regular",
            "pincode": None,
            "adr": "PLOT NO. A-417, Thane, Maharashtra, 400604",
            "stj": "State - Maharashtra",
            "ctj": "Commissionerate - Thane",
            "ctb": "Private Limited Company",
            "nba": ["Supplier of Services"],
            "einvoiceStatus": "No",
            "cxdt": "29/05/2026",
        }}
        captured = {}
        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(payload)

        result = JamkuProvider(self.config(), opener=opener).lookup(GSTIN).as_dict()
        self.assertEqual(result["trade_name"], "MADRECHA SOLUTIONS PRIVATE LIMITED")
        self.assertEqual(result["status"], "Active")
        self.assertEqual(result["pincode"], "400604")
        self.assertEqual(result["principal_address"], payload["data"]["adr"])
        self.assertEqual(result["nature_of_business"], ["Supplier of Services"])
        self.assertEqual(result["cancellation_date"], "29/05/2026")
        self.assertEqual(captured["request"].headers["X-rapidapi-host"], self.config()["host"])
        self.assertEqual(captured["request"].headers["X-rapidapi-key"], self.config()["api_key"])
        self.assertEqual(captured["timeout"], 20)

    def test_trade_name_falls_back_to_legal_name(self):
        provider = JamkuProvider(self.config(), opener=lambda *args, **kwargs: FakeResponse({
            "success": True, "data": {"gstin": GSTIN, "lgnm": "LEGAL NAME", "adr": "Thane"}
        }))
        self.assertEqual(provider.lookup(GSTIN).trade_name, "LEGAL NAME")

    def test_state_falls_back_to_gstin_state_code_when_provider_omits_it(self):
        provider = JamkuProvider(self.config(), opener=lambda *args, **kwargs: FakeResponse({
            "success": True,
            "data": {"gstin": "33AALFE2101R1ZF", "lgnm": "EM VEERU & CO",
                     "adr": "No.27, Virudhunagar, Tamil Nadu, 626104"},
        }))

        self.assertEqual(provider.lookup("33AALFE2101R1ZF").state, "Tamil Nadu")

    def test_cancelled_registration_is_still_a_successful_taxpayer(self):
        provider = JamkuProvider(self.config(), opener=lambda *args, **kwargs: FakeResponse({
            "success": True,
            "data": {"gstin": GSTIN, "lgnm": "EM VEERU & CO", "tradeName": "EM VEERU & CO",
                     "adr": "No.27, Tamil Nadu, 626104", "sts": "Cancelled suo-moto",
                     "rgdt": "11/06/2024", "cxdt": "29/05/2026", "ctb": "Partnership"},
        }))
        result = provider.lookup(GSTIN).as_dict()
        self.assertEqual(result["trade_name"], "EM VEERU & CO")
        self.assertEqual(result["status"], "Cancelled suo-moto")
        self.assertEqual(result["cancellation_date"], "29/05/2026")

    def test_timeout_is_retried_only_once(self):
        calls = []
        def opener(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1: raise TimeoutError()
            return FakeResponse({"success": True, "data": {"gstin": GSTIN, "lgnm": "LEGAL NAME"}})
        result = JamkuProvider(self.config(), opener=opener).lookup(GSTIN)
        self.assertEqual(result.trade_name, "LEGAL NAME")
        self.assertEqual(len(calls), 2)

    @override_settings(
        GST_LOOKUP_ENABLED=True,
        GST_LOOKUP_PROVIDER="jamku",
        JAMKU_BASE_URL="https://gst-return-status.p.rapidapi.com",
        JAMKU_GSTIN_ENDPOINT="/free/gstin/{gstin}",
        JAMKU_RAPIDAPI_HOST="gst-return-status.p.rapidapi.com",
        JAMKU_RAPIDAPI_KEY="configured",
        GST_LOOKUP_FALLBACK_PROVIDER="",
    )
    def test_jamku_is_registered_and_configured(self):
        self.assertEqual(GSTLookupService.status(), {
            "enabled": True,
            "provider": "jamku",
            "registered": True,
            "configured": True,
            "missing": [],
            "fallback_provider": None,
            "fallback_registered": False,
            "fallback_configured": False,
        })
