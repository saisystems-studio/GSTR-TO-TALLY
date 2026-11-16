import hashlib
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from gst_tally.connector import fingerprint, online
from gst_tally.models import ConnectorEnrollment, LocalTallyAgent

from .models import ConnectorDiscovery, NexusLink

from . import service
from .client import NexusError, call

TICKET_TTL = 600
USAGE_DEDUPE_SECONDS = 60


class AuthThrottle(AnonRateThrottle):
    rate = "30/min"


def fail(exc):
    """Translate a NexusError into a response. Unreachable Nexus never grants access."""
    return Response({"detail": exc.message, "code": exc.code, "errors": exc.errors}, status=exc.status if exc.status != 401 else 401)


def bad(message, code, status=400):
    return Response({"detail": message, "code": code}, status=status)


DISCOVERY_FRESH_SECONDS = 45
META_FIELDS = ("edition", "tss_details", "virtual_user_limit", "license_administrator", "gateway_server", "registered_email", "tss_expiry_date")


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def fresh_discovery(fp, serial=None):
    if not fp:
        return None
    qs = ConnectorDiscovery.objects.filter(enrollment__browser_device=fp, last_seen_at__gte=timezone.now() - timedelta(seconds=DISCOVERY_FRESH_SECONDS))
    if serial:
        qs = ConnectorDiscovery.objects.filter(serial=serial, last_seen_at__gte=timezone.now() - timedelta(minutes=10))
    return qs.order_by("-last_seen_at").first()


def detected_tally(request):
    """Serial/metadata reported by the local connector for this browser; never invented."""
    fp = fingerprint(request)
    disc = fresh_discovery(fp)
    if not disc and settings.DEBUG and request.META.get("REMOTE_ADDR") in ("127.0.0.1", "::1") and not request.META.get("HTTP_X_FORWARDED_FOR"):
        # Local development only: the browser fingerprint is per-origin (localhost:5173 vs :5174), so a connector paired from
        # another dev port is still the same machine's connector. The serial is still the connector's verified Tally read.
        disc = ConnectorDiscovery.objects.filter(last_seen_at__gte=timezone.now() - timedelta(seconds=DISCOVERY_FRESH_SECONDS)).order_by("-last_seen_at").first()
    if disc:
        if disc.tally_reachable and disc.serial:
            return {"detected": True, "tally_serial": disc.serial, "identity": disc.metadata}
        if (disc.metadata or {}).get("_tally_running"):
            return {"detected": False, "code": "tally_license_unavailable",
                    "message": "TallyPrime is running but is not reporting a valid license serial number (it may be in Educational Mode "
                               "or not activated). Activate your TallyPrime license, then click Retry."}
        return {"detected": False, "code": "tally_not_running", "message": "TallyPrime is not running or its serial number could not be verified. Open TallyPrime and retry."}
    agent = LocalTallyAgent.objects.filter(browser_device=fp).order_by("-last_seen_at").first() if fp else None
    if not agent or not online(agent):
        return {"detected": False, "code": "connector_offline", "message": "Connector not running. Start the GSTR2Tally connector and retry."}
    if not agent.tally_reachable or not agent.detected_serial:
        return {"detected": False, "code": "tally_not_running", "message": "TallyPrime is not running or its serial number could not be verified. Open TallyPrime and retry."}
    return {"detected": True, "tally_serial": agent.detected_serial, "identity": agent.identity or {}}


class PublicView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthThrottle]


class ConfigView(PublicView):
    def get(self, request):
        return Response({"enabled": settings.NEXUS_ENABLED, "enforce": settings.NEXUS_ENFORCE, "product": settings.NEXUS_PRODUCT_ID})


class DetectSerialView(PublicView):
    def get(self, request):
        result = detected_tally(request)
        result.pop("identity", None)
        return Response(result)


class RegisterView(PublicView):
    def post(self, request):
        d = request.data
        tally = detected_tally(request)
        if not tally["detected"]:
            return bad(tally["message"], tally["code"], 409)
        # The serial always comes from the verified connector read, never from the client body.
        payload = {"name": str(d.get("name", "")).strip(), "email": str(d.get("email", "")).strip(),
                   "contact_number": str(d.get("contact_number", "")).strip(),
                   "tally_serial": tally["tally_serial"], "product": settings.NEXUS_PRODUCT_ID}
        try:
            return Response(call("POST", "register/", payload), status=201)
        except NexusError as exc:
            if exc.status == 409 or exc.code == "account_exists":
                # Never creates a duplicate and never touches the existing account/license.
                return bad("Existing Account: this Tally Serial Number is already registered. Sign in with your User ID, use Forgot Password, "
                           "or choose Activate User. Registering again cannot replace the existing account or its email address.", "account_exists", 409)
            return fail(exc)


class LoginView(PublicView):
    def post(self, request):
        d = request.data
        device = service.device_block(d)
        try:
            body = call("POST", "auth/login/", {"user_id": str(d.get("user_id", "")).strip(), "password": str(d.get("password", "")), "device": device})
        except NexusError as exc:
            return fail(exc)
        token = body["token"]
        if body.get("must_change_password"):
            ticket = secrets.token_urlsafe(24)
            cache.set(f"nexus-chg:{ticket}", token, TICKET_TTL)
            return Response({"detail": "Choose a new password before continuing.", "code": "password_change_required", "ticket": ticket}, status=403)
        lic = body.get("license") or {}
        problem = service.license_error(lic)
        if problem:
            return Response({"detail": problem[1], "code": problem[0], "license": lic}, status=403)
        return Response({"license": lic, **service.establish(body["account"], token, lic, device["client_device_id"])})


class ChangePasswordView(PublicView):
    def post(self, request):
        d = request.data
        token = cache.get(f"nexus-chg:{d.get('ticket', '')}")
        if not token:
            return bad("This request expired. Sign in again.", "invalid_ticket")
        try:
            call("POST", "auth/change-password/", {"current_password": str(d.get("current_password", "")), "new_password": str(d.get("new_password", ""))}, token)
        except NexusError as exc:
            return fail(exc)
        cache.delete(f"nexus-chg:{d.get('ticket')}")
        return Response({"detail": "Password changed. Sign in again."})


RESET_CODE_COOLDOWN = 60        # seconds between code requests for the same identifier
RESET_CODE_HOURLY_LIMIT = 5     # requests per identifier per hour


class ForgotPasswordView(PublicView):
    """Sends the reset code through Nexus (which generates, stores, expires and emails it). The response is the
    same whether or not the account exists. Throttled here too, and a failed upstream call never counts against
    the customer or reports success."""

    def post(self, request):
        identifier = str(request.data.get("identifier", "")).strip()[:254]
        if not identifier:
            return bad("Enter your registered email address or Tally Serial Number.", "identifier_required", 400)
        who = digest(identifier.lower() + "|" + request.META.get("REMOTE_ADDR", ""))
        cooldown_key, hour_key = f"nexus-fp-cd:{who}", f"nexus-fp-hr:{who}"
        if not cache.add(cooldown_key, int(timezone.now().timestamp()), RESET_CODE_COOLDOWN):
            return Response({"detail": f"Please wait {RESET_CODE_COOLDOWN} seconds before requesting another code.",
                             "code": "resend_too_soon", "retry_after": RESET_CODE_COOLDOWN}, status=429)
        cache.add(hour_key, 0, 3600)
        try:
            count = cache.incr(hour_key)
        except ValueError:
            cache.set(hour_key, 1, 3600)
            count = 1
        if count > RESET_CODE_HOURLY_LIMIT:
            return Response({"detail": "Too many code requests. Please try again later.", "code": "resend_limit", "retry_after": 3600}, status=429)
        try:
            body = call("POST", "auth/forgot-password/", {"identifier": identifier})
        except NexusError as exc:
            cache.delete(cooldown_key)  # nothing was sent: let the customer retry immediately
            cache.set(hour_key, max(count - 1, 0), 3600)
            return fail(exc)
        # Identical neutral answer for every account state; never echo anything that reveals existence.
        return Response({"message": body.get("message") or "If the account exists, a verification code has been emailed to the registered address.",
                         "retry_after": RESET_CODE_COOLDOWN})


class ResetPasswordView(PublicView):
    """Contract: identifier + token (the 6-digit OTP) + new_password. The OTP is verified, spent and all sessions revoked by Nexus."""
    def post(self, request):
        d = request.data
        identifier = str(d.get("identifier", "")).strip()[:254]
        if not identifier:
            return bad("Enter your registered email address or Tally Serial Number.", "identifier_required", 400)
        try:
            return Response(call("POST", "auth/reset-password/", {"identifier": identifier, "token": str(d.get("token", "")).strip(),
                                                                   "new_password": str(d.get("new_password", ""))}))
        except NexusError as exc:
            return fail(exc)


class VerifyOtpView(PublicView):
    def post(self, request):
        d = request.data
        try:
            return Response(call("POST", "auth/verify-otp/", {"identifier": str(d.get("identifier", "")).strip()[:254], "otp": str(d.get("otp", "")).strip()}))
        except NexusError as exc:
            return fail(exc)


class ResendCredentialsView(PublicView):
    """Retry a failed welcome email for an account that never signed in (Nexus enforces the 60 s cooldown and neutral answer)."""
    def post(self, request):
        try:
            return Response(call("POST", "auth/resend-credentials/", {"user_id": str(request.data.get("user_id", "")).strip()}))
        except NexusError as exc:
            return fail(exc)


class ActivateRequestCodeView(PublicView):
    """Activate User step 1: asks Nexus to email a fresh Activation Key to the registered address (no OTP).
    The key is emailed by Nexus only; it never passes through or is stored by GSTR."""
    def post(self, request):
        try:
            return Response(call("POST", "activate/request-key/", {"user_id": str(request.data.get("user_id", "")).strip()[:60]}))
        except NexusError as exc:
            return fail(exc)


class ActivateVerifyView(PublicView):
    """Activate User step 2: verifies user_id + Activation Key with Nexus. The key is forwarded once and never logged/stored."""
    def post(self, request):
        d = request.data
        try:
            return Response(call("POST", "activate/verify/", {"user_id": str(d.get("user_id", "")).strip()[:60], "activation_key": str(d.get("activation_key", "")).strip()[:40]}))
        except NexusError as exc:
            return fail(exc)


class ActivateConfirmView(PublicView):
    def post(self, request):
        d = request.data
        device = service.device_block(d)
        try:
            body = call("POST", "activate/confirm/", {"ticket": str(d.get("ticket", "")), "device": device, "confirm_transfer": bool(d.get("confirm_transfer"))})
        except NexusError as exc:
            return fail(exc)
        lic = body.get("license") or {}
        problem = service.license_error(lic)
        if problem:
            return Response({"detail": problem[1], "code": problem[0], "license": lic}, status=403)
        session = service.establish(body["account"], body["token"], lic, device["client_device_id"])
        if body.get("device_replaced"):
            service.record_device_replacement(body["account"], device)
        return Response({"license": lic, "device_replaced": body.get("device_replaced", False), **session})


def current_link(request):
    link = NexusLink.objects.filter(pk=(request.auth or {}).get("nexus_link"), user=request.user).first() if request.auth else None
    token = service.decrypt(link.token_enc) if link else ""
    return link, token


class SessionView(APIView):
    """Authoritative session/licence poll. Revocation by Nexus ends the local session at once;
    if Nexus is unreachable the user is allowed only until the signed lease_until."""

    def get(self, request):
        link, token = current_link(request)
        if not link:
            return bad("Not a Nexus session.", "nexus_required", 401)
        try:
            nexus = call("GET", "session/", token=token)
        except NexusError as exc:
            if exc.status == 401:
                service.revoke(link, (exc.errors.get("reason") or ["revoked"])[0])
                return fail(exc)
            if exc.code == "nexus_unreachable" and link.lease_until and link.lease_until > timezone.now():
                return Response({"valid": True, "offline": True, "license": link.license, "lease_until": link.lease_until})
            return fail(exc)
        lic = nexus.get("license") or {}
        link.license, link.last_checked_at = lic, timezone.now()
        from django.utils.dateparse import parse_datetime
        if lic.get("lease_until"):
            link.lease_until = parse_datetime(lic["lease_until"])
        link.save(update_fields=["license", "last_checked_at", "lease_until", "updated_at"])
        problem = service.license_error(lic)
        if problem:
            service.revoke(link, problem[0])  # expired/inactive/mismatch: end the local session now; sign in again after renewal
            return Response({"valid": False, "detail": problem[1], "code": problem[0], "license": lic}, status=403)
        return Response({"valid": True, "license": lic})


class RefreshNexusTokenView(APIView):
    def post(self, request):
        link, token = current_link(request)
        if not link:
            return bad("Not a Nexus session.", "nexus_required", 401)
        try:
            new = call("POST", "auth/refresh/", token=token)
        except NexusError as exc:
            if exc.status == 401:
                service.revoke(link, "revoked")
            return fail(exc)
        link.token_enc = service.encrypt(new["token"])
        link.save(update_fields=["token_enc", "updated_at"])
        return Response({"refreshed": True})


class LicenseView(APIView):
    def get(self, request):
        link, token = current_link(request)
        if not link:
            return bad("Not a Nexus session.", "nexus_required", 401)
        try:
            lic = call("GET", "license/status/", token=token)
        except NexusError as exc:
            if exc.status == 401:
                service.revoke(link, "revoked")
            if exc.code == "nexus_unreachable" and link.lease_until and link.lease_until > timezone.now():
                return Response({**link.license, "offline": True})
            return fail(exc)
        link.license = lic
        link.save(update_fields=["license", "updated_at"])
        problem = service.license_error(lic)
        if problem:
            service.revoke(link, problem[0])
        return Response(lic)


class TallyInfoSyncView(APIView):
    """Sends only values the local connector actually reported; nothing is fabricated."""
    FIELDS = {"edition": ("tally_edition", "edition"), "tss_details": ("tss_status", "tally_software_services"),
              "virtual_user_limit": ("virtual_user_limit",), "license_administrator": ("license_administrator",),
              "gateway_server": ("gateway_server",), "registered_email": ("registered_email", "AdminEmailID"),
              "tss_expiry_date": ("tss_expiry_date",)}

    def post(self, request):
        link, token = current_link(request)
        if not link:
            return bad("Not a Nexus session.", "nexus_required", 401)
        disc = fresh_discovery("x", serial=link.nexus_user_id)
        agent = LocalTallyAgent.objects.filter(device__license__customer=request.user).order_by("-last_seen_at").first()
        identity = disc.metadata if disc else ((agent.identity or {}) if agent and online(agent) and agent.tally_reachable else {})
        payload = {}
        for field, keys in self.FIELDS.items():
            value = next((identity[k] for k in keys if identity.get(k) not in (None, "")), None)
            if value is not None:
                payload[field] = int(value) if field == "virtual_user_limit" and str(value).isdigit() else value
        if not payload:
            return bad("No verified Tally details are available from the connector.", "tally_details_unavailable", 409)
        payload["verified_fields"] = list(payload)
        try:
            return Response(call("POST", "tally-info/", payload, token))
        except NexusError as exc:
            return fail(exc)


class UsageOpenView(APIView):
    def post(self, request):
        link, token = current_link(request)
        if not link:
            return bad("Not a Nexus session.", "nexus_required", 401)
        key = f"nexus-open:{link.pk}"
        if not cache.add(key, 1, USAGE_DEDUPE_SECONDS):
            return Response({"recorded": False})
        try:
            return Response(call("POST", "usage/open/", {"app_version": str(request.data.get("app_version", ""))[:32]}, token))
        except NexusError as exc:
            cache.delete(key)
            if exc.status == 401:
                service.revoke(link, "revoked")
            return fail(exc)


class LogoutView(APIView):
    def post(self, request):
        link, token = current_link(request)
        if link and token:
            try:
                call("POST", "auth/logout/", token=token)
            except NexusError:
                pass
            service.revoke(link, "logout")
        return Response(status=204)


class ConnectorPairView(PublicView):
    """Browser proves it holds the enrollment proof shown to it by the connector, binding
    this browser to that enrollment BEFORE any account exists."""

    def post(self, request):
        fp = fingerprint(request)
        row = ConnectorEnrollment.objects.filter(enrollment_id=request.data.get("enrollment_id")).first() if re.fullmatch(r"[0-9a-fA-F-]{36}", str(request.data.get("enrollment_id", ""))) else None
        if (not fp or not row or row.expires_at <= timezone.now() or row.consumed_at or row.owner_id
                or row.proof_hash != digest(request.data.get("proof", "")) or (row.browser_device and row.browser_device != fp)):
            return bad("Connector pairing is invalid or expired. Reopen the connector.", "pairing_invalid")
        row.browser_device = fp
        row.save(update_fields=["browser_device"])
        return Response({"paired": True})


class ConnectorDiscoveryView(PublicView):
    """Connector reports the Tally identity it read locally. Authenticated by the enrollment's poll secret."""
    throttle_classes = []

    def post(self, request):
        d = request.data
        row = ConnectorEnrollment.objects.filter(enrollment_id=d.get("enrollment_id")).first() if re.fullmatch(r"[0-9a-fA-F-]{36}", str(d.get("enrollment_id", ""))) else None
        if not row or row.expires_at <= timezone.now() or row.poll_hash != digest(d.get("poll_secret", "")):
            return bad("Invalid enrollment.", "invalid_enrollment", 401)
        serial = str(d.get("tally_serial", "")).strip()
        reachable = d.get("tally_reachable") is True and bool(re.fullmatch(r"[A-Za-z0-9]{5,30}", serial))
        meta = {k: d[k] for k in META_FIELDS if d.get(k) not in (None, "")}
        if d.get("tally_reachable") is True and not reachable:
            meta["_tally_running"] = True  # Tally answered, but without a valid license serial (e.g. Educational Mode / serial 0)
        ConnectorDiscovery.objects.update_or_create(enrollment=row, defaults={
            "serial": serial if reachable else "", "tally_reachable": reachable, "metadata": meta, "last_seen_at": timezone.now()})
        return Response({"ok": True, "paired": bool(row.browser_device)})
