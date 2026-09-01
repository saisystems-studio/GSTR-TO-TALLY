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

from .auth_service import activate_device, validate_license, verify_device
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
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        email = str(request.data.get("email", "")).strip().lower()
        phone = str(request.data.get("phone_number", "")).strip()
        serial_number = str(request.data.get("serial_number", "")).strip()
        activation_key = str(request.data.get("activation_key", "")).strip()
        device_id = str(request.data.get("device_id", "")).strip()
        device_name = str(request.data.get("device_name", "")).strip()
        errors = {}
        if not username: errors["username"] = "Username is required."
        try: validate_email(email)
        except ValidationError: errors["email"] = "Enter a valid email address."
        if len(phone) != 10 or not phone.isdigit(): errors["phone_number"] = "Phone number must contain exactly 10 digits."
        if bool(serial_number) != bool(activation_key):
            errors["serial_number" if not serial_number else "activation_key"] = "Enter both serial number and activation key, or leave both blank."
        if not device_id or len(device_id) > 64: errors["device_id"] = "Device verification failed."
        if User.objects.filter(username__iexact=username).exists(): errors["username"] = "Username is already registered."
        if User.objects.filter(email__iexact=email).exists(): errors["email"] = "Email is already registered."
        if UserProfile.objects.filter(phone_number=phone).exists(): errors["phone_number"] = "Phone number is already registered."
        if errors: return Response({"detail": next(iter(errors.values())), "errors": errors}, status=400)
        license_obj = None
        if serial_number and activation_key:
            try:
                license_obj = validate_license(serial_number, email, activation_key)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=400)
        user = User.objects.create_user(username=username, email=email)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        UserProfile.objects.create(user=user, phone_number=phone, activated_at=timezone.now())
        trusted_token = None
        if license_obj:
            license_obj.is_activated = True
            license_obj.activated_user = user
            license_obj.activated_at = timezone.now()
            license_obj.save(update_fields=["is_activated", "activated_user", "activated_at", "updated_at"])
            _, trusted_token = activate_device(user, license_obj, device_id, device_name)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        payload = {"user": public_user(user), **tokens_for(user)}
        if trusted_token:
            payload["trusted_device_token"] = trusted_token
        return Response(payload, status=201)


class LoginView(APIView):
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        identifier = str(request.data.get("identifier", "")).strip()
        device_id = str(request.data.get("device_id", "")).strip()
        trusted_token = str(request.data.get("trusted_device_token", ""))
        device_name = str(request.data.get("device_name", "")).strip()
        if not identifier:
            return Response({"detail": "Enter your email or phone number."}, status=400)
        user = find_user_by_login_identifier(identifier)
        if user == "INVALID":
            return Response({"detail": "Enter a valid email address or 10-digit phone number."}, status=400)
        if user == "AMBIGUOUS":
            return Response({"detail": "Multiple accounts are linked to this login identifier. Contact support to resolve this."}, status=409)
        if not user: return Response({"detail": "Invalid email/phone number or password."}, status=400)
        activation = verify_device(user, device_id, trusted_token)
        replacement_token = None
        if not activation:
            if not hasattr(user, "activated_license"):
                user.last_login = timezone.now()
                user.save(update_fields=["last_login"])
                return Response({"user": public_user(user), **tokens_for(user)})
            activation_key = str(request.data.get("activation_key", "")).strip()
            serial_number = str(request.data.get("serial_number", "")).strip()
            if not activation_key or not serial_number:
                return Response({"detail": "This device is not activated.", "requires_activation": True}, status=403)
            try:
                license_obj = validate_license(serial_number, user.email, activation_key, user, device_id)
            except ValueError as exc:
                return Response({"detail": str(exc), "requires_activation": True}, status=403)
            _, replacement_token = activate_device(user, license_obj, device_id, device_name)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        payload = {"user": public_user(user), **tokens_for(user)}
        if replacement_token: payload["trusted_device_token"] = replacement_token
        return Response(payload)


class RefreshView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        try:
            refresh = RefreshToken(str(request.data.get("refresh", "")))
            user = User.objects.get(id=refresh["user_id"], is_active=True)
        except (TokenError, User.DoesNotExist, KeyError):
            return Response({"detail": "Your session has expired. Please login again."}, status=401)
        if hasattr(user, "activated_license") and not verify_device(user, str(request.data.get("device_id", "")), str(request.data.get("trusted_device_token", ""))):
            return Response({"detail": "Device verification failed."}, status=401)
        refresh.blacklist()
        return Response(tokens_for(user))


class LogoutView(APIView):
    def post(self, request):
        try: RefreshToken(str(request.data.get("refresh", ""))).blacklist()
        except TokenError: pass
        return Response(status=204)


class MeView(APIView):
    def get(self, request):
        if hasattr(request.user, "activated_license") and not verify_device(
            request.user,
            request.headers.get("X-Device-ID", ""),
            request.headers.get("X-Trusted-Device-Token", ""),
        ):
            return Response({"detail": "Unable to verify trusted device."}, status=401)
        return Response({"user": public_user(request.user)})


class DeviceVerifyView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        user = find_user(str(request.data.get("identifier", "")))
        valid = bool(user and verify_device(user, str(request.data.get("device_id", "")), str(request.data.get("trusted_device_token", ""))))
        return Response({"trusted": valid})
