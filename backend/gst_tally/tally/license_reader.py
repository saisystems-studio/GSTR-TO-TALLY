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
TAG_ALIASES = {
    "SerialNumber": ("SERIALNUMBER", "SERIALNO", "SERIAL", "LICENSESERIALNUMBER"),
    "IsGold": ("ISGOLD",),
    "IsSilver": ("ISSILVER",),
    "IsEducationalMode": ("ISEDUCATIONALMODE", "ISEDUCATIONAL"),
    "IsLicensedMode": ("ISLICENSEDMODE", "TALLYSOFTWARESERVICES", "TSSSTATUS"),
    "AdminEmailID": ("ADMINEMAILID", "LICENSEADMINISTRATOR", "ADMINISTRATOR"),
}


def _unavailable(reason, detail=""):
    logger.warning("[TALLY_LICENSE] license read unavailable: %s detail=%s", reason, detail)
    return {"license_available": False, "serial_number": "", "edition": "",
            "tally_software_services": "", "license_administrator": "",
            "license_verified": False, "license_error": UNAVAILABLE,
            "license_error_detail": detail or reason, "message": reason}


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


def _sanitized(text):
    return " ".join(str(text or "").split())[:1000]


def _find_value(root, param):
    result = (root.findtext(".//DATA/RESULT") or root.findtext(".//RESULT") or "").strip()
    if result:
        return result, "RESULT"
    aliases = TAG_ALIASES.get(param, ())
    for element in root.iter():
        tag = str(element.tag or "").split("}")[-1].upper()
        if tag in aliases and element.text and element.text.strip():
            return element.text.strip(), tag
    return "", ""


def _read_license_info_param(client, param):
    request = build_license_query_xml(param)
    request_xml = request.decode("utf-8", "replace") if isinstance(request, bytes) else str(request or "")
    logger.debug("[TALLY_LICENSE] sending license request param=%s payload=%s", param, _sanitized(request_xml))
    raw = client.post(request)
    http_status = getattr(client, "last_http_status", None)
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    logger.debug("[TALLY_LICENSE] response param=%s http_status=%s body=%s", param, http_status, _sanitized(text))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        detail = f"XML parsing failed for {param}: {exc}."
        logger.exception("[TALLY_LICENSE] %s Raw: %s", detail, _sanitized(text))
        return "", text, False, detail
    status = (root.findtext(".//HEADER/STATUS") or "").strip()
    if status != "1":
        detail = f"Tally returned status {status or 'blank'} for {param}."
        logger.warning("[TALLY_LICENSE] %s Raw: %s", detail, _sanitized(text))
        return "", text, False, detail
    result, source_tag = _find_value(root, param)
    logger.debug("[TALLY_LICENSE] parsed param=%s source_tag=%s present=%s", param, source_tag or "none", bool(result))
    return result, text, True, "" if result else f"{param} returned an empty value."


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
        details = {}
        values["SerialNumber"], raw_responses["SerialNumber"], statuses["SerialNumber"], details["SerialNumber"] = _read_license_info_param(active_client, "SerialNumber")
        if not statuses.get("SerialNumber"):
            return _unavailable("Tally returned a response that could not be parsed.", details.get("SerialNumber", "SerialNumber request failed."))
        if not values.get("SerialNumber", ""):
            logger.warning("[TALLY_LICENSE] Tally response had no serial number. Raw: %s", _sanitized(raw_responses.get("SerialNumber", "")))
            return _unavailable("Tally did not return a license serial number for the active instance.", details.get("SerialNumber", "SerialNumber returned an empty value."))
        for param in LICENSE_FIELDS:
            if param == "SerialNumber":
                continue
            values[param], raw_responses[param], statuses[param], details[param] = _read_license_info_param(active_client, param)
    except TallyConnectionError as exc:
        logger.exception("[TALLY_LICENSE] connection failed while reading license")
        return _unavailable("Could not reach Tally to read license information.", f"{exc.code}: {exc}")
    except Exception as exc:
        logger.exception("[TALLY_LICENSE] unexpected failure while reading license")
        return _unavailable("Connected to Tally, but license information could not be read.", str(exc))
    serial = values.get("SerialNumber", "")
    administrator = values.get("AdminEmailID", "")
    is_gold = values.get("IsGold", "")
    is_silver = values.get("IsSilver", "")
    is_educational = values.get("IsEducationalMode", "")
    is_licensed = values.get("IsLicensedMode", "")
    edition = "Gold" if _truthy(is_gold) else "Silver" if _truthy(is_silver) else "Educational" if _truthy(is_educational) else ""
    tally_software_services = _normalize_tss(is_licensed)
    logger.debug("[TALLY_LICENSE] fields serial_present=%s edition_present=%s tss_present=%s administrator_present=%s",
                 bool(serial), bool(edition), bool(tally_software_services), bool(administrator))
    return {"license_available": True, "serial_number": serial, "edition": edition,
            "tally_software_services": tally_software_services, "license_administrator": administrator,
            "license_verified": None, "license_error": None, "license_error_detail": "", "message": ""}
