import io
import json
import urllib.error
from django.test import SimpleTestCase
from unittest.mock import patch

from django.test import override_settings

from nexus_auth.client import NexusError, call


@override_settings(NEXUS_BASE_URL='http://localhost:8001', NEXUS_PRODUCT_ID='gstr2tally', NEXUS_PRODUCT_KEY='test')
class ClientErrorTests(SimpleTestCase):
    def rejection(self, status, body):
        error = urllib.error.HTTPError('http://localhost', status, 'error', {}, io.BytesIO(json.dumps(body).encode()))
        with patch('nexus_auth.client.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(NexusError) as caught:
                call('POST', 'register/', {})
        return caught.exception

    def test_field_validation_preserves_email_reason(self):
        error = self.rejection(400, {'code': 'validation_error', 'email': ['Enter a valid email address.']})
        self.assertEqual(error.errors, {'email': ['Enter a valid email address.']})
        self.assertIn('Enter a valid email address.', error.message)

    def test_detail_error_preserves_reason(self):
        error = self.rejection(429, {'detail': 'Request was throttled.', 'code': 'throttled'})
        self.assertEqual(error.message, 'Request was throttled.')
