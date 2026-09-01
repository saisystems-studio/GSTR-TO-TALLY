"""Read Tally's own software-license identity (serial number, edition, Tally
Software Services status, license administrator) -- distinct from this app's
own ``gst_tally.models.License`` (that one governs activation of this
application; this module reads TallyPrime's license).

EXPERIMENTAL, following the same precedent as ``gst_tally_refresh_rate.tdl``
at the repo root: not yet confirmed against a live TallyPrime instance. See
``read_requests.build_license_query_xml`` for the ``$$LicenseInfo`` function
request shape. This module never fabricates a value for a field Tally
did not actually return -- a field Tally's response genuinely omits stays
blank ("") rather than being invented, and the whole read is reported
``license_available: False`` only when NOTHING usable came back (no serial
number at all), so a partial response (e.g. serial + edition but no admin
field) is still shown instead of being discarded.
"""
import logging
from xml.etree import ElementTree as ET

from django.conf import settings

from .client import TallyClient, TallyConnectionError
from .read_requests import build_license_query_xml

logger = logging.getLogger(__name__)

UNAVAILABLE = "TALLY_LICENSE_DATA_UNAVAILABLE"
LICENSE_FIELDS = ("SerialNumber", "IsGold", "IsSilver", "IsEducationalMode", "IsLicensedMode", "AdminEmailID")


def _unavailable(reason):
    logger.warning("[TALLY_LICENSE] license read unavailable: %s", reason)
    return {"license_available": False, "serial_number": "", "edition": "",
            "tally_software_services": "", "license_administrator": "",
            "license_verified": False, "license_error": UNAVAILABLE, "message": reason}


def _truthy(value):
    return str(value or "").strip().casefold() in {"yes", "true", "1"}


def _normalize_tss(value):
    normalized = str(value or "").strip()
    folded = normalized.casefold()
    if folded in {"yes", "true", "1", "active", "valid"}:
        return "Active"
    if folded in {"no", "false", "0", "inactive", "disabled"}:
        return "Inactive"
    if folded in {"expired", "expire"}:
        return "Expired"
    if folded in {"unavailable", "unknown"}:
        return "Unavailable"
    return normalized


def _read_license_info_param(client, param):
    request = build_license_query_xml(param)
    request_xml = request.decode("utf-8", "replace") if isinstance(request, bytes) else str(request or "")
    print("=== TALLY LICENSE REQUEST ===")
    print(request_xml)
    raw = client.post(request)
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    print("=== TALLY LICENSE RAW RESPONSE ===")
    print(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        logger.warning("[TALLY_LICENSE] invalid XML from Tally for %s. Raw: %s", param, text[:1000])
        return "", text, False
    status = (root.findtext(".//HEADER/STATUS") or "").strip()
    if status != "1":
        logger.warning("[TALLY_LICENSE] unsuccessful Tally status for %s. Status: %s Raw: %s", param, status, text[:1000])
        return "", text, False
    result = (root.findtext(".//DATA/RESULT") or "").strip()
    print("=== TALLY LICENSE PARSED RESULT ===")
    print(result)
    return result, text, True


def read_tally_license(client=None):
    """Return the normalized license shape:

    ``{license_available, serial_number, edition, tally_software_services,
    license_administrator, license_verified, license_error}``

    Never raises -- every failure path (Tally disabled/unreachable, invalid
    XML, no license data at all) returns the same ``license_available: False``
    shape so the caller always has one contract to branch on. ``license_verified``
    is always ``None``/``False`` here -- deciding whether the fetched identity
    is *accepted* for this company is services.tally_license's job, not this
    reader's.
    """
    if settings.TALLY_DRY_RUN:
        return _unavailable("Tally dry run is enabled; no request was sent to Tally.")
    active_client = client or TallyClient()
    try:
        values = {}
        raw_responses = {}
        statuses = {}
        values["SerialNumber"], raw_responses["SerialNumber"], statuses["SerialNumber"] = _read_license_info_param(active_client, "SerialNumber")
        if not statuses.get("SerialNumber"):
            return _unavailable("Tally returned a response that could not be parsed.")
        if not values.get("SerialNumber", ""):
            logger.warning("[TALLY_LICENSE] Tally response had no serial number. Raw: %s", raw_responses.get("SerialNumber", "")[:1000])
            return _unavailable("Tally did not return a license serial number for the active instance.")
        for param in LICENSE_FIELDS:
            if param == "SerialNumber":
                continue
            values[param], raw_responses[param], statuses[param] = _read_license_info_param(active_client, param)
    except TallyConnectionError as exc:
        return _unavailable(f"Could not reach Tally to read license information ({exc.code}).")
    serial = values.get("SerialNumber", "")
    administrator = values.get("AdminEmailID", "")
    is_gold = values.get("IsGold", "")
    is_silver = values.get("IsSilver", "")
    is_educational = values.get("IsEducationalMode", "")
    is_licensed = values.get("IsLicensedMode", "")
    edition = "Gold" if _truthy(is_gold) else "Silver" if _truthy(is_silver) else "Educational" if _truthy(is_educational) else ""
    tally_software_services = _normalize_tss(is_licensed)
    print("serial_number =", serial)
    print("edition =", edition)
    print("tss =", is_licensed)
    print("license_administrator =", administrator)
    return {"license_available": True, "serial_number": serial, "edition": edition,
            "tally_software_services": tally_software_services, "license_administrator": administrator,
            "license_verified": None, "license_error": None, "message": ""}
