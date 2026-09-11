import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .auth_service import (
    create_password_reset_code,
    send_password_reset_email,
    verify_and_consume_reset_code,
    verify_device,
)
from .models import UserProfile

User = get_user_model()


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def public_user(user):
    profile = getattr(user, "gst_profile", None)
    return {"id": user.id, "username": user.username, "email": user.email, "phone_number": profile.phone_number if profile else ""}


def find_user(identifier):
    return User.objects.filter(Q(email__iexact=identifier.strip()) | Q(gst_profile__phone_number=identifier.strip()), is_active=True).first()


def normalize_phone(value):
    """Strip a +91/91/0 country or trunk prefix down to the bare 10-digit number."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[-10:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def find_user_by_phone(phone):
    """Look up the single account for a normalized login phone number.

    ``UserProfile.phone_number`` is unique at the DB level, so duplicates can
    only exist from historical/dirty data; guard against silently picking one
    of them and surface it as a distinct, correctable error instead.
    """
    matches = list(User.objects.filter(gst_profile__phone_number=phone, is_active=True)[:2])
    if len(matches) > 1:
        return "AMBIGUOUS"
    return matches[0] if matches else None


def find_user_by_email(email):
    """Same single-account guarantee as ``find_user_by_phone``, for email."""
    matches = list(User.objects.filter(email__iexact=email.strip(), is_active=True)[:2])
    if len(matches) > 1:
        return "AMBIGUOUS"
    return matches[0] if matches else None


def find_user_by_login_identifier(identifier):
    """Resolve the login identifier as an email (contains "@") or a phone
    number, and look up the single matching account for whichever it is."""
    identifier = identifier.strip()
    if "@" in identifier:
        return find_user_by_email(identifier)
    phone = normalize_phone(identifier)
    if len(phone) != 10 or not phone.isdigit():
        return "INVALID"
    return find_user_by_phone(phone)


class RegisterView(APIView):
    """Account registration only -- deliberately independent of Tally/product
    activation (see services/product_license.py for that, triggered later
    from the GST import workflow, never here). No serial number, activation
    key, or device identity is required to create an account."""
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        email = str(request.data.get("email", "")).strip().lower()
        phone = str(request.data.get("phone_number", "")).strip()
        password = str(request.data.get("password", ""))
        errors = {}
        if not username: errors["username"] = "Username is required."
        try: validate_email(email)
        except ValidationError: errors["email"] = "Enter a valid email address."
        if len(phone) != 10 or not phone.isdigit(): errors["phone_number"] = "Phone number must contain exactly 10 digits."
        if len(password) < 8: errors["password"] = "Password must be at least 8 characters."
        if User.objects.filter(username__iexact=username).exists(): errors["username"] = "Username is already registered."
        if User.objects.filter(email__iexact=email).exists(): errors["email"] = "Email is already registered."
        if UserProfile.objects.filter(phone_number=phone).exists(): errors["phone_number"] = "Phone number is already registered."
        if errors: return Response({"detail": next(iter(errors.values())), "errors": errors}, status=400)
        user = User.objects.create_user(username=username, email=email, password=password)
        UserProfile.objects.create(user=user, phone_number=phone, activated_at=timezone.now())
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return Response({"user": public_user(user), **tokens_for(user)}, status=201)


class LoginView(APIView):
    """Email/phone + password only -- no activation key, no device trust.
    Product/Tally activation is a separate, later step (see
    services/product_license.py) and never gates normal login."""
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = str(request.data.get("identifier", "")).strip()
        password = str(request.data.get("password", ""))
        if not identifier:
            return Response({"detail": "Enter your email or phone number."}, status=400)
        user = find_user_by_login_identifier(identifier)
        if user == "INVALID":
            return Response({"detail": "Enter a valid email address or 10-digit phone number."}, status=400)
        if user == "AMBIGUOUS":
            return Response({"detail": "Multiple accounts are linked to this login identifier. Contact support to resolve this."}, status=409)
        if not user or not user.check_password(password):
            return Response({"detail": "Invalid email/phone number or password."}, status=400)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return Response({"user": public_user(user), **tokens_for(user)})


class RefreshView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        try:
            refresh = RefreshToken(str(request.data.get("refresh", "")))
            user = User.objects.get(id=refresh["user_id"], is_active=True)
        except (TokenError, User.DoesNotExist, KeyError):
            return Response({"detail": "Your session has expired. Please login again."}, status=401)
        # No server-side blacklist: the old refresh token is simply left to
        # expire naturally. A new access + refresh pair is issued below.
        return Response(tokens_for(user))


class LogoutView(APIView):
    def post(self, request):
        # Stateless logout, no blacklist DB table required -- the frontend
        # discards its tokens (see authApi.js::signOut) and the old refresh
        # token is left to expire naturally on its own.
        return Response(status=204)


class MeView(APIView):
    def get(self, request):
        return Response({"user": public_user(request.user)})


class DeviceVerifyView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        user = find_user(str(request.data.get("identifier", "")))
        valid = bool(user and verify_device(user, str(request.data.get("device_id", "")), str(request.data.get("trusted_device_token", ""))))
        return Response({"trusted": valid})


# Deliberately the exact same response either way -- confirming or denying
# that an email is registered would let anyone enumerate real accounts.
FORGOT_PASSWORD_GENERIC_RESPONSE = {"detail": "If this email is registered, a verification code has been sent to it."}


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        if not email:
            return Response({"detail": "Enter your registered email address."}, status=400)
        user = find_user_by_email(email)
        if user and user != "AMBIGUOUS":
            code = create_password_reset_code(user)
            send_password_reset_email(user, code)
        return Response(FORGOT_PASSWORD_GENERIC_RESPONSE)


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        code = str(request.data.get("code", "")).strip()
        new_password = str(request.data.get("new_password", ""))
        if len(new_password) < 8:
            return Response({"detail": "Password must be at least 8 characters."}, status=400)
        user = find_user_by_email(email)
        if not user or user == "AMBIGUOUS" or not verify_and_consume_reset_code(user, code):
            return Response({"detail": "That verification code is invalid or has expired."}, status=400)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        return Response({"detail": "Your password has been updated. You can now log in with it."})
