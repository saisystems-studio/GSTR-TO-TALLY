import io
import json
import os
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import SimpleTestCase, override_settings

from gst_tally.services.gst_lookup.base import (GSTLookupConfigurationError,
                                                GSTLookupNotFoundError,
                                                GSTLookupProviderError,
                                                GSTLookupTimeoutError)
from gst_tally.services.gst_lookup.providers.vayana import VayanaProvider
from gst_tally.services.gst_lookup.vayana_auth import VayanaAuthSigner, build_auth_token, vayana_timestamp


GSTIN = "33AAACB2894G1ZJ"


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()


class VayanaProviderTests(SimpleTestCase):
    def setUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        handle = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
        handle.write(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
        handle.close()
        self.key_path = handle.name

    def tearDown(self):
        os.unlink(self.key_path)

    def config(self):
        return {"base_url": "https://sandbox.vayana.invalid", "client_id": "client-id",
                "client_secret": "client-secret", "cust_id": "", "private_key_path": self.key_path,
                "auth_gstin": GSTIN, "signature_algorithm": "SHA256", "timeout": 15}

    def test_token_timestamp_and_rsa_signature(self):
        now = datetime(2026, 8, 21, 12, 30, 45, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        timestamp = vayana_timestamp(now)
        token = build_auth_token(cust_id="", client_id="client-id", txn_id="txn-1",
                                 timestamp=timestamp, gstin=GSTIN, api_action="TP")
        self.assertEqual(timestamp, "20260821123045+0530")
        self.assertEqual(token, f"v2.0::client-id:txn-1:{timestamp}:{GSTIN}:TP")
        signature = VayanaAuthSigner(self.key_path, "SHA256").sign(token)
        import base64
        self.key.public_key().verify(base64.b64decode(signature), token.encode(), padding.PKCS1v15(), hashes.SHA256())

    def test_successful_search_is_normalized(self):
        payload = {"gstin": GSTIN, "lgnm": "ABC PRIVATE LIMITED", "tradeNam": "ABC",
                   "sts": "Active", "dty": "Regular", "rgdt": "01/01/2020",
                   "ctb": "Private Limited Company", "nba": ["Wholesale Business"],
                   "einvoiceStatus": "Yes", "stj": "State Jurisdiction", "ctj": "Centre Jurisdiction",
                   "stjCd": "TN001", "ctjCd": "TN002", "lstupdt": "05/01/2026",
                   "pradr": {"addr": {"bno": "10", "st": "Main Road", "loc": "Chennai",
                                      "stcd": "Tamil Nadu", "pncd": "600001"}, "ntr": ["Office"]},
                   "adadr": [{"addr": {"bno": "20", "loc": "Madurai", "stcd": "Tamil Nadu",
                                         "pncd": "625001"}, "ntr": ["Warehouse"]}]}
        captured = {}
        def opener(request, **kwargs):
            captured["url"], captured["headers"] = request.full_url, dict(request.header_items())
            return FakeResponse(payload)
        result = VayanaProvider(self.config(), opener=opener).lookup(f" {GSTIN.lower()} ".strip().upper()).as_dict()
        self.assertIn("gstin=33AAACB2894G1ZJ&action=TP", captured["url"])
        lowered_headers = {key.lower(): value for key, value in captured["headers"].items()}
        self.assertNotIn("clientid", lowered_headers)
        self.assertNotIn("client-secret", lowered_headers)
        self.assertNotIn("client-secret", str(result))
        self.assertEqual(result["trade_name"], "ABC")
        self.assertEqual(result["principal_address"], "10, Main Road, Chennai, Tamil Nadu - 600001")
        self.assertEqual(result["state"], "Tamil Nadu")
        self.assertEqual(result["pincode"], "600001")
        self.assertEqual(result["constitution_of_business"], "Private Limited Company")
        self.assertEqual(result["nature_of_business"], ["Wholesale Business"])
        self.assertEqual(result["principal_business_nature"], ["Office"])
        self.assertEqual(result["additional_places"][0]["business_nature"], ["Warehouse"])

    def test_legal_name_is_display_fallback(self):
        payload = {"gstin": GSTIN, "lgnm": "LEGAL NAME", "pradr": {"addr": {"loc": "Chennai"}}}
        result = VayanaProvider(self.config(), opener=lambda *a, **k: FakeResponse(payload)).lookup(GSTIN)
        self.assertEqual(result.trade_name, "LEGAL NAME")

    def test_vayana_error_codes(self):
        for code, exception in (("FO8001", GSTLookupProviderError), ("FO8007", GSTLookupNotFoundError)):
            with self.subTest(code=code), self.assertRaises(exception):
                VayanaProvider(self.config(), opener=lambda *a, **k: FakeResponse({"code": code})).lookup(GSTIN)

    def test_invalid_gstin_never_calls_http(self):
        called = []
        with self.assertRaises(GSTLookupProviderError):
            VayanaProvider(self.config(), opener=lambda *a, **k: called.append(True)).lookup("invalid")
        self.assertEqual(called, [])

    def test_timeout(self):
        with self.assertRaises(GSTLookupTimeoutError):
            VayanaProvider(self.config(), opener=lambda *a, **k: (_ for _ in ()).throw(URLError(socket.timeout()))).lookup(GSTIN)

    def test_invalid_private_key_fails_safely(self):
        with open(self.key_path, "wb") as handle: handle.write(b"not a private key")
        with self.assertRaises(GSTLookupConfigurationError): VayanaAuthSigner(self.key_path, "SHA256")

    @override_settings(VAYANA_BASE_URL="", VAYANA_CLIENT_ID="",
                       VAYANA_CUST_ID="", VAYANA_PRIVATE_KEY_PATH="", VAYANA_AUTH_GSTIN="",
                       VAYANA_SIGNATURE_ALGORITHM="")
    def test_missing_configuration_lists_names_only(self):
        issues = VayanaProvider.configuration_issues()
        self.assertIn("VAYANA_BASE_URL", issues)
        self.assertIn("VAYANA_PRIVATE_KEY_PATH", issues)
        self.assertNotIn("client-secret", str(issues).lower())
