import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework_simplejwt.tokens import RefreshToken

from .models import NexusLink

User = get_user_model()


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("nexus-token:" + settings.SECRET_KEY).encode()).digest()))


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value):
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def device_block(data):
    d = data.get("device") or {}
    return {"client_device_id": str(d.get("client_device_id", ""))[:64],
            "fingerprint": str(d.get("fingerprint", ""))[:256],
            "device_name": str(d.get("device_name", ""))[:100],
            "app_version": str(d.get("app_version", ""))[:32]}


def license_error(lic):
    """Map a Nexus licence state to (code, message); None when authorised."""
    if lic.get("authorized"):
        return None
    msgs = {"pending": ("activation_required", "Activation required. Your license has not been activated yet."),
            "expired": ("license_expired", "Your license has expired. Please renew."),
            "cancelled": ("license_inactive", "Your license is inactive."),
            "inactive": ("license_inactive", "Your license is inactive."),
            "device_not_enrolled": ("activation_required", "This device is not activated. Use Activate User."),
            "device_not_authorized": ("device_mismatch", "Your license is active on another device.")}
    return msgs.get(lic.get("state"), ("license_inactive", "License is not authorised."))


def establish(account, token, lic, device_id):
    """Create/refresh the local user + NexusLink and return a local JWT pair.
    Callers must already have confirmed lic['authorized'] with Nexus."""
    serial = str(account["user_id"])
    user, created = User.objects.get_or_create(username=f"nexus_{serial}", defaults={"email": account.get("email", "")})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    lease = parse_datetime(lic["lease_until"]) if lic.get("lease_until") else None
    link, _ = NexusLink.objects.update_or_create(nexus_user_id=serial, client_device_id=device_id, defaults={
        "user": user, "token_enc": encrypt(token), "license": lic,
        "lease_until": lease, "last_checked_at": timezone.now(), "revoked_reason": ""})
    refresh = RefreshToken.for_user(user)
    refresh["nexus_link"] = link.pk  # binds this local session to this device's Nexus session
    return {"access": str(refresh.access_token), "refresh": str(refresh),
            "user": {"id": user.id, "username": user.username, "email": user.email,
                     "phone_number": account.get("contact_number", ""), "tally_serial": serial}}


def revoke(link, reason):
    link.revoked_reason, link.token_enc = reason, ""
    link.save(update_fields=["revoked_reason", "token_enc", "updated_at"])


def record_device_replacement(account, device):
    """Nexus has confirmed a same-account laptop replacement: end every other device's local session
    immediately (not only at the next 5-minute poll) and queue the completion event for Nexus."""
    from django.db import transaction
    from gst_tally.services.device_transfer import queue_nexus_transfer_event
    serial = str(account["user_id"])
    with transaction.atomic():
        old_links = list(NexusLink.objects.filter(nexus_user_id=serial).exclude(client_device_id=device["client_device_id"]))
        old_ids = [link.client_device_id for link in old_links]
        for link in old_links:
            revoke(link, "device_replaced")
        user = User.objects.get(username=f"nexus_{serial}")
        queue_nexus_transfer_event(serial, user, old_ids, device.get("device_name", ""), device.get("client_device_id", ""))
