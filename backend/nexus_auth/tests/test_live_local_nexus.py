"""REAL end-to-end test against a LOCAL Nexus (no mocked Nexus). Run only when
NEXUS_LIVE_TEST=1 and NEXUS_BASE_URL points to an isolated local Nexus whose DB is
disposable, e.g.:  NEXUS_LIVE_TEST=1 NEXUS_STAFF_USER=Superadmin NEXUS_STAFF_PASSWORD=... manage.py test nexus_auth.tests.test_live_local_nexus
Mail is read from Nexus' file email backend directory NEXUS_MAIL_DIR."""
import glob
import http.cookiejar
import json
import os
import re
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from django.conf import settings
from rest_framework.test import APITestCase

LIVE = os.environ.get("NEXUS_LIVE_TEST") == "1"


class Staff:
    def __init__(self, base, user, password):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""
        self.csrf = self.req("GET", "/api/auth/csrf/").get("csrfToken", "") or self.cookie("csrftoken")
        self.req("POST", "/api/auth/login/", {"username": user, "password": password})
        self.csrf = self.req("GET", "/api/auth/csrf/").get("csrfToken", "") or self.cookie("csrftoken")

    def cookie(self, name):
        return next((c.value for c in self.jar if c.name == name), "")

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json", "X-CSRFToken": self.csrf or self.cookie("csrftoken"), "Referer": self.base + "/"})
        try:
            with self.op.open(r, timeout=20) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise AssertionError(f"{method} {path} -> {exc.code} {exc.read().decode()[:300]}") from None


def latest_mail(mail_dir, to, after):
    time.sleep(0.3)
    for path in sorted(glob.glob(os.path.join(mail_dir, "*.log")), key=os.path.getmtime, reverse=True):
        if os.path.getmtime(path) < after - 1:
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        for chunk in reversed(text.split("\n-------------------------------------------------------------------------------\n")):
            if to in chunk:
                return chunk
    return ""


@unittest.skipUnless(LIVE, "set NEXUS_LIVE_TEST=1 with a local Nexus")
class LiveLocalNexus(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        base = settings.NEXUS_BASE_URL
        self.assertTrue(base.startswith("http://127.0.0.1") or base.startswith("http://localhost"), "refusing non-local Nexus")
        self.staff = Staff(base, os.environ["NEXUS_STAFF_USER"], os.environ["NEXUS_STAFF_PASSWORD"])
        self.mail_dir = os.environ["NEXUS_MAIL_DIR"]
        self.serial = "9" + str(int(time.time()))[-8:]
        self.email = f"live{self.serial}@example.com"
        self.t0 = time.time()

    def device(self, n):
        return {"client_device_id": f"live-dev-{n}-{self.serial}", "fingerprint": f"fp-{n}-{self.serial}", "device_name": f"PC{n}", "app_version": "1.0"}

    def register(self):
        det = {"detected": True, "tally_serial": self.serial, "identity": {}}
        with mock.patch("nexus_auth.views.detected_tally", return_value=det):
            r = self.client.post("/api/nexus/register/", {"name": "Live Test", "email": self.email, "contact_number": "9876543210"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["user_id"], self.serial)  # User ID = Tally serial
        mailtxt = latest_mail(self.mail_dir, self.email, self.t0)
        temp = re.search(r"Temporary Password: (\S+)", mailtxt)
        self.assertTrue(temp, "welcome email with temporary password not found")
        return temp.group(1)

    def customer(self):
        rows = self.staff.req("GET", f"/api/v1/tally-sl-admin/products/gstr2tally/customers/?search={self.serial}")
        results = rows.get("results", rows if isinstance(rows, list) else [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_id"], self.serial)
        return results[0]

    def activate_plan(self, plan, **extra):
        cid = self.customer()["id"]
        r = self.staff.req("POST", f"/api/v1/tally-sl-admin/products/gstr2tally/customers/{cid}/license/activate/", {"license_plan": plan, **extra})
        return r["activation_key"]

    def login(self, pw, n=1):
        return self.client.post("/api/nexus/login/", {"user_id": self.serial, "password": pw, "device": self.device(n)}, format="json")

    def activate_device(self, key, n, transfer=False):
        """Request code (Nexus allows 1/min per account), verify, then confirm. With transfer='warn' the
        un-confirmed attempt is asserted to be refused first, then confirmed with the same ticket."""
        wait = 62 - (time.time() - getattr(self, "last_code_at", 0))
        if wait > 0:
            time.sleep(wait)
        self.client.post("/api/nexus/activate/request-code/", {"user_id": self.serial}, format="json")
        self.last_code_at = time.time()
        mailtxt = latest_mail(self.mail_dir, self.email, time.time() - 5)
        code = re.search(r"(?:code|Code)[^A-Z0-9]{0,40}([A-Z0-9]{8})", mailtxt).group(1)
        v = self.client.post("/api/nexus/activate/verify/", {"user_id": self.serial, "code": code, "activation_key": key}, format="json")
        self.assertEqual(v.status_code, 200, v.data)
        body = {"ticket": v.data["ticket"], "device": self.device(n)}
        if transfer == "warn":
            self.assertTrue(v.data["requires_confirmation"])
            self.assertIn("another device", v.data["message"])
            first = self.client.post("/api/nexus/activate/confirm/", {**body, "confirm_transfer": False}, format="json")
            self.assertEqual((first.status_code, first.data["code"]), (409, "confirmation_required"))
            transfer = True
        return self.client.post("/api/nexus/activate/confirm/", {**body, "confirm_transfer": bool(transfer)}, format="json")

    def test_full_lifecycle(self):
        temp = self.register()
        self.assertEqual(self.register_dup().status_code, 409)
        cust = self.customer()
        self.assertEqual(cust["license_status"].upper(), "PENDING")

        # first login with temp password -> must change password, no local session
        r = self.login(temp)
        self.assertEqual((r.status_code, r.data["code"]), (403, "password_change_required"))
        self.assertNotIn("access", r.data)
        wrong = self.login("wrong-password")
        self.assertEqual((wrong.status_code, wrong.data["code"]), (401, "invalid_credentials"))
        newpw = "Live#Pass12345"
        c = self.client.post("/api/nexus/password/change/", {"ticket": r.data["ticket"], "current_password": temp, "new_password": newpw}, format="json")
        self.assertEqual(c.status_code, 200, c.data)

        # no licence yet -> login must be refused (activation required), still no JWT
        r = self.login(newpw)
        self.assertEqual((r.status_code, r.data["code"]), (403, "activation_required"))
        self.assertNotIn("access", r.data)

        # Superadmin assigns ANNUAL; activation on laptop 1
        key = self.activate_plan("ANNUAL")
        a = self.activate_device(key, 1)
        self.assertEqual(a.status_code, 200, a.data)
        self.assertEqual(a.data["license"]["license_plan"], "ANNUAL")
        self.assertTrue(a.data["license"]["authorized"])
        self.assertTrue(a.data["license"]["expires_at"])
        h1 = {"HTTP_AUTHORIZATION": f"Bearer {a.data['access']}"}

        # dashboard-gating API works only with entitlement
        self.assertEqual(self.client.get("/api/auth/me/", **h1).status_code, 200)
        lic = self.client.get("/api/nexus/license/", **h1)
        self.assertEqual((lic.status_code, lic.data["state"]), (200, "active"))
        self.assertEqual(self.client.get("/api/nexus/session/", **h1).status_code, 200)

        # usage event reaches Nexus reporting
        u = self.client.post("/api/nexus/usage/open/", {"app_version": "1.0"}, format="json", **h1)
        self.assertEqual(u.status_code, 200, u.data)
        perf = self.staff.req("GET", f"/api/v1/tally-sl-admin/reports/user-performance/?product=gstr2tally&search={self.serial}")
        self.assertIn(self.serial, json.dumps(perf))

        # token refresh + normal login on enrolled device
        self.assertEqual(self.client.post("/api/nexus/token/refresh/", {}, format="json", **h1).status_code, 200)
        r = self.login(newpw, 1)
        self.assertEqual(r.status_code, 200, r.data)
        h1 = {"HTTP_AUTHORIZATION": f"Bearer {r.data['access']}"}

        # login from an unenrolled device is not authorised
        r2 = self.login(newpw, 2)
        self.assertEqual((r2.status_code, r2.data["code"]), (403, "device_mismatch"))

        # replacement laptop: warning first, then confirmed transfer
        moved = self.activate_device(key, 2, transfer="warn")
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertTrue(moved.data["device_replaced"])
        h2 = {"HTTP_AUTHORIZATION": f"Bearer {moved.data['access']}"}

        # previous device is revoked at the next check and its local JWT dies
        self.assertEqual(self.client.get("/api/nexus/session/", **h1).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me/", **h1).status_code, 401)
        self.assertEqual(self.client.get("/api/nexus/session/", **h2).status_code, 200)

        # logout revokes the new device's session
        self.assertEqual(self.client.post("/api/nexus/logout/", {}, format="json", **h2).status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me/", **h2).status_code, 401)

        # plan variants + inactivation, server-authoritative
        cid = self.customer()["id"]
        base = f"/api/v1/tally-sl-admin/products/gstr2tally/customers/{cid}/license/"
        self.staff.req("POST", base + "inactivate/", {})
        r = self.login(newpw, 2)
        self.assertEqual((r.status_code, r.data["code"]), (403, "license_inactive"))
        self.staff.req("POST", base + "renew/", {})
        self.assertEqual(self.login(newpw, 2).status_code, 200)

    def register_dup(self):
        det = {"detected": True, "tally_serial": self.serial, "identity": {}}
        with mock.patch("nexus_auth.views.detected_tally", return_value=det):
            return self.client.post("/api/nexus/register/", {"name": "Live Test", "email": self.email, "contact_number": "9876543210"}, format="json")

    def test_trial_and_lifetime_plans(self):
        for plan, extra in (("TRIAL", {"trial_days": 3}), ("LIFETIME", {})):
            self.serial = "9" + str(int(time.time() * 1000))[-8:]
            self.email = f"live{self.serial}@example.com"
            self.t0 = time.time()
            self.last_code_at = 0
            temp = self.register()
            r = self.login(temp)
            self.client.post("/api/nexus/password/change/", {"ticket": r.data["ticket"], "current_password": temp, "new_password": "Live#Pass12345"}, format="json")
            key = self.activate_plan(plan, **extra)
            a = self.activate_device(key, 1)
            self.assertEqual(a.status_code, 200, a.data)
            self.assertEqual(a.data["license"]["license_plan"], plan)
            if plan == "LIFETIME":
                self.assertFalse(a.data["license"]["expires_at"])

    def test_nexus_down_denies_login(self):
        with self.settings(NEXUS_BASE_URL="http://127.0.0.1:1/"):
            r = self.client.post("/api/nexus/login/", {"user_id": "1", "password": "x", "device": self.device(1)}, format="json")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("access", r.data)
