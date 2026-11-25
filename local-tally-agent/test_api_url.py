import unittest
from tally_local_agent import build_server_api_url as b, API_PREFIX


class UrlTests(unittest.TestCase):
    def test_local(self):
        self.assertEqual(b('http://127.0.0.1:8000', API_PREFIX + 'heartbeat/'),
                         'http://127.0.0.1:8000/api/gst-tally/local-agent/heartbeat/')

    def test_prod(self):
        self.assertEqual(b('https://saiinfosysmdu.in', API_PREFIX + 'next-job/'),
                         'https://saiinfosysmdu.in/gstr2tally/api/gst-tally/local-agent/next-job/')

    def test_discovery(self):
        self.assertEqual(b('https://saiinfosysmdu.in/', '/api/nexus/connector/discovery/'),
                         'https://saiinfosysmdu.in/gstr2tally/api/nexus/connector/discovery/')

    def test_rejects(self):
        with self.assertRaises(ValueError): b('https://evil.com', '/api/x/')
        with self.assertRaises(ValueError): b('http://saiinfosysmdu.in', '/api/x/')
        with self.assertRaises(ValueError): b('https://saiinfosysmdu.in', '/other/')


if __name__ == '__main__':
    unittest.main()
