import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from gst_tally.auth_service import secret_hash
from gst_tally.models import (
    DeviceActivationRequest,
    LicensedDevice,
    LicenseAuditLog,
    ProductLicense,
    TallyCompanyMapping,
)
from gst_tally.services.company import resolve_source_company_gstin
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
    user/company -- see _recover_product_license and _authorize_company_gstin
    for how GSTIN/company identity is kept separate from this."""
    serial = normalize_tally_serial(serial)
    if not (user and getattr(user, "is_authenticated", False) and serial):
        return None
    return (ProductLicense.objects.select_for_update().select_related("customer")
            .filter(customer=user, licensed_tally_serial=serial)
            .exclude(status=ProductLicense.REVOKED)
            .order_by("-last_verified_at", "-created_at").first())


def _authorize_company_gstin(*, gstin, detected_serial, company_name):
    """Whether `gstin` may be used under `detected_serial`, delegating GSTIN
    identity to TallyCompanyMapping (never to ProductLicense.licensed_gstin,
    and never to the company name) -- so one valid Tally installation may
    legitimately serve more than one company. First time a GSTIN is seen for
    a serial it is bound (first-time-wins, mirroring the same pattern
    services/tally_license.py::verify_batch_license already uses for this
    same model, just not wired into the live path); a GSTIN already bound to
    a DIFFERENT serial -- whether via an existing TallyCompanyMapping row or
    via a different ProductLicense's own explicit licensed_gstin -- is always
    rejected, regardless of which user/ProductLicense is asking. Company NAME
    is stored for display only and never gates this decision.

    Returns (authorized, error_code, mapping, created).
    """
    gstin = normalize_gstin(gstin)
    detected_serial = normalize_tally_serial(detected_serial)
    company_name = str(company_name or "").strip()
    if not gstin:
        return False, "SOURCE_COMPANY_GSTIN_MISSING", None, False
    if not detected_serial:
        return False, "TALLY_LICENSE_DATA_UNAVAILABLE", None, False
    # A different Tally purchase (any user) may already hold this exact GSTIN
    # as its own explicit activation-key entitlement even if no
    # TallyCompanyMapping row was ever created for it -- that claim wins.
    if ProductLicense.objects.filter(licensed_gstin=gstin).exclude(licensed_tally_serial=detected_serial).exists():
        return False, "GSTIN_LICENSE_SERIAL_MISMATCH", None, False

    mapping, created = TallyCompanyMapping.objects.get_or_create(
        gstin=gstin, defaults={"license_serial": detected_serial, "tally_company_name": company_name, "status": "ACTIVE"},
    )
    if created:
        logger.debug("COMPANY MAPPING CREATED gstin=%s serial=%s", gstin, detected_serial)
        return True, "", mapping, True
    if mapping.license_serial and mapping.license_serial != detected_serial:
        return False, "GSTIN_LICENSE_SERIAL_MISMATCH", mapping, False
    update_fields = []
    if not mapping.license_serial:
        mapping.license_serial = detected_serial
        update_fields.append("license_serial")
    if company_name and mapping.tally_company_name != company_name:
        # Display name only -- a cosmetic difference (trailing comma, branch
        # suffix, year appended) must never fail verification once the GSTIN
        # already matches.
        mapping.tally_company_name = company_name
        update_fields.append("tally_company_name")
    if update_fields:
        mapping.save(update_fields=[*update_fields, "updated_at"])
    logger.debug("COMPANY MAPPING FOUND gstin=%s serial=%s", gstin, detected_serial)
    return True, "", mapping, False


def _recover_product_license(user, detected_serial):
    """Self-heals a ProductLicense row lost to a DB reset/deletion. Only
    triggers when the user has NO ProductLicense row at all -- an existing
    row (even one that's incomplete, e.g. blank licensed_tally_serial) is an
    administrative matter and is never silently duplicated. Guards against
    stealing another account's Tally installation, and against fabricating a
    license for an account with no real commercial entitlement (the exact
    same Subscription check already enforced on every gst-tally request --
    see subscriptions.middleware.SubscriptionEnforcementMiddleware).

    licensed_gstin is deliberately left blank: GSTIN authorization for this
    row happens entirely through TallyCompanyMapping (_authorize_company_gstin),
    not through this field. status stays PENDING so the existing
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


def _create_or_reuse_device_request(license_obj, *, old_device, device_fingerprint, device_name, windows_version, app_version, detected_tally_serial, current_company_gstin):
    request, _ = DeviceActivationRequest.objects.get_or_create(
        license=license_obj,
        requested_device_fingerprint=device_fingerprint,
        status=DeviceActivationRequest.PENDING,
        defaults={
            "old_device": old_device,
            "requested_device_name": device_name[:255],
            "windows_version": windows_version[:100],
            "app_version": app_version[:50],
            "detected_tally_serial": detected_tally_serial[:64],
            "current_company_gstin": current_company_gstin,
        },
    )
    return request


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
    licensed_gstin = normalize_gstin(license_obj.licensed_gstin)
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
    if activation_key:
        # First-ever activation: the license's own pre-assigned GSTIN is the
        # explicit entitlement this exact purchase was sold for (superadmin
        # provisioning / management/commands/seed_product_licenses.py) -- a
        # hard, permanent lock. Never auto-expanded to other companies here.
        gstin_authorized = bool(current_gstin) and current_gstin == licensed_gstin
        gstin_error_code, audit_event = "COMPANY_GSTIN_MISMATCH", "GSTIN_MISMATCH"
    else:
        # Ongoing usage (verify/heartbeat/pre-import): company identity is
        # delegated to TallyCompanyMapping so one valid Tally installation
        # may legitimately serve multiple companies -- see
        # _authorize_company_gstin. Company NAME is never part of this check.
        gstin_authorized, mapping_error, _mapping, _created = _authorize_company_gstin(
            gstin=current_gstin, detected_serial=detected_serial, company_name=current_company_name,
        )
        gstin_error_code = audit_event = mapping_error or "COMPANY_GSTIN_MISMATCH"
    if not gstin_authorized:
        record_license_audit(
            license_obj, audit_event,
            old_value={"licensed_gstin": licensed_gstin},
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
        active_device.device_name = str(device_name or active_device.device_name)[:255]
        active_device.windows_version = str(windows_version or active_device.windows_version)[:100]
        active_device.app_version = str(app_version or active_device.app_version)[:50]
        active_device.last_seen = now
        active_device.save(update_fields=["device_name", "windows_version", "app_version", "last_seen", "updated_at"])
        logger.debug("LICENSED DEVICE FOUND device_id=%s fingerprint=%s", active_device.id, device_fingerprint)
    else:
        active_count = LicensedDevice.objects.filter(license=license_obj, status=LicensedDevice.ACTIVE).count()
        if active_count >= license_obj.allowed_devices:
            old_device = LicensedDevice.objects.filter(license=license_obj, status=LicensedDevice.ACTIVE).order_by("first_seen").first()
            request = _create_or_reuse_device_request(
                license_obj,
                old_device=old_device,
                device_fingerprint=device_fingerprint,
                device_name=str(device_name or ""),
                windows_version=str(windows_version or ""),
                app_version=str(app_version or ""),
                detected_tally_serial=detected_serial,
                current_company_gstin=current_gstin,
            )
            record_license_audit(
                license_obj, "DEVICE_CHANGE_REQUESTED",
                old_value={"device_fingerprint": old_device.device_fingerprint if old_device else ""},
                new_value={"device_fingerprint": device_fingerprint, "request_id": request.id},
                device_fingerprint=device_fingerprint,
                detected_tally_serial=detected_serial,
                current_company_gstin=current_gstin,
                ip_address=ip_address,
                created_by=user if getattr(user, "is_authenticated", False) else None,
            )
            return _failure("DEVICE_LIMIT_REACHED", license_obj, detected_tally_serial=detected_serial,
                            current_company_gstin=current_gstin,
                            registered_device=old_device.device_name if old_device else "",
                            device_request_id=request.id,
                            message="This license is already active on another device.")
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
    connection = step3_connection_check(client=client)
    tally_connected = bool(connection.get("read_connected") and connection.get("can_import"))
    # Two independent identities, never conflated (see verify_license_snapshot
    # for the separate license/serial check): the GSTIN the uploaded GSTR
    # file actually belongs to (source_file_gstin -- the return's own
    # taxpayer/recipient GSTIN, already resolved onto the batch at import
    # time, never a supplier/party GSTIN and never a ProductLicense GSTIN)
    # versus the GSTIN of whichever company is currently open in Tally
    # (current_tally_company_gstin, read live from the connection). Company
    # verification is exactly these two matching -- nothing about a
    # ProductLicense belongs in this comparison.
    current_tally_company_gstin = normalize_gstin(connection.get("company_gstin", ""))
    current_tally_company_name = connection.get("company_name", "")
    company_source = ""
    if tally_connected:
        # Only ever fills a genuine extraction gap (batch.company_gstin still
        # blank, and not merely ambiguous) from the single company currently
        # open in Tally -- never from any party/supplier/invoice-row GSTIN.
        # See services/company.py::resolve_source_company_gstin.
        _resolved_gstin, _resolved_name, company_source = resolve_source_company_gstin(
            batch, current_tally_company_gstin=current_tally_company_gstin,
            current_tally_company_name=current_tally_company_name,
        )
    source_file_gstin = normalize_gstin(batch.company_gstin)
    company_verified = bool(
        tally_connected and source_file_gstin and current_tally_company_gstin
        and source_file_gstin == current_tally_company_gstin
    )
    company_fields = {
        "source_file_gstin": source_file_gstin,
        "current_tally_company_name": current_tally_company_name,
        "current_tally_company_gstin": current_tally_company_gstin,
        "company_source": company_source,
    }
    _debug_log_company_verification(batch, source_file_gstin=source_file_gstin,
                                     company_source=company_source,
                                     current_tally_company_name=current_tally_company_name,
                                     current_tally_company_gstin=current_tally_company_gstin,
                                     company_verified=company_verified)
    if not connection.get("read_connected") or not connection.get("can_import"):
        license_obj = _license_from_key_or_id(user=user, licensed_gstin=source_file_gstin)
        if license_obj:
            record_license_audit(license_obj, "PRE_IMPORT_LICENSE_FAILED", device_fingerprint=device_fingerprint,
                                 current_company_gstin=current_tally_company_gstin, ip_address=ip_address, created_by=user)
        result = _failure("TALLY_NOT_CONNECTED", license_obj, current_company_gstin=current_tally_company_gstin,
                          tally_connected=False, company_verified=False,
                          message="Current Tally connection is not verified.", **company_fields)
        _debug_log_verification(result, device_fingerprint=device_fingerprint)
        return result
    if not company_verified:
        # A live, reachable Tally with the wrong company open (or a source
        # file whose GSTIN couldn't be resolved) must never reach the license
        # check -- license authorization is meaningless until we know this is
        # even the right company's data.
        license_obj = _license_from_key_or_id(user=user, licensed_gstin=source_file_gstin)
        if license_obj:
            record_license_audit(license_obj, "PRE_IMPORT_LICENSE_FAILED", device_fingerprint=device_fingerprint,
                                 current_company_gstin=current_tally_company_gstin, ip_address=ip_address, created_by=user)
        if not source_file_gstin:
            # No comparison was actually possible -- this is a source-file
            # extraction gap, never a real GSTIN mismatch. Reporting
            # COMPANY_GSTIN_MISMATCH here would be misleading (it implies two
            # GSTINs were read and disagreed) and would hide the real,
            # debuggable problem: the uploaded return's own GSTIN.
            code, message = "SOURCE_COMPANY_GSTIN_MISSING", "Unable to identify the company GSTIN from the uploaded return."
        else:
            code = "COMPANY_GSTIN_MISMATCH"
            message = (f"The GSTIN of the company currently open in Tally ({current_tally_company_gstin or '-'}) "
                       f"does not match the uploaded return's company GSTIN ({source_file_gstin}).")
        result = _failure(code, license_obj, current_company_gstin=current_tally_company_gstin,
                          tally_connected=tally_connected, company_verified=False,
                          message=message, **company_fields)
        _debug_log_verification(result, device_fingerprint=device_fingerprint)
        return result

    reading = read_tally_license(client=client)
    detected_serial = normalize_tally_serial(reading.get("serial_number", ""))
    if detected_serial:
        logger.debug("LICENSE SERIAL DETECTED serial=%s user_id=%s", detected_serial, getattr(user, "id", None))
    # ProductLicense identity is the (user, Tally serial) pair -- never the
    # company GSTIN/name (see _license_for_serial). A different serial is
    # always a different license, never reused across installations.
    license_obj = _license_for_serial(user, detected_serial)
    if license_obj:
        logger.debug("PRODUCT LICENSE FOUND license_id=%s user_id=%s serial=%s",
                     license_obj.id, getattr(user, "id", None), detected_serial)
    else:
        # No row for this exact serial. If this user's OWN license for the
        # currently-open company GSTIN exists but is for a different serial,
        # surface that as the specific, actionable TALLY_SERIAL_MISMATCH
        # diagnostic below (registered vs detected serial) rather than a
        # generic "not configured" -- this is a genuine wrong-installation
        # case, not a DB reset.
        diagnostic = (ProductLicense.objects.filter(customer=user, licensed_gstin=current_tally_company_gstin)
                      .exclude(status=ProductLicense.REVOKED)
                      .order_by("-last_verified_at", "-created_at").first()
                      if current_tally_company_gstin else None)
        if diagnostic and normalize_tally_serial(diagnostic.licensed_tally_serial) != detected_serial:
            license_obj = diagnostic
        elif reading.get("license_available") and detected_serial:
            # No existing license anywhere for this user, but Tally itself is
            # genuinely licensed right now -- this is the DB-reset/deleted
            # case: self-heal instead of permanently blocking the user.
            license_obj = _recover_product_license(user, detected_serial)
    reading_fields = {
        "serial_number": reading.get("serial_number", ""),
        "detected_tally_serial": reading.get("serial_number", ""),
        "edition": reading.get("edition", ""),
        "tally_edition": reading.get("edition", ""),
        "tally_software_services": reading.get("tally_software_services", ""),
        "tss_status": reading.get("tally_software_services", ""),
        "license_administrator": reading.get("license_administrator", ""),
        "license_error_detail": reading.get("license_error_detail", ""),
        "tally_connected": tally_connected,
        "company_verified": company_verified,
        "company_name": current_tally_company_name,
        "company_gstin": current_tally_company_gstin,
        **company_fields,
    }
    if not license_obj:
        result = _failure(
            "PRODUCT_LICENSE_NOT_CONFIGURED",
            message="No registered Tally Serial is configured for this product license.",
            **reading_fields,
        )
        _debug_log_verification(result, device_fingerprint=device_fingerprint)
        return result
    if not reading.get("license_available"):
        if license_obj:
            record_license_audit(license_obj, "PRE_IMPORT_LICENSE_FAILED", device_fingerprint=device_fingerprint,
                                 current_company_gstin=current_tally_company_gstin, ip_address=ip_address, created_by=user)
        result = _failure("TALLY_LICENSE_DATA_UNAVAILABLE", license_obj, current_company_gstin=current_tally_company_gstin,
                          message=reading.get("message") or "Connected to Tally, but license information could not be read.",
                          **reading_fields)
        _debug_log_verification(result, device_fingerprint=device_fingerprint)
        return result

    result = verify_license_snapshot(
        user=user,
        license_obj=license_obj,
        device_fingerprint=device_fingerprint,
        device_name=device_name,
        detected_tally_serial=reading.get("serial_number", ""),
        tally_edition=reading.get("edition", ""),
        tss_status=reading.get("tally_software_services", ""),
        license_administrator=reading.get("license_administrator", ""),
        current_company_name=current_tally_company_name,
        current_company_gstin=current_tally_company_gstin,
        state=connection.get("company_state", ""),
        financial_year=connection.get("financial_year", ""),
        tally_snapshot=reading,
        company_snapshot=connection,
        ip_address=ip_address,
        source="pre_import",
    )
    _debug_log_verification(result, device_fingerprint=device_fingerprint)
    if result.get("ready"):
        logger.debug("LICENSE VERIFICATION PASSED license_id=%s user_id=%s gstin=%s serial=%s",
                     license_obj.id, getattr(user, "id", None), current_tally_company_gstin, detected_serial)
    if not result.get("ready"):
        license_obj = ProductLicense.objects.filter(pk=result.get("license_id")).first()
        if license_obj:
            record_license_audit(
                license_obj,
                "PRE_IMPORT_LICENSE_FAILED",
                old_value={"registered_tally_serial": result.get("registered_tally_serial"), "licensed_gstin": result.get("licensed_gstin")},
                new_value={"detected_tally_serial": result.get("detected_tally_serial"), "current_company_gstin": result.get("current_company_gstin")},
                device_fingerprint=device_fingerprint,
                detected_tally_serial=result.get("detected_tally_serial", ""),
                current_company_gstin=result.get("current_company_gstin", ""),
                ip_address=ip_address,
                created_by=user,
            )
    result.update(
        tally_connected=tally_connected,
        company_verified=company_verified,
        company_name=current_tally_company_name,
        company_gstin=current_tally_company_gstin,
        **company_fields,
        ready_for_master_preparation=tally_connected and company_verified and bool(result.get("license_verified")),
    )
    return result
