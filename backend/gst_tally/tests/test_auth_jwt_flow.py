"""Login -> refresh -> expiry -> re-login, with no server-side token
blacklist. See config/settings.py::SIMPLE_JWT and gst_tally/auth_views.py --
rest_framework_simplejwt.token_blacklist is intentionally not installed."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

User = get_user_model()

CREDENTIALS = {"identifier": "arun@example.com", "password": "Passw0rd!23"}


class JwtSessionFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="arun", email="arun@example.com", password="Passw0rd!23")
        self.client = APIClient()

    def test_login_generates_access_and_refresh_tokens(self):
        response = self.client.post("/api/auth/login/", CREDENTIALS, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("access"))
        self.assertTrue(response.data.get("refresh"))

    def test_access_token_authenticates_protected_endpoint(self):
        login = self.client.post("/api/auth/login/", CREDENTIALS, format="json").data

        response = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {login['access']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["email"], "arun@example.com")

    def test_expired_access_token_is_rejected_then_refresh_issues_a_new_one(self):
        login = self.client.post("/api/auth/login/", CREDENTIALS, format="json").data
        expired_access = AccessToken.for_user(self.user)
        expired_access.set_exp(lifetime=timedelta(seconds=-1))

        denied = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {expired_access}")
        self.assertEqual(denied.status_code, 401)

        refreshed = self.client.post("/api/auth/refresh/", {"refresh": login["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(refreshed.data.get("access"))
        self.assertTrue(refreshed.data.get("refresh"))

        retried = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {refreshed.data['access']}")
        self.assertEqual(retried.status_code, 200)

    def test_expired_refresh_token_is_rejected(self):
        expired_refresh = RefreshToken.for_user(self.user)
        expired_refresh.set_exp(lifetime=timedelta(seconds=-1))

        response = self.client.post("/api/auth/refresh/", {"refresh": str(expired_refresh)}, format="json")

        self.assertEqual(response.status_code, 401)
        self.assertIn("expired", response.data["detail"].lower())

    def test_login_after_a_prior_session_issues_completely_new_tokens(self):
        first = self.client.post("/api/auth/login/", CREDENTIALS, format="json").data
        second = self.client.post("/api/auth/login/", CREDENTIALS, format="json").data

        self.assertNotEqual(first["access"], second["access"])
        self.assertNotEqual(first["refresh"], second["refresh"])

    def test_logout_succeeds_without_any_blacklist_table(self):
        login = self.client.post("/api/auth/login/", CREDENTIALS, format="json").data

        response = self.client.post("/api/auth/logout/", {"refresh": login["refresh"]}, format="json",
                                    HTTP_AUTHORIZATION=f"Bearer {login['access']}")

        self.assertEqual(response.status_code, 204)
        # The "logged out" refresh token is never invalidated server-side --
        # by design, no blacklist -- so it still works until it naturally
        # expires. The frontend is what discards it (authApi.js::signOut).
        still_valid = self.client.post("/api/auth/refresh/", {"refresh": login["refresh"]}, format="json")
        self.assertEqual(still_valid.status_code, 200)

    def test_token_blacklist_app_is_not_installed(self):
        from django.conf import settings
        self.assertNotIn("rest_framework_simplejwt.token_blacklist", settings.INSTALLED_APPS)


class RegisterThenLoginFlowTests(TestCase):
    """Register -> Login, with no product-activation step in between and no
    activation key anywhere in the authentication flow (see
    gst_tally/auth_views.py::RegisterView/LoginView)."""

    def setUp(self):
        self.client = APIClient()

    def test_register_succeeds_with_no_device_serial_or_activation_key(self):
        # Deliberately the bare minimum fields -- no device_id, serial_number
        # or activation_key at all, proving registration never requires them.
        response = self.client.post("/api/auth/register/", {
            "username": "priya_1",
            "full_name": "Priya",
            "email": "priya@example.com",
            "phone_number": "9876543210",
            "password": "Passw0rd!23",
        }, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data.get("access"))
        self.assertTrue(response.data.get("refresh"))
        self.assertNotIn("requires_activation", response.data)
        self.assertNotIn("trusted_device_token", response.data)

    def test_registered_user_can_immediately_login_with_email_and_password(self):
        self.client.post("/api/auth/register/", {
            "username": "priya_2",
            "full_name": "Priya",
            "email": "priya2@example.com",
            "phone_number": "9876543211",
            "password": "Passw0rd!23",
        }, format="json")

        response = self.client.post("/api/auth/login/", {
            "identifier": "priya2@example.com",
            "password": "Passw0rd!23",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("access"))
        self.assertTrue(response.data.get("refresh"))

    def test_registered_user_can_login_with_phone_number_and_password(self):
        self.client.post("/api/auth/register/", {
            "username": "priya_3",
            "full_name": "Priya",
            "email": "priya3@example.com",
            "phone_number": "9876543212",
            "password": "Passw0rd!23",
        }, format="json")

        response = self.client.post("/api/auth/login/", {
            "identifier": "9876543212",
            "password": "Passw0rd!23",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("access"))

    def test_login_never_asks_for_activation_key_regardless_of_password_correctness(self):
        self.client.post("/api/auth/register/", {
            "username": "priya_4",
            "full_name": "Priya",
            "email": "priya4@example.com",
            "phone_number": "9876543213",
            "password": "Passw0rd!23",
        }, format="json")

        wrong_password = self.client.post("/api/auth/login/", {
            "identifier": "priya4@example.com",
            "password": "wrong-password",
        }, format="json")

        self.assertEqual(wrong_password.status_code, 400)
        self.assertNotIn("requires_activation", wrong_password.data)
        self.assertNotIn("activation", wrong_password.data.get("detail", "").lower())

    def test_login_requires_a_password_even_for_an_account_with_no_password_set(self):
        # An account that never went through a password-setting flow has an
        # unusable Django password -- login must still require (and reject
        # a blank) password rather than falling back to any device/activation
        # based identity check.
        user = User.objects.create_user(username="nopass", email="nopass@example.com")
        user.set_unusable_password()
        user.save()

        response = self.client.post("/api/auth/login/", {
            "identifier": "nopass@example.com",
            "password": "",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access", response.data)
