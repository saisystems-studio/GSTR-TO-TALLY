import io
import json
import socket
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase

from gst_tally.services.gstin.models import (GSTINAuthenticationError, GSTINNotFoundError,
                                             GSTINRateLimitError, GSTINResponseError,
                                             GSTINTimeoutError, GSTINUnavailableError)
from gst_tally.services.gstin.providers.cleartax import ClearTaxGSTINProvider
from gst_tally.services.party_lookup import process_gstin, valid_gstin


GSTIN = "33AAACB2894G1ZJ"


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()


def http_error(code):
    return HTTPError("https://example.invalid", code, "error", {}, io.BytesIO())


class ClearTaxProviderTests(SimpleTestCase):
    def provider(self, outcome, retries=0):
        def opener(*args, **kwargs):
            if isinstance(outcome, Exception): raise outcome
            return FakeResponse(outcome)
        return ClearTaxGSTINProvider("https://api.clear.in", "entity-id", "secret", retries=retries, opener=opener)

    def test_valid_gstin_and_successful_normalization(self):
        self.assertTrue(valid_gstin(GSTIN))
        result = self.provider({"gstin": GSTIN, "tradeNam": "ABC TRADERS", "pradr": {"addr": {
            "bno": "12", "st": "Main Road", "loc": "Chennai", "stcd": "Tamil Nadu", "pncd": 600001,
        }}}).get_gstin_details(GSTIN)
        self.assertEqual(result.trade_name, "ABC TRADERS")
        self.assertEqual(result.principal_place_of_business, "12, Main Road, Chennai, Tamil Nadu - 600001")

    def test_invalid_gstin_does_not_call_provider_or_database(self):
        self.assertFalse(valid_gstin("NOT-A-GSTIN"))
        self.assertEqual(process_gstin("NOT-A-GSTIN")[0], "Invalid")

    def test_authentication_failure(self):
        for code in (401, 403):
            with self.subTest(code=code), self.assertRaises(GSTINAuthenticationError):
                self.provider(http_error(code)).get_gstin_details(GSTIN)

    def test_not_found(self):
        with self.assertRaises(GSTINNotFoundError): self.provider(http_error(404)).get_gstin_details(GSTIN)

    @patch("gst_tally.services.gstin.providers.cleartax.time.sleep", return_value=None)
    def test_rate_limit_is_retried_then_classified(self, _sleep):
        with self.assertRaises(GSTINRateLimitError): self.provider(http_error(429), retries=1).get_gstin_details(GSTIN)

    def test_timeout(self):
        with self.assertRaises(GSTINTimeoutError): self.provider(URLError(socket.timeout())).get_gstin_details(GSTIN)

    @patch("gst_tally.services.gstin.providers.cleartax.time.sleep", return_value=None)
    def test_temporary_provider_failures(self, _sleep):
        for code in (500, 503):
            with self.subTest(code=code), self.assertRaises(GSTINUnavailableError):
                self.provider(http_error(code), retries=1).get_gstin_details(GSTIN)

    def test_malformed_provider_response(self):
        for payload in ({"data": "bad"}, {"gstin": GSTIN, "tradeNam": ""}):
            with self.subTest(payload=payload), self.assertRaises(GSTINResponseError):
                self.provider(payload).get_gstin_details(GSTIN)
