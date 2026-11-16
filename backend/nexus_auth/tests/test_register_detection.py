"""Registration-page serial detection.
LiveConnectorTests runs the ACTUAL connector code (tally_local_agent.enroll) against an in-process live server and
the REAL TallyPrime on 127.0.0.1:9000 (read-only queries); only the Nexus HTTP call is mocked. Skipped when Tally
is not reachable. Uses the throwaway test database only."""
import importlib.util
import json
import re
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APITestCase

from nexus_auth.client import NexusError

AGENT_DIR = Path(__file__).resolve().parents[3] / "local-tally-agent"
FP = {"X-Device-Fingerprint": "browser-dev-1"}


def tally_up():
    try:
        socket.create_connection(("127.0.0.1", 9000), timeout=2).close()
        return True
    except OSError:
        return False


def load_agent():
    sys.path.insert(0, str(AGENT_DIR))
    spec = importlib.util.spec_from_file_location("tally_local_agent_under_test", AGENT_DIR / "tally_local_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@override_settings(NEXUS_ENABLED=True, NEXUS_PRODUCT_KEY="k", ALLOWED_HOSTS=["*"])
class LiveConnectorTests(LiveServerTestCase):
    def call(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.live_server_url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_real_connector_real_tally_detection_and_register(self):
        if not tally_up():
            self.skipTest("TallyPrime is not running on 127.0.0.1:9000")
        cache.clear()
        agent = load_agent()
        with tempfile.TemporaryDirectory() as tmp:
            opened = []
            with mock.patch.object(agent, "CONFIG_PATH", Path(tmp) / "config.dat"), \
                    mock.patch.object(agent.save_private, "__defaults__", (lambda b: b,)), mock.patch.object(agent, "unprotect", lambda b: b), \
                    mock.patch.object(agent.webbrowser, "open", lambda url: opened.append(url)):
                config = {"installation_id": "11111111-2222-4333-8444-555555555555"}
                agent.enroll(self.live_server_url, config)
                self.assertTrue(opened and "#connector=" in opened[0])
                enrollment_id, proof = re.search(r"#connector=([0-9a-f-]{36})\.(.+)$", opened[0]).groups()
                status, paired = self.call("POST", "/api/nexus/connector/pair/", {"enrollment_id": enrollment_id, "proof": proof}, FP)
                self.assertEqual((status, paired.get("paired")), (200, True), paired)
                agent.enroll(self.live_server_url, config)  # connector reports what it really read from Tally

        status, detected = self.call("GET", "/api/nexus/tally/detect/", headers=FP)
        print("REAL TALLY DETECT ->", status, {k: detected.get(k) for k in ("detected", "tally_serial", "code")})
        self.assertEqual(status, 200)
        if detected["detected"]:
            serial = detected["tally_serial"]
            self.assertRegex(serial, r"^[0-9A-Za-z]{5,30}$")
            with mock.patch("nexus_auth.views.call", return_value={"message": "registered"}) as call:
                status, body = self.call("POST", "/api/nexus/register/", {"name": "T", "email": "t@example.com", "contact_number": "9876543210",
                                                                         "tally_serial": "99999"}, FP)
            self.assertEqual(status, 201, body)
            payload = call.call_args[0][2]
            self.assertEqual(payload["tally_serial"], serial)  # verified serial, never the client's
            self.assertEqual(payload["email"], "t@example.com")
        else:
            # Tally is open but reports no valid serial (e.g. "0"): clear, specific message, registration blocked.
            self.assertEqual(detected["code"], "tally_license_unavailable")
            status, body = self.call("POST", "/api/nexus/register/", {"name": "T", "email": "t@example.com", "contact_number": "9876543210"}, FP)
            self.assertEqual(status, 409)


@override_settings(NEXUS_ENABLED=True, NEXUS_PRODUCT_KEY="k")
class RegisterBehaviourTests(APITestCase):
    def setUp(self):
        cache.clear()

    def detected(self, serial="776776311"):
        return mock.patch("nexus_auth.views.detected_tally", return_value={"detected": True, "tally_serial": serial, "identity": {}})

    def test_already_registered_serial_shows_exact_message_without_changes(self):
        body = {"name": "Ravi", "email": "r@example.com", "contact_number": "9876543210"}
        err = NexusError(409, "account_exists", "An account already exists for this Tally Serial Number.")
        with self.detected(), mock.patch("nexus_auth.views.call", side_effect=err) as call:
            r = self.client.post("/api/nexus/register/", body, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (409, "account_exists"))
        self.assertIn("already registered", r.data["detail"])
        self.assertIn("Activate User", r.data["detail"])
        self.assertEqual(call.call_count, 1)  # exactly one create attempt, no retries/duplicates

    def test_validation_error_passes_through(self):
        err = NexusError(400, "validation_error", "Please correct the highlighted fields.", {"email": ["Enter a valid email address."]})
        with self.detected(), mock.patch("nexus_auth.views.call", side_effect=err):
            r = self.client.post("/api/nexus/register/", {"name": "R", "email": "bad", "contact_number": "9876543210"}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (400, "validation_error"))

    def test_running_tally_without_valid_serial_has_specific_message(self):
        from gst_tally.models import ConnectorEnrollment
        from nexus_auth.views import digest
        import uuid
        from datetime import timedelta
        from django.utils import timezone
        enrollment = ConnectorEnrollment.objects.create(
            installation_id=uuid.uuid4(), poll_hash=digest("poll"), proof_hash=digest("proof"), browser_device="browser-dev-1",
            expires_at=timezone.now() + timedelta(hours=1))
        r = self.client.post("/api/nexus/connector/discovery/", {"enrollment_id": str(enrollment.enrollment_id), "poll_secret": "poll",
                                                                  "tally_reachable": True, "tally_serial": "0"}, format="json")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/nexus/tally/detect/", HTTP_X_DEVICE_FINGERPRINT="browser-dev-1")
        self.assertFalse(r.data["detected"])
        self.assertEqual(r.data["code"], "tally_license_unavailable")
        self.assertIn("Retry", r.data["message"])
        # and a closed Tally is still reported as not running
        self.client.post("/api/nexus/connector/discovery/", {"enrollment_id": str(enrollment.enrollment_id), "poll_secret": "poll",
                                                              "tally_reachable": False, "tally_serial": ""}, format="json")
        r = self.client.get("/api/nexus/tally/detect/", HTTP_X_DEVICE_FINGERPRINT="browser-dev-1")
        self.assertEqual(r.data["code"], "tally_not_running")
