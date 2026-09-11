"""Resolve and bind a Tally license identity (serial number + administrator)
to an application company GSTIN. The backend is the sole authority on
match/mismatch -- the frontend only ever displays what this returns.
"""
from gst_tally.models import CompanyDetails, TallyCompanyMapping
from gst_tally.tally.license_reader import read_tally_license
from gst_tally.tally.odbc import normalize_company_name

MISMATCH = "LICENSE_IDENTITY_MISMATCH"
FIELD_ERRORS = {
    "serial_number": ("TALLY_LICENSE_SERIAL_UNAVAILABLE", "Tally license serial number could not be read from the active Tally instance."),
    "edition": ("TALLY_LICENSE_EDITION_UNAVAILABLE", "Tally license edition could not be read from the active Tally instance."),
    "tally_software_services": ("TALLY_LICENSE_TSS_UNAVAILABLE", "Tally Software Services status could not be read from the active Tally instance."),
    "license_administrator": ("TALLY_LICENSE_ADMINISTRATOR_UNAVAILABLE", "Tally License Administrator could not be read from the active Tally instance."),
}


def _missing_required_field(reading):
    for field, (code, message) in FIELD_ERRORS.items():
        if not str(reading.get(field, "") or "").strip():
            return {**reading, "license_verified": False, "license_error": code, "message": message}
    return None


def _verified(reading):
    return {**reading, "license_available": True, "license_verified": True,
            "license_error": "", "message": "License verified successfully."}


def _resolve_batch_company_gstin(batch):
    """The GSTIN Company Verification (services/company_verification.py)
    already resolved for this batch, so License Verification never has to
    ask the frontend for it again. Checked in order -- the first real value
    wins:

    1. CompanyDetails, the record Company Verification itself persisted, for
       the exact Tally company name that batch was verified against.
    2. batch.company_details["gstin"] -- the GSTIN already attached to this
       batch's resolved company details (e.g. from resolve_batch_company).
    3. batch.company_gstin -- the import batch's own company GSTIN field.
    4. TallyCompanyMapping -- a GSTIN this same Tally company name was bound
       to on an earlier successful verification.
    """
    selected_name = str((batch.company_details or {}).get("selected_tally_company") or "").strip()

    if selected_name:
        record = (CompanyDetails.objects.filter(company_name=normalize_company_name(selected_name))
                  .order_by("-verified_at").first())
        if record and record.gstin:
            return str(record.gstin).strip().upper()

    saved_gstin = (batch.company_details or {}).get("gstin")
    if saved_gstin:
        return str(saved_gstin).strip().upper()

    if batch.company_gstin:
        return str(batch.company_gstin).strip().upper()

    if selected_name:
        mapping = TallyCompanyMapping.objects.filter(tally_company_name=selected_name).order_by("-updated_at").first()
        if mapping and mapping.gstin:
            return str(mapping.gstin).strip().upper()

    return ""


def verify_batch_license(batch, client=None):
    reading = read_tally_license(client)
    if not reading["license_available"]:
        return reading

    missing = _missing_required_field(reading)
    if missing:
        return missing

    company_gstin = _resolve_batch_company_gstin(batch)
    if not company_gstin:
        return {**reading, "license_verified": False, "license_error": "COMPANY_GSTIN_REQUIRED"}

    mapping, _ = TallyCompanyMapping.objects.get_or_create(
        gstin=company_gstin,
        defaults={"tally_company_name": "", "company_details": {}, "status": ""},
    )
    if not mapping.license_serial:
        # First time this company GSTIN is being verified against a Tally
        # license: bind this pair as the accepted identity going forward.
        mapping.license_serial = reading["serial_number"]
        mapping.license_administrator = reading["license_administrator"]
        mapping.save(update_fields=["license_serial", "license_administrator", "updated_at"])
        return _verified(reading)

    matches = (mapping.license_serial == reading["serial_number"]
               and mapping.license_administrator == reading["license_administrator"])
    if matches:
        return _verified(reading)

    return {**reading, "license_verified": False, "license_error": MISMATCH,
            "expected_serial": mapping.license_serial,
            "expected_administrator": mapping.license_administrator}
