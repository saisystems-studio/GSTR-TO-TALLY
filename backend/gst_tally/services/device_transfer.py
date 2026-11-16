"""Self-service, owner-confirmed license transfer between laptops."""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from gst_tally.models import DeviceTransfer, LicensedDevice, NexusOutboxEvent, ProductLicense
from gst_tally.services.product_license import _status_code, normalize_tally_serial, record_license_audit

logger = logging.getLogger(__name__)
RATE_LIMIT = 5
RATE_WINDOW = timedelta(minutes=15)
MAX_ATTEMPTS = 8
TRANSFERRED_MESSAGE = ("Your GSTR2Tally license has been transferred to another device. "
                       "This computer is no longer authorized.")


class TransferError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _owned_license(user, serial):
    serial = normalize_tally_serial(serial)
    qs = ProductLicense.objects.filter(customer=user).exclude(status=ProductLicense.REVOKED)
    if serial:
        return qs.filter(licensed_tally_serial=serial).first()
    return qs.order_by("-last_verified_at", "-created_at").first()


def _was_transferred_away(license_obj, fingerprint):
    return DeviceTransfer.objects.filter(license=license_obj, result=DeviceTransfer.SUCCESS,
                                         old_device_fingerprint=fingerprint).exists()


def is_nexus_linked(user):
    return bool(getattr(user, "is_authenticated", False) and user.nexus_links.exists())


def preview(user, fingerprint, device_name="", serial=""):
    """Whether a transfer popup should be offered, and its display data."""
    fingerprint = str(fingerprint or "").strip()
    if is_nexus_linked(user):
        # Nexus-linked customers transfer only through Nexus Activate User.
        return {"transfer_available": False, "reason": "USE_NEXUS_FLOW"}
    license_obj = _owned_license(user, serial)
    if not fingerprint or not license_obj:
        return {"transfer_available": False, "reason": "NO_OWNED_LICENSE"}
    if _status_code(license_obj, timezone.localdate()):
        return {"transfer_available": False, "reason": "LICENSE_NOT_ACTIVE"}
    current = LicensedDevice.objects.filter(license=license_obj, device_fingerprint=fingerprint).first()
    if current and current.status == LicensedDevice.ACTIVE:
        return {"transfer_available": False, "reason": "ALREADY_ACTIVE"}
    # An administratively revoked/pending device is never self-service eligible;
    # only a brand-new device or one previously transferred away is.
    if current and not _was_transferred_away(license_obj, fingerprint):
        return {"transfer_available": False, "reason": "DEVICE_REQUIRES_ADMIN"}
    active = list(LicensedDevice.objects.filter(license=license_obj, status=LicensedDevice.ACTIVE).order_by("-last_seen"))
    if not active:
        return {"transfer_available": False, "reason": "NO_ACTIVE_DEVICE"}
    if len(active) < license_obj.allowed_devices:
        return {"transfer_available": False, "reason": "CAPACITY_AVAILABLE"}
    previous = active[0]
    return {
        "transfer_available": True,
        "customer_name": user.get_full_name() or user.get_username(),
        "tally_serial": license_obj.licensed_tally_serial,
        "license_status": license_obj.status,
        "previous_device_id": previous.id,
        "previous_device_name": previous.device_name or previous.device_fingerprint[:8],
        "current_device_name": device_name or fingerprint[:8],
        "multiple_active": len(active) > 1,
    }


def _log(license_obj, user, result, reason, fingerprint, name, ip, old=None):
    return DeviceTransfer.objects.create(
        license=license_obj, customer=user, result=result, reason=reason,
        tally_serial=license_obj.licensed_tally_serial,
        old_device_fingerprint=old.device_fingerprint if old else "",
        old_device_name=old.device_name if old else "",
        new_device_fingerprint=fingerprint, new_device_name=name, ip_address=ip or "")


def _rate_limited(user):
    since = timezone.now() - RATE_WINDOW
    return DeviceTransfer.objects.filter(customer=user, created_at__gte=since).exclude(
        result=DeviceTransfer.CANCELLED).count() >= RATE_LIMIT


def reauthenticate(user, password):
    return bool(password) and user.has_usable_password() and user.check_password(password)


def cancel(user, fingerprint, device_name="", serial="", ip=""):
    license_obj = _owned_license(user, serial)
    if license_obj:
        _log(license_obj, user, DeviceTransfer.CANCELLED, "USER_CANCELLED", fingerprint, device_name, ip)
    return {"cancelled": True}


def confirm(user, fingerprint, device_name, password, serial="", ip="", windows_version="", app_version=""):
    fingerprint = str(fingerprint or "").strip()
    device_name = str(device_name or "")[:255]
    if is_nexus_linked(user):
        raise TransferError("USE_NEXUS_FLOW", "Use Nexus Activate User to move your license to this laptop.", 409)
    if not fingerprint:
        raise TransferError("DEVICE_FINGERPRINT_REQUIRED", "Device identity is unavailable.")
    owned = _owned_license(user, serial)
    if not owned:
        raise TransferError("LICENSE_ASSIGNED_TO_ANOTHER_ACCOUNT",
                            "License Assigned to Another Account. Please sign in using the registered account "
                            "or contact your administrator.", 403)
    if _rate_limited(user):
        raise TransferError("TRANSFER_RATE_LIMITED", "Too many transfer attempts. Please try again later.", 429)
    if not reauthenticate(user, password):
        _log(owned, user, DeviceTransfer.AUTH_FAILED, "REAUTH_FAILED", fingerprint, device_name, ip)
        raise TransferError("REAUTH_FAILED", "Password verification failed. The transfer was not completed.", 403)

    with transaction.atomic():
        # Row lock on the license serializes concurrent transfers.
        license_obj = ProductLicense.objects.select_for_update().get(pk=owned.pk)
        if license_obj.customer_id != user.id or _status_code(license_obj, timezone.localdate()):
            raise TransferError("LICENSE_NOT_TRANSFERABLE", "This license cannot be transferred.", 403)
        info = preview(user, fingerprint, device_name, license_obj.licensed_tally_serial)
        if not info["transfer_available"]:
            raise TransferError("TRANSFER_NOT_AVAILABLE",
                                "No device transfer is needed or allowed for this device.", 409)
        if info["multiple_active"]:
            raise TransferError("MULTIPLE_ACTIVE_DEVICES", "Several devices are active; contact your administrator.", 409)
        old = LicensedDevice.objects.select_for_update().get(pk=info["previous_device_id"])
        old.status = LicensedDevice.REVOKED
        old.save(update_fields=["status", "updated_at"])
        new, _ = LicensedDevice.objects.get_or_create(
            license=license_obj, device_fingerprint=fingerprint,
            defaults={"device_name": device_name, "windows_version": windows_version[:100], "app_version": app_version[:50]})
        new.status = LicensedDevice.ACTIVE
        new.device_name = device_name or new.device_name
        new.last_seen = timezone.now()
        new.save()
        if LicensedDevice.objects.filter(license=license_obj, status=LicensedDevice.ACTIVE).count() > license_obj.allowed_devices:
            raise TransferError("DEVICE_LIMIT_REACHED", "Device limit exceeded.", 409)  # atomic block rolls back
        transfer = _log(license_obj, user, DeviceTransfer.SUCCESS, "", fingerprint, device_name, ip, old=old)
        record_license_audit(license_obj, "DEVICE_TRANSFERRED",
                             old_value={"device_fingerprint": old.device_fingerprint, "device_name": old.device_name},
                             new_value={"device_fingerprint": fingerprint, "device_name": device_name},
                             device_fingerprint=fingerprint, ip_address=ip, created_by=user)
        event = NexusOutboxEvent.objects.create(event_type="DEVICE_TRANSFER_COMPLETED", payload={
            "customer_id": user.id, "product": getattr(settings, "NEXUS_PRODUCT_ID", "gstr2tally"),
            "tally_serial": license_obj.licensed_tally_serial,
            "old_device": {"id": old.id, "name": old.device_name},
            "new_device": {"id": new.id, "name": new.device_name},
            "transferred_at": transfer.created_at.isoformat(), "transfer_id": transfer.id})
        transaction.on_commit(lambda: deliver_event(event.pk))
    return {"transferred": True, "message": "Device Transfer Successful. This laptop is now activated.",
            "device_id": new.id, "license_id": license_obj.id}


def device_is_transferred_away(user, fingerprint):
    """True when this fingerprint was transferred away and is not active again."""
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        return False
    devices = LicensedDevice.objects.filter(license__customer=user, device_fingerprint=fingerprint)
    if devices.filter(status=LicensedDevice.ACTIVE).exists():
        return False
    return devices.filter(status=LicensedDevice.REVOKED).exists() and DeviceTransfer.objects.filter(
        customer=user, result=DeviceTransfer.SUCCESS, old_device_fingerprint=fingerprint).exists()


def deliver_event(pk):
    """Best-effort delivery; never raises, never affects the committed transfer."""
    from nexus_auth import client
    try:
        with transaction.atomic():
            ev = NexusOutboxEvent.objects.select_for_update().get(pk=pk)
            if ev.status == NexusOutboxEvent.SENT or ev.next_attempt_at > timezone.now():
                return ev.status
            if not (getattr(settings, "NEXUS_ENABLED", False) and getattr(settings, "NEXUS_PRODUCT_KEY", "") and getattr(settings, "NEXUS_EVENT_SIGNING_SECRET", "")):
                return ev.status  # not configured: stays PENDING for later delivery
            try:
                client.call("POST", getattr(settings, "NEXUS_EVENTS_PATH", "events/device-transfer/"),
                            {"event_id": str(ev.event_id), "event_type": ev.event_type, **ev.payload}, sign=True)
                ev.status, ev.sent_at, ev.last_error = NexusOutboxEvent.SENT, timezone.now(), ""
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "status", None) in (404, 405):
                    # Receiving endpoint not deployed yet: keep the event, retry hourly, never mark FAILED.
                    ev.last_error = "Nexus events endpoint not available (HTTP %s)" % exc.status
                    ev.next_attempt_at = timezone.now() + timedelta(hours=1)
                else:
                    ev.attempts += 1
                    ev.last_error = str(exc)[:250]
                    ev.status = NexusOutboxEvent.FAILED if ev.attempts >= MAX_ATTEMPTS else NexusOutboxEvent.PENDING
                    ev.next_attempt_at = timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** ev.attempts))
            ev.save()
            return ev.status
    except Exception:  # noqa: BLE001
        logger.exception("nexus outbox delivery failed pk=%s", pk)
        return "ERROR"


def deliver_due():
    ids = list(NexusOutboxEvent.objects.filter(status=NexusOutboxEvent.PENDING, next_attempt_at__lte=timezone.now())
               .values_list("pk", flat=True)[:100])
    return {pk: deliver_event(pk) for pk in ids}


def queue_nexus_transfer_event(serial, user, old_client_device_ids, new_device_name, new_device_id=""):
    """Outbox entry for a transfer confirmed through the Nexus Activate flow. Shape follows the Nexus contract
    (events/device-transfer/): old_device/new_device each carry id + name."""
    old_ids = list(old_client_device_ids)
    event = NexusOutboxEvent.objects.create(event_type="DEVICE_TRANSFER_COMPLETED", payload={
        "customer_id": user.id, "product": getattr(settings, "NEXUS_PRODUCT_ID", "gstr2tally"),
        "tally_serial": str(serial),
        "old_device": {"id": old_ids[0] if old_ids else "", "name": old_ids[0] if old_ids else "", "client_device_ids": old_ids},
        "new_device": {"id": str(new_device_id or "")[:64], "name": str(new_device_name or "")[:100]},
        "transferred_at": timezone.now().isoformat(), "source": "nexus_activate_confirm"})
    transaction.on_commit(lambda: deliver_event(event.pk))
    return event
