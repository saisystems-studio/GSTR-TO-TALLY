import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from gst_tally.auth_service import secret_hash
from gst_tally.models import (
    LicensedDevice,
    LicenseAuditLog,
    ProductLicense,
    TallyCompanyMapping,
)
from gst_tally.services.party_lookup import normalize_gstin
from gst_tally.tally.connection import step3_connection_check
from gst_tally.tally.license_reader import read_tally_license

logger = logging.getLogger(__name__)


def _debug_log_verification(result, *, device_fingerprint=""):
    """Development-only trace of a license verification outcome. Gated on
    DEBUG (never runs against a production settings module) and only ever
    reads fields already present on `result` plus a couple of cheap by-pk
    lookups (activation key/activated_at, and the device rows for this
    license) the failure/success payloads don't already carry -- no extra
    query happens outside DEBUG."""
    if not settings.DEBUG:
        return
    license_obj = ProductLicense.objects.filter(pk=result.get("license_id")).only(
        "display_activation_key", "display_activation_key_suffix", "activated_at", "purchase_date", "allowed_devices",
    ).first()
    active_devices = list(LicensedDevice.objects.filter(
        license_id=result.get("license_id"), status=LicensedDevice.ACTIVE,
    ).only("device_fingerprint", "device_name")) if license_obj else []
    registered_device = (result.get("device") or {})
    registered_fingerprint = registered_device.get("device_fingerprint", "") or (active_devices[0].device_fingerprint if active_devices else "")
    registered_name = registered_device.get("device_name", "") or result.get("registered_device", "") or (active_devices[0].device_name if active_devices else "")
    logger.debug(
        "license verification: license_id=%s activation_key=%s status=%s activated_at=%s start_date=%s "
        "expiry_date=%s licensed_gstin=%s registered_tally_serial=%s detected_tally_serial=%s "
        "company_gstin=%s allowed_devices=%s active_device_count=%s current_device_fingerprint=%s "
        "registered_device_fingerprint=%s registered_device_name=%s registered_device_id=%s device_match=%s "
        "device_request_id=%s device_status=%s verification_result=%s",
        result.get("license_id"),
        product_license_activation_key(license_obj) if license_obj else "",
        result.get("status", ""),
        license_obj.activated_at if license_obj else None,
        license_obj.purchase_date if license_obj else None,
        result.get("expiry_date"),
        result.get("licensed_gstin", ""),
        result.get("registered_tally_serial", ""),
        result.get("detected_tally_serial", ""),
        result.get("current_company_gstin", ""),
        license_obj.allowed_devices if license_obj else "",
        len(active_devices),
        device_fingerprint,
        registered_fingerprint,
        registered_name,
        (result.get("device") or {}).get("id", "") or (active_devices[0].id if active_devices else ""),
        bool(device_fingerprint) and device_fingerprint == registered_fingerprint,
        result.get("device_request_id", ""),
        registered_device.get("status", ""),
        result.get("verification_result", ""),
    )


def _debug_log_company_verification(batch, *, source_file_gstin, company_source, current_tally_company_name,
                                     current_tally_company_gstin, company_verified):
    """Development-only trace of Step 3 Company Verification for one exact
    import batch -- makes it immediately clear whether a failure is a source
    GSTIN extraction gap (source_company_gstin blank/"Not Found"), was
    resolved via the current-Tally-company fallback (company_source =
    CURRENT_TALLY_FALLBACK), or is a genuine mismatch against the currently
    open Tally company. Gated on DEBUG, same as _debug_log_verification."""
    if not settings.DEBUG:
        return
    logger.debug(
        "company verification: batch_id=%s return_type=%s uploaded_filename=%s "
        "source_company_gstin=%s source_company_name=%s company_source=%s "
        "current_tally_company_name=%s current_tally_company_gstin=%s company_verified=%s",
        batch.id, batch.gst_return_type, batch.file_name,
        source_file_gstin or "-", (batch.company_details or {}).get("company_name", "") or "-",
        company_source or batch.company_resolution_status or "Not Found",
        current_tally_company_name or "-", current_tally_company_gstin or "-",
        company_verified,
    )


LICENSE_VERIFIED = "LICENSE_VERIFIED"
# First-activation validity window. Applied exactly once (see the PENDING
# branch in verify_license_snapshot below) -- renewals extend expiry_date
# through the separate Super Admin "Extend License" flow
# (superadmin/views.py), which never touches this constant or the original
# activated_at/purchase_date.
LICENSE_VALIDITY_DAYS = 365


def product_license_activation_key(obj):
    if not obj:
        return ""
    return obj.display_activation_key or (f"...{obj.display_activation_key_suffix}" if obj.display_activation_key_suffix else "")


def product_license_days_remaining(obj):
    if not obj or not obj.expiry_date:
        return None
    return (obj.expiry_date - timezone.localdate()).days


def product_license_status(obj):
    if not obj:
        return "NEEDS_SETUP"
    if obj.status in (ProductLicense.SUSPENDED, ProductLicense.REVOKED, ProductLicense.PENDING):
        return obj.status
    if not obj.expiry_date:
        return "NEEDS_SETUP"
    days = product_license_days_remaining(obj)
    if days < 0:
        return ProductLicense.EXPIRED
    if days <= 30:
        return "EXPIRING_SOON"
    return ProductLicense.ACTIVE


def hash_activation_key(raw_key):
    return secret_hash(str(raw_key or ""))


def normalize_tally_serial(value):
    return str(value or "").strip()


def _failure(code, license_obj=None, message="", **extra):
    payload = {
        "ready": False,
        "ready_for_master_preparation": False,
        "verification_result": code,
        "license_available": code not in {"LICENSE_NOT_FOUND", "TALLY_LICENSE_DATA_UNAVAILABLE"},
        "license_verified": False,
        "device_authorized": False,
        "license_error": code,
        "license_error_detail": extra.get("license_error_detail", message or code),
        "serial_number": extra.get("serial_number", extra.get("detected_tally_serial", "")),
        "edition": extra.get("edition", extra.get("tally_edition", "")),
        "tally_software_services": extra.get("tally_software_services", extra.get("tss_status", "")),
        "license_administrator": extra.get("license_administrator", ""),
        "message": message or code,
    }
    if license_obj:
        payload.update(
            license_id=license_obj.id,
            licensed_gstin=license_obj.licensed_gstin,
            registered_tally_serial=license_obj.licensed_tally_serial,
            expiry_date=license_obj.expiry_date,
            status=license_obj.status,
        )
    payload.update(extra)
    return payload


def _success(license_obj, device, **extra):
    return {
        "ready": True,
        "ready_for_master_preparation": True,
        "verification_result": LICENSE_VERIFIED,
        "license_available": True,
        "license_verified": True,
        "device_authorized": True,
        "license_error": "",
        "license_error_detail": "",
        "serial_number": extra.get("detected_tally_serial", ""),
        "edition": extra.get("tally_edition", ""),
        "tally_software_services": extra.get("tss_status", ""),
        "message": "License verified successfully.",
        "license_id": license_obj.id,
        "licensed_gstin": license_obj.licensed_gstin,
        "registered_tally_serial": license_obj.licensed_tally_serial,
        "expiry_date": license_obj.expiry_date,
        "status": license_obj.status,
        "device": {
            "id": device.id,
            "device_fingerprint": device.device_fingerprint,
            "device_name": device.device_name,
            "status": device.status,
            "first_seen": device.first_seen,
            "last_seen": device.last_seen,
            "app_version": device.app_version,
        },
        **extra,
    }


def record_license_audit(
    license_obj,
    event_type,
    *,
    old_value=None,
    new_value=None,
    device_fingerprint="",
    detected_tally_serial="",
    current_company_gstin="",
    ip_address="",
    created_by=None,
):
    return LicenseAuditLog.objects.create(
        license=license_obj,
        event_type=event_type,
        old_value=old_value,
        new_value=new_value,
        device_fingerprint=str(device_fingerprint or "")[:128],
        detected_tally_serial=normalize_tally_serial(detected_tally_serial)[:64],
        current_company_gstin=normalize_gstin(current_company_gstin),
        ip_address=str(ip_address or "")[:45],
        created_by=created_by,
    )


def _license_from_key_or_id(activation_key=None, license_id=None, user=None, licensed_gstin=""):
    qs = ProductLicense.objects.select_for_update().select_related("customer")
    if activation_key:
        return qs.filter(activation_key_hash=hash_activation_key(activation_key)).first()
    if license_id:
        qs = qs.filter(pk=license_id)
        if user and getattr(user, "is_authenticated", False):
            qs = qs.filter(customer=user)
        return qs.first()
    if user and getattr(user, "is_authenticated", False):
        base = qs.filter(customer=user).exclude(status=ProductLicense.REVOKED)
        licensed_gstin = normalize_gstin(licensed_gstin)
        if licensed_gstin:
            # Prefer the license actually registered to the currently-open
            # Tally company -- a customer may hold separate licenses for
            # more than one GSTIN. But if none matches, fall back to the
            # customer's own license anyway rather than returning None here:
            # a hard filter that finds nothing makes a genuine GSTIN
            # mismatch look identical to "no license at all" to the caller
            # (LICENSE_NOT_FOUND instead of the correct, more specific
            # COMPANY_GSTIN_MISMATCH -- see verify_license_snapshot, which
            # is what actually compares this license's licensed_gstin
            # against the detected one and decides which error applies).
            matched = base.filter(licensed_gstin=licensed_gstin).order_by("-last_verified_at", "-created_at").first()
            if matched:
                return matched
        return base.order_by("-last_verified_at", "-created_at").first()
    return None


def _license_for_serial(user, serial):
    """The one ProductLicense identity for this exact (user, Tally serial)
    pair -- strict, no fallback to any other serial. One purchased Tally
    license = one serial number, and a different serial is always a
    different ProductLicense identity (never reused), even for the same
    user/company -- company GSTIN is verified separately against the uploaded
    source by pre_import_security_check."""
    serial = normalize_tally_serial(serial)
    if not (user and getattr(user, "is_authenticated", False) and serial):
        return None
    return (ProductLicense.objects.select_for_update().select_related("customer")
            .filter(customer=user, licensed_tally_serial=serial)
            .exclude(status=ProductLicense.REVOKED)
            .order_by("-last_verified_at", "-created_at").first())


def _record_company_mapping(*, gstin, detected_serial, company_name):
    """Keep the company mapping as display/history data, never authorization."""
    gstin = normalize_gstin(gstin)
    detected_serial = normalize_tally_serial(detected_serial)
    if not gstin:
        return
    mapping, _ = TallyCompanyMapping.objects.get_or_create(
        gstin=gstin,
        defaults={
            "license_serial": detected_serial,
            "tally_company_name": str(company_name or "").strip(),
            "status": "ACTIVE",
        },
    )
    update_fields = []
    if company_name and mapping.tally_company_name != str(company_name).strip():
        mapping.tally_company_name = str(company_name).strip()
        update_fields.append("tally_company_name")
    if update_fields:
        mapping.save(update_fields=[*update_fields, "updated_at"])


def _recover_product_license(user, detected_serial):
    """Self-heals a ProductLicense row lost to a DB reset/deletion. Only
    triggers when the user has NO ProductLicense row at all -- an existing
    row (even one that's incomplete, e.g. blank licensed_tally_serial) is an
    administrative matter and is never silently duplicated. Guards against
    stealing another account's Tally installation, and against fabricating a
    license for an account with no real commercial entitlement (the exact
    same Subscription check already enforced on every gst-tally request --
    see subscriptions.middleware.SubscriptionEnforcementMiddleware).

    licensed_gstin is deliberately left blank: GSTIN authorization is checked
    separately against the uploaded source and current Tally company, not
    through this field. status stays PENDING so the existing
    PENDING -> ACTIVE transition in verify_license_snapshot computes
    activation/expiry dates exactly as it does for any first activation.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    detected_serial = normalize_tally_serial(detected_serial)
    if not detected_serial:
        return None
    if ProductLicense.objects.filter(customer=user).exists():
        return None
    if (ProductLicense.objects.filter(licensed_tally_serial=detected_serial)
            .exclude(customer=user).exclude(status=ProductLicense.REVOKED).exists()):
        logger.warning("[PRODUCT_LICENSE] serial %s is already registered to a different account; refusing to recreate for user_id=%s",
                        detected_serial, user.id)
        return None
    from subscriptions.services import check_request_block
    if check_request_block(user) is not None:
        logger.info("[PRODUCT_LICENSE] user_id=%s has no active subscription entitlement; refusing to auto-recreate a license", user.id)
        return None
    license_obj = ProductLicense.objects.create(
        customer=user,
        activation_key_hash=hash_activation_key(f"auto-recovered:{user.id}:{detected_serial}:{uuid.uuid4()}"),
        display_activation_key="AUTO-RECOVERED",
        licensed_tally_serial=detected_serial,
        status=ProductLicense.PENDING,
    )
    logger.info("PRODUCT LICENSE RECREATED license_id=%s user_id=%s serial=%s", license_obj.id, user.id, detected_serial)
    return license_obj


def _status_code(license_obj, today):
    if license_obj.status == ProductLicense.SUSPENDED:
        return "LICENSE_SUSPENDED"
    if license_obj.status == ProductLicense.REVOKED:
        return "LICENSE_REVOKED"
    if license_obj.status != ProductLicense.PENDING and not license_obj.expiry_date:
        return "LICENSE_EXPIRY_NOT_CONFIGURED"
    if license_obj.expiry_date and today > license_obj.expiry_date:
        if license_obj.status != ProductLicense.EXPIRED:
            license_obj.status = ProductLicense.EXPIRED
            license_obj.save(update_fields=["status", "updated_at"])
            record_license_audit(license_obj, "LICENSE_EXPIRED")
        return "LICENSE_EXPIRED"
    if license_obj.status not in {ProductLicense.PENDING, ProductLicense.ACTIVE, ProductLicense.EXPIRING}:
        return "LICENSE_INVALID"
    return ""


@transaction.atomic
def verify_license_snapshot(
    *,
    activation_key=None,
    license_id=None,
    user=None,
    license_obj=None,
    device_fingerprint="",
    device_name="",
    windows_version="",
    app_version="",
    detected_tally_serial="",
    tally_edition="",
    tss_status="",
    license_administrator="",
    current_company_name="",
    current_company_gstin="",
    state="",
    financial_year="",
    tally_snapshot=None,
    company_snapshot=None,
    ip_address="",
    source="startup",
):
    today = timezone.localdate()
    now = timezone.now()
    if license_obj is None:
        # A caller that already resolved (or self-healed) the exact license
        # row -- e.g. pre_import_security_check's serial-first lookup/recovery
        # -- passes it directly so it's never re-resolved here by a
        # potentially different, GSTIN-based rule.
        license_obj = _license_from_key_or_id(activation_key=activation_key, license_id=license_id, user=user,
                                              licensed_gstin=current_company_gstin)
    if not license_obj:
        return _failure("LICENSE_NOT_FOUND", message="No matching product license was found.",
                        detected_tally_serial=detected_tally_serial or (tally_snapshot or {}).get("serial_number", ""),
                        tally_edition=tally_edition or (tally_snapshot or {}).get("edition", ""),
                        tss_status=tss_status or (tally_snapshot or {}).get("tally_software_services", ""),
                        license_administrator=license_administrator or (tally_snapshot or {}).get("license_administrator", ""),
                        current_company_gstin=normalize_gstin(current_company_gstin or (company_snapshot or {}).get("company_gstin", "")))

    status_code = _status_code(license_obj, today)
    if status_code:
        return _failure(status_code, license_obj, message=status_code)

    detected_serial = normalize_tally_serial(detected_tally_serial or (tally_snapshot or {}).get("serial_number"))
    registered_serial = normalize_tally_serial(license_obj.licensed_tally_serial)
    current_gstin = normalize_gstin(current_company_gstin or (company_snapshot or {}).get("company_gstin") or (company_snapshot or {}).get("gstin"))
    device_fingerprint = str(device_fingerprint or "").strip()

    if not registered_serial:
        return _failure("PRODUCT_LICENSE_NOT_CONFIGURED", license_obj, detected_tally_serial=detected_serial,
                        current_company_gstin=current_gstin,
                        message="No registered Tally Serial is configured for this product license.")
    if not detected_serial:
        return _failure("TALLY_LICENSE_DATA_UNAVAILABLE", license_obj, detected_tally_serial=detected_serial,
                        current_company_gstin=current_gstin,
                        license_error_detail=(tally_snapshot or {}).get("license_error_detail", ""),
                        message=(tally_snapshot or {}).get("message") or "Connected to Tally, but license information could not be read.")
    if detected_serial != registered_serial:
        record_license_audit(
            license_obj, "TALLY_SERIAL_MISMATCH",
            old_value={"registered_tally_serial": registered_serial},
            new_value={"detected_tally_serial": detected_serial},
            device_fingerprint=device_fingerprint,
            detected_tally_serial=detected_serial,
            current_company_gstin=current_gstin,
            ip_address=ip_address,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        return _failure("TALLY_SERIAL_MISMATCH", license_obj, detected_tally_serial=detected_serial,
                        current_company_gstin=current_gstin,
                        message="This GSTR 2 Tally license is registered to another Tally installation.")
    # The product license authorizes the Tally installation by serial. The
    # currently open company's GSTIN is checked against the uploaded source
    # GSTIN by pre_import_security_check; it is not a one-company entitlement
    # stored on ProductLicense. One serial may therefore serve many companies.
    gstin_authorized = bool(current_gstin)
    gstin_error_code = audit_event = "COMPANY_GSTIN_MISMATCH"
    if not gstin_authorized:
        record_license_audit(
            license_obj, audit_event,
            old_value={"licensed_gstin": normalize_gstin(license_obj.licensed_gstin)},
            new_value={"current_company_gstin": current_gstin},
            device_fingerprint=device_fingerprint,
            detected_tally_serial=detected_serial,
            current_company_gstin=current_gstin,
            ip_address=ip_address,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        message = ("This company's GSTIN is already licensed under a different Tally installation."
                   if gstin_error_code == "GSTIN_LICENSE_SERIAL_MISMATCH"
                   else "Current Tally company GSTIN does not match this license.")
        return _failure(gstin_error_code, license_obj, detected_tally_serial=detected_serial,
                        current_company_gstin=current_gstin, message=message)
    if not device_fingerprint:
        return _failure("DEVICE_FINGERPRINT_REQUIRED", license_obj, detected_tally_serial=detected_serial,
                        current_company_gstin=current_gstin, message="Device verification failed.")

    active_device = LicensedDevice.objects.filter(
        license=license_obj, device_fingerprint=device_fingerprint, status=LicensedDevice.ACTIVE,
    ).first()
    if active_device:
        if LicensedDevice.objects.filter(license=license_obj, status=LicensedDevice.ACTIVE).count() > license_obj.allowed_devices:
            return _failure("DEVICE_LIMIT_REACHED", license_obj, detected_tally_serial=detected_serial,
                            message="Registered devices exceed the configured device limit.")
        active_device.device_name = str(device_name or active_device.device_name)[:255]
        active_device.windows_version = str(windows_version or active_device.windows_version)[:100]
        active_device.app_version = str(app_version or active_device.app_version)[:50]
        active_device.last_seen = now
        active_device.save(update_fields=["device_name", "windows_version", "app_version", "last_seen", "updated_at"])
        logger.debug("LICENSED DEVICE FOUND device_id=%s fingerprint=%s", active_device.id, device_fingerprint)
    else:
        existing_device = LicensedDevice.objects.filter(
            license=license_obj, device_fingerprint=device_fingerprint,
        ).first()
        if existing_device:
            return _failure("DEVICE_NOT_AUTHORIZED", license_obj,
                            detected_tally_serial=detected_serial,
                            message="This device requires administrator authorization.")
        active_count = LicensedDevice.objects.filter(license=license_obj, status=LicensedDevice.ACTIVE).count()
        if active_count >= license_obj.allowed_devices:
            return _failure("DEVICE_LIMIT_REACHED", license_obj,
                            detected_tally_serial=detected_serial,
                            current_company_gstin=current_gstin,
                            message="Device limit reached. Ask your administrator to authorize this device.")
        else:
            active_device = LicensedDevice.objects.create(
                license=license_obj,
                device_fingerprint=device_fingerprint,
                device_name=str(device_name or "")[:255],
                windows_version=str(windows_version or "")[:100],
                app_version=str(app_version or "")[:50],
                status=LicensedDevice.ACTIVE,
                first_seen=now,
                last_seen=now,
            )
            record_license_audit(
                license_obj, "DEVICE_REGISTERED",
                new_value={"device_fingerprint": device_fingerprint, "device_name": active_device.device_name},
                device_fingerprint=device_fingerprint,
                detected_tally_serial=detected_serial,
                current_company_gstin=current_gstin,
                ip_address=ip_address,
                created_by=user if getattr(user, "is_authenticated", False) else None,
            )
            logger.debug("LICENSED DEVICE CREATED device_id=%s fingerprint=%s", active_device.id, device_fingerprint)

    if license_obj.status == ProductLicense.PENDING:
        # First successful activation only -- this branch can only ever run
        # once per license, since status leaves PENDING here and never
        # returns to it. The 365-day validity window is fixed at this exact
        # moment and must never be recalculated on a later
        # login/verification/import -- the `not license_obj.X` guards below
        # (redundant with the PENDING check today, but cheap insurance) are
        # what keep this idempotent if ever reached twice. Renewals
        # (superadmin ProductLicense "Extend License" view) extend
        # expiry_date separately, without ever touching activated_at or
        # purchase_date (the permanent start date).
        license_obj.status = ProductLicense.ACTIVE
        license_obj.activated_at = license_obj.activated_at or now
        if not license_obj.purchase_date:
            license_obj.purchase_date = license_obj.activated_at.date()
        if not license_obj.expiry_date:
            license_obj.expiry_date = license_obj.purchase_date + timedelta(days=LICENSE_VALIDITY_DAYS)
        record_license_audit(
            license_obj, "LICENSE_ACTIVATED", device_fingerprint=device_fingerprint,
            detected_tally_serial=detected_serial, current_company_gstin=current_gstin,
            new_value={
                "start_date": str(license_obj.purchase_date),
                "expiry_date": str(license_obj.expiry_date),
                "tally_serial": license_obj.licensed_tally_serial,
                "status": "SUCCESS",
            },
            ip_address=ip_address, created_by=user if getattr(user, "is_authenticated", False) else None)
    license_obj.last_verified_at = now
    license_obj.save(update_fields=["status", "activated_at", "purchase_date", "expiry_date", "last_verified_at", "updated_at"])

    return _success(
        license_obj,
        active_device,
        detected_tally_serial=detected_serial,
        current_company_gstin=current_gstin,
        current_company_name=str(current_company_name or "")[:255],
        tally_edition=tally_edition,
        tss_status=tss_status,
        license_administrator=license_administrator,
        state=state,
        financial_year=financial_year,
        source=source,
    )


@transaction.atomic
def pre_import_security_check(batch, user, device_fingerprint="", device_name="", ip_address="", client=None):
    """Evaluate all Step 3 checks before registration; retain the legacy API fields."""
    from subscriptions.models import Subscription

    # Serialize capacity checks and first registration across this account.
    subscription = Subscription.objects.select_for_update().filter(user=user).first()
    connection = step3_connection_check(client=client)
    connected = bool(connection.get("read_connected") and connection.get("can_import"))
    source_gstin = normalize_gstin(batch.company_gstin)
    current_gstin = normalize_gstin(connection.get("company_gstin"))
    company_name = connection.get("company_name", "")
    company_match = bool(connected and source_gstin and source_gstin == current_gstin)
    reading = read_tally_license(client=client) if connection.get("read_connected") else {}
    detected = normalize_tally_serial(reading.get("serial_number"))
    licenses = ProductLicense.objects.select_for_update().filter(customer=user).exclude(status=ProductLicense.REVOKED)
    serials = {normalize_tally_serial(value) for value in licenses.values_list("licensed_tally_serial", flat=True)} - {""}
    used = len(serials)
    limit = subscription.allowed_products if subscription else None
    product_allowed = bool(limit is not None and (detected in serials or used < limit))
    license_obj = _license_for_serial(user, detected) or _license_from_key_or_id(user=user, licensed_gstin=source_gstin)
    # Preserve the existing first-registration recovery rule, within capacity.
    # Never register a different serial over an existing product.
    if not license_obj and connected and company_match and reading.get("license_available") and product_allowed:
        license_obj = _recover_product_license(user, detected)
        if license_obj:
            used += 1
    registered = normalize_tally_serial(license_obj.licensed_tally_serial) if license_obj else ""
    serial_match = bool(reading.get("license_available") and detected and registered == detected)
    fingerprint = str(device_fingerprint or "").strip()
    devices = LicensedDevice.objects.filter(license=license_obj) if license_obj else LicensedDevice.objects.none()
    current_device = devices.filter(device_fingerprint=fingerprint).first() if fingerprint else None
    device_used = devices.filter(status=LicensedDevice.ACTIVE).count()
    device_limit = license_obj.allowed_devices if license_obj else None
    authorized = bool(current_device and current_device.status == LicensedDevice.ACTIVE)
    device_limit_allowed = bool(device_limit is not None and (
        device_used <= device_limit if authorized else device_used < device_limit))
    can_register_device = bool(fingerprint and not current_device and device_limit_allowed)
    errors = []

    def fail(code, message):
        if not any(error["code"] == code for error in errors):
            errors.append({"code": code, "message": message})

    if not connected:
        fail("TALLY_NOT_CONNECTED", "Connect to Tally and open the required company.")
    if not source_gstin:
        fail("SOURCE_COMPANY_GSTIN_MISSING", "Identify the company GSTIN from the uploaded return.")
    elif connection.get("read_connected") and not company_match:
        fail("COMPANY_GSTIN_MISMATCH", f"Open the Tally company having GSTIN {source_gstin}.")
    if not registered:
        fail("PRODUCT_LICENSE_NOT_CONFIGURED", "Tally License Not Configured. Ask your administrator to configure the registered serial.")
    if not reading.get("license_available") or not detected:
        fail("TALLY_LICENSE_DATA_UNAVAILABLE", "Unable to read the actual Tally serial. Check the Tally connection and license.")
    elif registered and not serial_match:
        fail("TALLY_SERIAL_MISMATCH", f"Use the registered Tally license {registered}.")
    if limit is None:
        fail("PRODUCT_LIMIT_NOT_CONFIGURED", "Ask your administrator to configure the subscription product limit.")
    elif not product_allowed:
        fail("PRODUCT_LIMIT_REACHED", f"Product Limit Reached: {used}/{limit} used. Contact your administrator.")
    if license_obj:
        status_code = _status_code(license_obj, timezone.localdate())
        if status_code:
            fail(status_code, "Product license is unavailable: " + status_code + ". Contact your administrator.")
    if not fingerprint:
        fail("DEVICE_FINGERPRINT_REQUIRED", "Device identity is unavailable. Reconnect from the registered device.")
    elif not authorized and not can_register_device:
        fail("DEVICE_NOT_AUTHORIZED", "This device requires administrator authorization.")
    if device_limit is not None and not device_limit_allowed:
        fail("DEVICE_LIMIT_REACHED", f"Device Limit Reached: {device_used}/{device_limit} used. Ask your administrator to authorize this device.")

    result = {}
    if not errors:
        result = verify_license_snapshot(
            user=user, license_obj=license_obj, device_fingerprint=fingerprint, device_name=device_name,
            detected_tally_serial=detected, current_company_gstin=current_gstin,
            current_company_name=company_name, tally_snapshot=reading, company_snapshot=connection,
            tally_edition=reading.get("edition", ""), tss_status=reading.get("tally_software_services", ""),
            license_administrator=reading.get("license_administrator", ""),
            state=connection.get("company_state", ""), financial_year=connection.get("financial_year", ""),
            ip_address=ip_address, source="pre_import",
        )
        if not result.get("ready"):
            fail(result.get("license_error", "LICENSE_VERIFICATION_FAILED"), result.get("message", "Verification failed."))
        current_device = devices.filter(device_fingerprint=fingerprint, status=LicensedDevice.ACTIVE).first()
        authorized = bool(current_device)
        device_used = devices.filter(status=LicensedDevice.ACTIVE).count()
    if not authorized and not any(error["code"].startswith("DEVICE_") for error in errors):
        fail("DEVICE_NOT_AUTHORIZED", "Device registration is pending. Resolve the other verification errors and retry.")
    ready = bool(not errors and connected and company_match and serial_match and product_allowed and authorized and device_limit_allowed)
    if ready:
        _record_company_mapping(gstin=current_gstin, detected_serial=detected, company_name=company_name)
    result.update(
        tally_connected=connected, company_verified=company_match,
        company_name=company_name, company_gstin=current_gstin,
        source_file_gstin=source_gstin, current_tally_company_name=company_name,
        current_tally_company_gstin=current_gstin, current_company_gstin=current_gstin,
        registered_tally_serial=registered, detected_tally_serial=detected, serial_number=detected,
        license_available=bool(reading.get("license_available")),
        license_error_detail=reading.get("license_error_detail", ""),
        edition=reading.get("edition", ""), tally_edition=reading.get("edition", ""),
        tally_software_services=reading.get("tally_software_services", ""),
        license_administrator=reading.get("license_administrator", ""),
        license_verified=bool(serial_match and license_obj and not _status_code(license_obj, timezone.localdate())),
        device_authorized=authorized, device_limit_allowed=device_limit_allowed,
        product_allowed=product_allowed,
        company={"name": company_name, "source_gstin": source_gstin, "tally_gstin": current_gstin, "gstin_match": company_match},
        tally_license={"registered_serial": registered, "detected_serial": detected, "match": serial_match},
        product={"allowed": product_allowed, "limit": limit, "used": used},
        device={**result.get("device", {}), "authorized": authorized, "device_name": device_name or fingerprint,
                "device_fingerprint": fingerprint, "limit": device_limit, "used": device_used, "limit_allowed": device_limit_allowed},
        errors=errors, ready=ready, ready_for_master_preparation=ready,
        verification_result=errors[0]["code"] if errors else LICENSE_VERIFIED,
        license_error=errors[0]["code"] if errors else "",
        message=errors[0]["message"] if errors else "Correct company and registered Tally license are currently in use.",
    )
    if license_obj:
        result.update(license_id=license_obj.id, licensed_gstin=license_obj.licensed_gstin,
                      expiry_date=license_obj.expiry_date, status=license_obj.status)
        if errors:
            record_license_audit(license_obj, "PRE_IMPORT_LICENSE_FAILED",
                                 new_value={"errors": errors}, device_fingerprint=fingerprint,
                                 detected_tally_serial=detected, current_company_gstin=current_gstin,
                                 ip_address=ip_address, created_by=user)
    return result
