from xml.etree import ElementTree as ET
import json
import logging
import subprocess
from django.conf import settings
from .client import TallyClient, TallyConnectionError, endpoint, tcp_probe
from .json_company_reader import get_tally_company_gst_registration, read_current_company_via_json
from .read_requests import build_company_query_xml, build_ledger_query_xml
from .read_parsers import parse_company_query_response, parse_master_query_response
from .version_transport import format_tally_version, parse_tally_version, select_transport

logger = logging.getLogger(__name__)


def _gst_duty_head_key(value):
    key = str(value or "").strip().casefold()
    return {"central tax": "CGST", "cgst": "CGST",
"state tax": "SGST/UTGST", "ut tax": "SGST/UTGST", "sgst": "SGST/UTGST", "sgst/utgst": "SGST/UTGST",
"integrated tax": "IGST", "igst": "IGST", "cess": "Cess"}.get(key, str(value or "").strip())


COMPANY_REQUEST = build_company_query_xml()


def _is_local_host(host):
    normalized = str(host or "").strip().casefold()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def local_port_listening(host, port):
    if not _is_local_host(host):
        return None
    try:
        output = subprocess.check_output(["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    needles = {f"127.0.0.1:{int(port)}", f"0.0.0.0:{int(port)}", f"[::]:{int(port)}", f"[::1]:{int(port)}"}
    # Real `netstat -ano` output always starts with header/unrelated lines --
    # the match must scan every line before concluding "not listening", not
    # just the first one.
    return any("LISTENING" in line.upper() and any(needle in line for needle in needles)
               for line in output.splitlines())


def tally_process_running(host):
    """Best-effort local process check (equivalent to `tasklist | findstr tally`).

    Only meaningful when Tally would run on this same machine -- returns None
    (unknown, never "not running") for a remote host or when `tasklist` itself
    can't be run, so callers never mistake "can't tell" for a positive result.
    """
    if not _is_local_host(host):
        return None
    try:
        output = subprocess.check_output(["tasklist"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    return any("tally" in line.casefold() for line in output.splitlines())


def _company_from_response(raw):
    parsed = parse_company_query_response(raw)
    company = (parsed.get("companies") or [{}])[0]
    name = str(company.get("name") or "").strip()
    state = str(company.get("state") or settings.TALLY_COMPANY_STATE).strip()
    gstin = str(company.get("gstin") or settings.TALLY_COMPANY_GSTIN).strip()
    return {**company, "name": name, "state": state, "gstin": gstin}


def _step3_error_code(code):
    if code in {"TALLY_CONNECTION_REFUSED"}:
        return "TALLY_CONNECTION_REFUSED"
    if code in {"TALLY_CONNECT_TIMEOUT", "TALLY_READ_TIMEOUT", "TALLY_CONNECTION_TIMEOUT"}:
        return "TALLY_CONNECTION_TIMEOUT"
    if code in {"TALLY_NO_COMPANY_OPEN", "TALLY_COMPANY_NOT_OPEN", "TALLY_COMPANY_NOT_DETECTED"}:
        return "COMPANY_NOT_OPEN"
    return code or ""


def read_current_company_via_xml(client):
    """The existing XML Collection-based current-company read (works on any
    Tally version, including < 7.1) -- unchanged behaviour, just extracted
    into a standalone function so the version-aware router can call it
    explicitly and the JSON adapter can return the same normalized shape."""
    raw = client.post(COMPANY_REQUEST)
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    print("=== TALLY XML COMPANY REQUEST ===")
    print(COMPANY_REQUEST.decode("utf-8", "replace"))
    print("=== TALLY XML COMPANY RESPONSE ===")
    print(text)
    company = _company_from_response(raw)
    name, state, gstin = company["name"], company["state"], company["gstin"]
    if name and (not gstin or not state):
        registration = get_tally_company_gst_registration(name, client=client)
        gstin = gstin or registration.get("gstin", "")
        state = state or registration.get("state", "")
    return {"transport": "XML", "company_open": bool(name), "company_name": name,
    "company_gstin": gstin, "company_state": state,
    "financial_year_from": company.get("financial_year_from", ""),
    "financial_year_to": company.get("financial_year_to", ""),
    "financial_year": company.get("financial_year", ""),
    "financial_year_available": company.get("financial_year_available", False),
    "financial_year_error": company.get("financial_year_error", ""),
    "raw_response": text}


def detect_tally_version_and_probe_json(client):
    """Detect the actual running Tally version AND probe real JSON capability
    in one lightweight call -- the same Company object read that carries the
    version fields (prod_maj_rel/prod_min_rel) is also the smallest available
    proof that a native JSON request actually works against this Tally.

    This function never raises: a transport-level failure (refused, timed
    out) and a response that simply isn't valid/parseable JSON are both
    legitimate "JSON is not available right now" outcomes -- distinguishing
    them further wouldn't change what the caller must do next (try XML), so
    both are logged and folded into the same "capability not confirmed"
    result rather than aborting the whole connection check.

    Returns (version_tuple_or_None, version_source, company_snapshot_or_None,
    http_reached). ``company_snapshot`` (and therefore json_capability) is
    only non-None when the JSON probe genuinely succeeded. version_source is
    "DETECTED" only when the version came from that same successful probe;
    otherwise it is "CONFIGURED_FALLBACK" (settings.TALLY_VERSION) -- a
    configured version is a hint for *eligibility to try* JSON, never proof
    that JSON actually works (see json_capability_confirmed in the caller).
    """
    print("=== JSON CAPABILITY REQUEST ===")
    print({"subtype": "Company", "id": "", "svExportFormat": "jsonex"})
    snapshot, http_reached = None, False
    try:
        snapshot = read_current_company_via_json(client)
        http_reached = True
    except TallyConnectionError as exc:
        print("=== JSON COMPANY READ ERROR ===")
        print({"type": type(exc).__name__, "message": str(exc), "http_status": None, "raw_body": ""})
        logger.info("Tally JSON probe failed at the transport level: %s", exc)
    except (ValueError, TypeError, KeyError) as exc:
        http_reached = True
        print("=== JSON COMPANY READ ERROR ===")
        print({"type": type(exc).__name__, "message": str(exc),
        "http_status": getattr(client, "last_http_status", None), "raw_body": ""})
        logger.info("Tally JSON probe did not return a parseable JSON response: %s", exc)

    print("=== JSON CAPABILITY RESPONSE ===")
    print({"status": getattr(client, "last_http_status", None), "success": snapshot is not None})
    if snapshot is not None:
        detected = parse_tally_version(snapshot.get("tally_version_detected"))
        if detected:
            return detected, "DETECTED", snapshot, http_reached
    return parse_tally_version(settings.TALLY_VERSION), "CONFIGURED_FALLBACK", None, http_reached


def _log_tally_connection_check(result):
    print("=== TALLY CONNECTION CHECK ===")
    print("Configured Host:", settings.TALLY_HOST)
    print("Configured Port:", settings.TALLY_PORT)
    print("Resolved Host:", result.get("host"))
    print("Resolved Port:", result.get("port"))
    print("TCP Connected:", result.get("tcp_connected"))
    print("HTTP Connected:", result.get("http_connected"))
    print("ODBC Connected:", result.get("odbc_connected"))
    print("Read Connected:", result.get("read_connected"))
    print("Requested Transport:", result.get("requested_transport"))
    print("Actual Transport:", result.get("actual_transport"))
    print("JSON Connected:", result.get("json_connected"))
    print("XML Connected:", result.get("xml_connected"))
    print("Fallback Used:", result.get("fallback_used"))
    print("Fallback Reason:", result.get("fallback_reason"))
    print("Tally Process Detected:", result.get("tally_process_detected"))
    print("Port Listening:", result.get("port_listening"))
    print("Company Open:", result.get("company_open"))
    print("Detected Company:", result.get("company_name"))
    print("Detected GSTIN:", result.get("company_gstin"))
    print("Can Import:", result.get("can_import"))
    print("Error Code:", result.get("error_code"))
    print("Error Message:", result.get("error_message"))


def step3_connection_check(client=None, odbc_connect=None):
    if getattr(settings, 'TALLY_LOCAL_AGENT_REQUIRED', False):
        from gst_tally.connector_context import connection_snapshot
        return connection_snapshot()
    result = _step3_connection_check(client=client, odbc_connect=odbc_connect)
    _log_tally_connection_check(result)
    return result


def _step3_connection_check(client=None, odbc_connect=None):
    """Real Step 3 Tally connectivity diagnostics.

    This check is intentionally read-only and not gated by ``TALLY_DRY_RUN``:
    dry run prevents writes, but Step 3 must still prove that the configured
    Tally HTTP endpoint is reachable and that a company is open. Every call
    performs a fresh live check -- nothing about the current company or its
    transport is cached across requests (Retry always re-detects everything).
    """
    active_client = client or TallyClient()
    try:
        _, host, port = endpoint(getattr(active_client, "base_url", None))
    except TallyConnectionError as exc:
        logger.exception("Invalid Tally endpoint configuration during Step 3 connection check")
        return {"read_connected": False, "http_connected": False, "odbc_connected": False,
                "company_open": False, "host": "", "port": None, "error_code": exc.code,
                "error_message": str(exc), "message": str(exc)}

    tcp_connected, tcp_code, tcp_message = tcp_probe(host, port, getattr(active_client, "connect_timeout", None))

    result = {"read_connected": False, "tcp_connected": tcp_connected, "http_connected": False, "odbc_connected": False,
              "company_open": False, "host": host, "port": port, "error_code": "",
              "error_message": "", "message": "", "company": "", "company_name": "",
              "company_state": "", "state": "", "company_gstin": "", "gstin": "",
              "http_status": None, "odbc_error": "", "http_error": "",
              "tally_version_detected": "", "tally_version_configured": settings.TALLY_VERSION,
              "version_source": "", "transport_mode": "", "requested_transport": "", "actual_transport": "",
              "json_supported_by_version": False, "json_capability_confirmed": False,
              "json_connected": False, "xml_connected": False,
              "fallback_used": False, "fallback_reason": "",
              "tally_process_detected": None, "port_listening": None}
    logger.info("Step 3 Tally connection diagnostics: configured_tally_host=%s configured_tally_port=%s resolved_host=%s resolved_port=%s",
                settings.TALLY_HOST, settings.TALLY_PORT, host, port)
    if not tcp_connected:
        # A raw TCP failure means neither HTTP nor ODBC is attempted -- there
        # is nothing to gain from trying either. When we can positively prove
        # nothing is listening on the port (local_port_listening() is False,
        # not just "unknown"), refine the code/message to name that exactly --
        # and further distinguish "Tally isn't even running" from "Tally is
        # running but its Connectivity/HTTP-ODBC server isn't listening on
        # this port" (e.g. the setting was changed but Tally wasn't restarted).
        code = _step3_error_code(tcp_code)
        port_listening = local_port_listening(host, port)
        process_detected = tally_process_running(host)
        result["port_listening"] = port_listening
        result["tally_process_detected"] = process_detected
        if code == "TALLY_CONNECTION_REFUSED" and port_listening is False:
            if process_detected is False:
                code = "TALLY_PROCESS_NOT_RUNNING"
                tcp_message = f"TallyPrime does not appear to be running on {host}. Start TallyPrime and open a company."
            else:
                code = "TALLY_PORT_NOT_LISTENING"
                tcp_message = f"No process is listening on {host}:{port}."
        result.update(error_code=code, error_message=tcp_message, http_error=tcp_message,
                      message=tcp_message, reachable=False, tally_available=False,
                      failure_type=code, can_import=False)
        return result

    print("=== CONNECTION ===")
    print({"TCP": tcp_connected})

    # STEP 2/3: version is only ELIGIBILITY to try JSON, never proof it will
    # work -- json_capability_confirmed (below) is the actual, live-probed
    # evidence. A configured/fallback version alone must never be enough to
    # mandate JSON and fail the whole connection if it doesn't pan out.
    version_tuple, version_source, company_snapshot, http_reached = detect_tally_version_and_probe_json(active_client)
    json_supported_by_version = bool(version_tuple and select_transport(version_tuple) == "JSON")
    json_capability_confirmed = company_snapshot is not None

    print("=== VERSION ===")
    print({"detected": format_tally_version(version_tuple) if version_source == "DETECTED" else "",
           "configured": settings.TALLY_VERSION, "source": version_source})
    print("=== JSON PROBE ===")
    print({"attempted": True, "success": json_capability_confirmed})

    requested_transport = "JSON" if json_supported_by_version else "XML"
    result.update(tally_version_detected=format_tally_version(version_tuple) if version_source == "DETECTED" else "",
                  version_source=version_source, json_supported_by_version=json_supported_by_version,
                  json_capability_confirmed=json_capability_confirmed, requested_transport=requested_transport)

    company_data = None
    if json_capability_confirmed:
        company_data = company_snapshot
        result.update(json_connected=True, actual_transport="JSON")
    else:
        # STEP 5: JSON not confirmed (whatever the reason) -- try the
        # existing, known-good XML company read before giving up. JSON
        # failing must never fail the whole connection while XML works.
        print("=== XML PROBE ===")
        try:
            company_data = read_current_company_via_xml(active_client)
            result.update(xml_connected=True, actual_transport="XML")
            if json_supported_by_version:
                result.update(fallback_used=True,
                              fallback_reason="Native JSON company read unavailable; XML HTTP integration used.")
            print({"attempted": True, "success": True})
        except TallyConnectionError as exc:
            print({"attempted": True, "success": False, "error": str(exc)})
            logger.warning("Tally XML current-company read failed during Step 3: %s", exc)
            code = _step3_error_code(exc.code)
            if code not in {"TALLY_CONNECTION_REFUSED", "TALLY_CONNECTION_TIMEOUT"}:
                code = "TALLY_TRANSPORT_UNAVAILABLE"
            result.update(error_code=code, error_message=str(exc) or "Neither JSON nor XML transport could read Tally.",
                          http_error=str(exc), http_connected=http_reached)
            print("=== TRANSPORT ===")
            print({"requested": requested_transport, "actual": "", "fallback": False})
            print("=== FINAL ===")
            print({"read_connected": False, "company_open": False, "error_code": result["error_code"]})
            return result
        except ET.ParseError as exc:
            print({"attempted": True, "success": False, "error": str(exc)})
            logger.exception("Tally XML current-company read returned invalid XML during Step 3")
            result.update(error_code="XML_TRANSPORT_UNAVAILABLE",
                          error_message=f"Tally returned invalid XML: {exc}",
                          http_error=f"Tally returned invalid XML: {exc}", http_connected=http_reached)
            return result

    print("=== TRANSPORT ===")
    print({"requested": requested_transport, "actual": result["actual_transport"], "fallback": result["fallback_used"]})

    name = company_data.get("company_name", "")
    state = company_data.get("company_state", "")
    gstin = company_data.get("company_gstin", "")
    print("=== CURRENT COMPANY ===")
    print({"name": name, "gstin": gstin, "state": state})
    result.update(read_connected=True, http_connected=True,
                  company_open=bool(name), company=name, company_name=name,
                  company_state=state, state=state, company_gstin=gstin, gstin=gstin,
                  financial_year_from=company_data.get("financial_year_from", ""),
                  financial_year_to=company_data.get("financial_year_to", ""),
                  financial_year=company_data.get("financial_year", ""),
                  financial_year_available=company_data.get("financial_year_available", False),
                  financial_year_error=company_data.get("financial_year_error", ""),
                  http_status=getattr(active_client, "last_http_status", None) or 200)
    # transport_mode is kept, aliasing actual_transport, for callers written
    # against the previous field name.
    result["transport_mode"] = result["actual_transport"]
    if not name:
        result.update(error_code="COMPANY_NOT_OPEN",
                      error_message="Tally is connected, but no company is open.")
    print("=== FINAL ===")
    print({"read_connected": result["read_connected"], "company_open": result["company_open"], "error_code": result["error_code"]})

    # ODBC is checked unconditionally (not just when no company is open) --
    # can_import below needs an accurate odbc_connected regardless of whether
    # the HTTP company read already succeeded.
    if settings.TALLY_ODBC_ENABLED:
        from .odbc import classify_odbc_error, odbc_company_status
        odbc_status = odbc_company_status(connect=odbc_connect)
        result["odbc_connected"] = bool(odbc_status.get("odbc_connected"))
        result["odbc_error"] = odbc_status.get("odbc_error", "")
        odbc_code, _ = classify_odbc_error(result["odbc_error"])
        if not result["odbc_connected"] and result["http_connected"]:
            result["error_code"] = result["error_code"] or odbc_code
            result["error_message"] = result["error_message"] or result["odbc_error"] or "The Tally ODBC connection failed."

    result["message"] = result["error_message"] or "Tally connection verified."
    result["reachable"] = result["read_connected"]
    result["tally_available"] = result["read_connected"]
    result["failure_type"] = result["error_code"]
    odbc_required_for_step3 = bool(settings.TALLY_ODBC_ENABLED and settings.TALLY_WRITE_FORMAT != "JSON")
    required_channels_connected = result["http_connected"] and (result["odbc_connected"] if odbc_required_for_step3 else True)
    result["can_import"] = bool(required_channels_connected and result["company_open"])
    return result


def connection_status(client=None):
    try: _, host, port = endpoint(getattr(client, "base_url", None))
    except TallyConnectionError as exc:
        return {"reachable": False, "tcp_connected": False, "http_request_attempted": False, "tally_response_received": False,
    "host": "", "port": None, "failure_type": exc.code, "company_open": False, "company": "", "state": "", "gstin": "", "dry_run": settings.TALLY_DRY_RUN, "can_import": False, "message": str(exc)}
    if settings.TALLY_DRY_RUN:
        return {"reachable": False, "tcp_connected": False, "http_request_attempted": False, "tally_response_received": False,
    "host": host, "port": port, "failure_type": "TALLY_DRY_RUN", "company_open": False, "company": "", "state": settings.TALLY_COMPANY_STATE,
    "gstin": settings.TALLY_COMPANY_GSTIN, "dry_run": True, "can_import": False,
    "message": "Dry run is enabled; no request was sent to Tally."}
    tcp_connected, failure_type, failure_message = (True, "", "") if client is not None and not isinstance(client, TallyClient) else tcp_probe(host, port)
    if not tcp_connected:
        return {"reachable": False, "tcp_connected": False, "http_request_attempted": False, "tally_response_received": False,
    "host": host, "port": port, "failure_type": failure_type, "company_open": False, "company": "", "state": "", "gstin": "", "dry_run": False, "can_import": False, "message": failure_message}
    active_client = client or TallyClient()
    try: raw = active_client.post(COMPANY_REQUEST)
    except TallyConnectionError as exc:
        logger.warning("Tally HTTP connection check failed: %s", exc)
        return {"reachable": False, "tcp_connected": True, "http_request_attempted": True, "tally_response_received": False,
    "host": host, "port": port, "failure_type": exc.code, "company_open": False, "company": "", "state": "", "gstin": "", "dry_run": False, "can_import": False, "message": str(exc)}
    try: root = ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.exception("Tally HTTP connection check returned invalid XML")
        return {"reachable": False, "tcp_connected": True, "http_request_attempted": True, "tally_response_received": True,
    "host": host, "port": port, "http_status": getattr(active_client, "last_http_status", None), "failure_type": "TALLY_INVALID_RESPONSE", "company_open": False, "company": "", "state": "", "gstin": "", "dry_run": False, "can_import": False, "message": f"Tally returned invalid XML: {exc}"}
    company = _company_from_response(raw)
    name, state, gstin = company["name"], company["state"], company["gstin"]
    expected = settings.TALLY_EXPECTED_COMPANY.strip()
    matches = not expected or name.casefold() == expected.casefold()
    failure_type = "" if name and matches else "TALLY_NO_COMPANY_OPEN" if not name else "TALLY_UNEXPECTED_COMPANY"
    message = "Connected" if name and matches else "No company is open" if not name else f"Expected company '{expected}', but '{name}' is open"
    return {"reachable": True, "tcp_connected": True, "http_request_attempted": True, "tally_response_received": True,
    "host": host, "port": port, "http_status": getattr(active_client, "last_http_status", None) or 200, "failure_type": failure_type,
    "company_open": bool(name), "company": name, "state": state, "gstin": gstin,
    "financial_year_from": company.get("financial_year_from", ""),
    "financial_year_to": company.get("financial_year_to", ""),
    "financial_year": company.get("financial_year", ""),
    "financial_year_available": company.get("financial_year_available", False),
    "financial_year_error": company.get("financial_year_error", ""),
    "dry_run": False, "can_import": bool(name and matches), "message": message}

def diagnostics():
    from .odbc import odbc_company_status
    status = odbc_company_status()
    return {"tally_enabled": settings.TALLY_ENABLED, "dry_run": settings.TALLY_DRY_RUN,
"odbc_enabled": settings.TALLY_ODBC_ENABLED, "odbc_dsn": settings.TALLY_ODBC_DSN,
"odbc_connected": status["odbc_connected"], "read_connected": status.get("read_connected", status["odbc_connected"]), "connection_attempted": settings.TALLY_ODBC_ENABLED,
"tally_available": status.get("read_connected", status["odbc_connected"]), "failure_type": status.get("failure_type", ""),
"company_detected": status["company_detected"], "company_name": status["company_name"],
"company_state": status["company_state"], "company_gstin": status["company_gstin"], "reason": status["message"]}

def existing_masters(client=None):
    """Master lookup. Uses the master query builder/parser pair, never the voucher pair."""
    raw = (client or TallyClient()).post(build_ledger_query_xml())
    result = parse_master_query_response(raw)
    if not result["query_valid"]:
        raise TallyConnectionError("TALLY_MASTER_QUERY_FAILED", result["reason"])
    return result["names"], result["gstins"]

def master_exists(master, client=None):
    """Use Tally's lightweight object export instead of enumerating every master."""
    object_type = {"Party": "Ledger", "Sales": "Ledger", "Tax": "Ledger", "Charge": "Ledger",
    "Stock Item": "Stock Item", "Unit": "Unit"}[master["master_type"]]
    root = ET.Element("ENVELOPE"); header = ET.SubElement(root, "HEADER")
    ET.SubElement(header, "VERSION").text = "1"; ET.SubElement(header, "TALLYREQUEST").text = "Export"
    ET.SubElement(header, "TYPE").text = "Object"; ET.SubElement(header, "SUBTYPE").text = object_type
    identifier = ET.SubElement(header, "ID", {"TYPE": "Name"}); identifier.text = master["name"]
    desc = ET.SubElement(ET.SubElement(root, "BODY"), "DESC"); fetch = ET.SubElement(desc, "FETCHLIST")
    for field in ("NAME", "GSTREGISTRATIONNO", "PARENT", "BASEUNITS"): ET.SubElement(fetch, "FETCH").text = field
    response = ET.fromstring((client or TallyClient()).post(ET.tostring(root, encoding="utf-8")))
    tag = object_type.replace(" ", "").upper()
    node = response.find(f".//{tag}")
    if node is None: return False, ""
    found_name = (node.get("NAME") or node.findtext("NAME") or "").strip()
    found_gstin = (node.findtext(".//GSTREGISTRATIONNO") or "").strip().upper()
    return found_name.casefold() == master["name"].casefold(), found_gstin


def _json_scalar(value):
    """Normalize scalar values returned by Tally JSON/JSONEx."""
    if isinstance(value, list):
        # JSONEx string/list wrappers can start with {"metadata": true, ...}.
        cleaned = [
            item for item in value
            if not (isinstance(item, dict) and item.get("metadata") is True)
        ]
        return _json_scalar(cleaned[-1]) if cleaned else ""

    if isinstance(value, dict):
        for key in ("value", "name"):
            if key in value:
                return _json_scalar(value[key])
        return ""

    return str(value or "").replace("\x04", "").strip()


def _json_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _find_json_ledger(value, ledger_name):
    """Recursively locate the requested Ledger object in a Tally JSON export."""
    wanted = str(ledger_name or "").strip().casefold()

    if isinstance(value, dict):
        lowered = {str(key).casefold(): item for key, item in value.items()}

        metadata = lowered.get("metadata")
        metadata_name = ""
        metadata_type = ""
        if isinstance(metadata, dict):
            metadata_lowered = {
                str(key).casefold(): item for key, item in metadata.items()
            }
            metadata_name = _json_scalar(metadata_lowered.get("name"))
            metadata_type = _json_scalar(metadata_lowered.get("type")).casefold()

        candidate_name = (
            metadata_name
            or _json_scalar(lowered.get("name"))
            or _json_scalar(lowered.get("ledgername"))
        )

        if (
            candidate_name
            and candidate_name.casefold() == wanted
            and (not metadata_type or metadata_type == "ledger")
        ):
            return lowered

        for child in value.values():
            found = _find_json_ledger(child, ledger_name)
            if found is not None:
                return found

    elif isinstance(value, list):
        for child in value:
            found = _find_json_ledger(child, ledger_name)
            if found is not None:
                return found

    return None


def _json_dict(value):
    if not isinstance(value, dict):
        return {}
    return {str(key).casefold(): item for key, item in value.items()}


def _ledger_details_from_json(payload, name, raw_text=""):
    """Convert native Tally Ledger JSON/JSONEx into ledger_details()'s normal shape."""
    node = _find_json_ledger(payload, name)
    if node is None:
        return {
            "exists": False,
            "name": name,
            "reason": "Ledger not found in native Tally JSON response",
            "raw": raw_text,
            "read_transport": "JSON",
        }

    histories = [
        _json_dict(item)
        for item in _json_list(node.get("gstdetails"))
        if isinstance(item, dict)
    ]
    histories.sort(key=lambda row: _json_scalar(row.get("applicablefrom")))
    effective_history = histories[-1] if histories else {}

    rate_rows = []
    for state_value in _json_list(effective_history.get("statewisedetails")):
        state_row = _json_dict(state_value)
        if not state_row:
            continue
        for rate_value in _json_list(state_row.get("ratedetails")):
            rate_row = _json_dict(rate_value)
            if rate_row:
                rate_rows.append(rate_row)

    rates = {}
    rate_valuation_types = {}
    for row in rate_rows:
        head = _gst_duty_head_key(_json_scalar(row.get("gstratedutyhead")))
        rate = _json_scalar(row.get("gstrate"))
        if head and rate:
            rates[head] = rate
        valuation_type = _json_scalar(row.get("gstratevaluationtype"))
        if head and valuation_type:
            rate_valuation_types[head] = valuation_type

    direct_rate = _json_scalar(node.get("rateoftaxcalculation"))

    if rates.get("IGST"):
        gst_rate = rates["IGST"]
    elif rates:
        try:
            gst_rate = str(
                float(rates.get("CGST", 0) or 0)
                + float(rates.get("SGST/UTGST", 0) or 0)
            )
        except (TypeError, ValueError):
            gst_rate = direct_rate
    else:
        gst_rate = direct_rate

    registration_rows = [
        _json_dict(item)
        for item in _json_list(node.get("ledgstregdetails"))
        if isinstance(item, dict)
    ]
    registration = registration_rows[-1] if registration_rows else {}

    mailing_rows = [
        _json_dict(item)
        for item in _json_list(node.get("ledmailingdetails"))
        if isinstance(item, dict)
    ]
    mailing = mailing_rows[-1] if mailing_rows else {}

    metadata = _json_dict(node.get("metadata"))

    state = (
        _json_scalar(node.get("ledstatename"))
        or _json_scalar(node.get("statename"))
        or _json_scalar(mailing.get("state"))
        or _json_scalar(registration.get("state"))
    )

    return {
        "exists": True,
        "name": (
            _json_scalar(metadata.get("name"))
            or _json_scalar(node.get("name"))
            or name
        ),
        "parent": _json_scalar(node.get("parent")),
        "gstin": (
            _json_scalar(node.get("partygstin"))
            or _json_scalar(node.get("gstregistrationno"))
            or _json_scalar(registration.get("gstin"))
        ).upper(),
        "duty_type": _json_scalar(node.get("taxtype")),
        "tax_type": _json_scalar(node.get("gstdutyhead")),
        "gst_rate": gst_rate,
        "outer_gst_rate": direct_rate or gst_rate,
        "gst_applicable": _json_scalar(node.get("gstapplicable")),
        "gst_rate_details": _json_scalar(
            effective_history.get("srcofgstdetails")
        ),
        "gst_rate_history_exists": bool(histories),
        "gst_rate_details_popup_exists": bool(rate_rows),
        "set_alter_gst_rate_details": "Yes" if rate_rows else "No",
        "gst_applicable_from": _json_scalar(
            effective_history.get("applicablefrom")
        ),
        "gst_nature_of_transaction": _json_scalar(
            effective_history.get("gstnatureoftransaction")
        ),
        "taxability": _json_scalar(effective_history.get("taxability")),
        "supply_type": (
            _json_scalar(node.get("gsttypeofsupply"))
            or _json_scalar(effective_history.get("supplytype"))
        ),
        "rounding_method": _json_scalar(node.get("roundtype")),
        "state": state,
        "place_of_supply": _json_scalar(registration.get("placeofsupply")),
        "country": (
            _json_scalar(node.get("countryname"))
            or _json_scalar(node.get("countryofresidence"))
            or _json_scalar(mailing.get("country"))
        ),
        "registration_type": (
            _json_scalar(node.get("gstregistrationtype"))
            or _json_scalar(registration.get("gstregistrationtype"))
        ),
        "pincode": (
            _json_scalar(node.get("pincode"))
            or _json_scalar(mailing.get("pincode"))
        ),
        "address": _json_scalar(mailing.get("address")),
        "gst_rates": rates,
        "gst_rate_valuation_types": rate_valuation_types,
        "master_id": _json_scalar(node.get("masterid")),
        "raw": raw_text,
        "read_transport": "JSON",
    }


def _ledger_details_via_json(name, company="", client=None):
    """
    Read one exact Ledger using Tally's native HTTP JSONEx Object export.

    This mirrors the JSON capability read already used by the project:
    tallyrequest=Export, type=Object, subtype=Ledger, svExportFormat=jsonex.
    """
    active_client = client or TallyClient()

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "version": "1",
        "tallyrequest": "Export",
        "type": "Object",
        "subtype": "Ledger",
        "id": name,
        "svExportFormat": "jsonex",
    }
    if company:
        headers["svCurrentCompany"] = company

    raw = active_client.post(b"", headers=headers)
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")

    if not text.strip():
        raise ValueError("Tally returned an empty JSON ledger response")

    payload = json.loads(text)
    result = _ledger_details_from_json(payload, name, text)

    if not result.get("exists"):
        raise ValueError(result.get("reason") or "Ledger missing in JSON response")

    return result


def _ledger_details_via_xml(name, company="", client=None):
    """Existing XML Ledger object read retained as the compatibility fallback."""
    root = ET.Element("ENVELOPE")
    header = ET.SubElement(root, "HEADER")
    ET.SubElement(header, "VERSION").text = "1"
    ET.SubElement(header, "TALLYREQUEST").text = "Export"
    ET.SubElement(header, "TYPE").text = "Object"
    ET.SubElement(header, "SUBTYPE").text = "Ledger"
    ET.SubElement(header, "ID", {"TYPE": "Name"}).text = name

    desc = ET.SubElement(ET.SubElement(root, "BODY"), "DESC")
    variables = ET.SubElement(desc, "STATICVARIABLES")
    if company:
        ET.SubElement(variables, "SVCURRENTCOMPANY").text = company

    fetch = ET.SubElement(desc, "FETCHLIST")
    for field in (
        "Name",
        "Parent",
        "PartyGSTIN",
        "GSTRegistrationNo",
        "TaxType",
        "GSTDutyHead",
        "RateOfTaxCalculation",
        "RoundType",
        "GSTApplicable",
        "GSTTypeOfSupply",
        "GSTDetails",
        "GSTDetails.*",
        "GSTDetails.StateWiseDetails",
        "GSTDetails.StateWiseDetails.*",
        "GSTDetails.StateWiseDetails.RateDetails",
        "GSTDetails.StateWiseDetails.RateDetails.*",
        "LedStateName",
        "StateName",
        "CountryName",
        "CountryOfResidence",
        "GSTRegistrationType",
        "Pincode",
        "LedMailingDetails",
        "LedGSTRegDetails",
    ):
        ET.SubElement(fetch, "FETCH").text = field

    raw = (client or TallyClient()).post(ET.tostring(root, encoding="utf-8"))
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")

    # Tally can emit its internal \x04 marker as an XML-forbidden entity.
    text = text.replace("&#4;", "").replace("&#x4;", "")

    try:
        response = ET.fromstring(text)
    except ET.ParseError as exc:
        return {
            "exists": False,
            "name": name,
            "reason": f"Unparsable ledger query response: {exc}",
            "raw": text,
            "read_transport": "XML",
        }

    node = next(
        (
            item
            for item in response.iter("LEDGER")
            if (item.get("NAME") or "").strip().casefold()
            == name.strip().casefold()
        ),
        None,
    )

    if node is None:
        return {
            "exists": False,
            "name": name,
            "reason": "Ledger not found in current Tally company",
            "raw": text,
            "read_transport": "XML",
        }

    rates = {}
    gst_history_nodes = node.findall(".//GSTDETAILS.LIST")
    dated_history = sorted(
        gst_history_nodes,
        key=lambda history: (history.findtext("APPLICABLEFROM") or "").strip(),
    )
    effective_history = dated_history[-1] if dated_history else None
    rate_details_nodes = (
        effective_history.findall(".//RATEDETAILS.LIST")
        if effective_history is not None
        else []
    )

    rate_valuation_types = {}
    for rate in rate_details_nodes:
        head = _gst_duty_head_key(rate.findtext("GSTRATEDUTYHEAD"))
        value = (rate.findtext("GSTRATE") or "").strip()
        if head and value:
            rates[head] = value
        valuation_type = (rate.findtext("GSTRATEVALUATIONTYPE") or "").strip()
        if head and valuation_type:
            rate_valuation_types[head] = valuation_type

    direct_rate = (node.findtext("RATEOFTAXCALCULATION") or "").strip()
    gst_rate = (
        rates.get("IGST")
        or (
            str(
                sum(
                    float(rates.get(key, 0) or 0)
                    for key in ("CGST", "SGST/UTGST")
                )
            )
            if rates
            else direct_rate
        )
    )

    rounding_method = (node.findtext("ROUNDTYPE") or "").strip()
    if (
        not rounding_method
        and (node.findtext("TAXTYPE") or "").strip().casefold() == "gst"
    ):
        rounding_method = "Not Applicable"

    state = (
        node.findtext("LEDSTATENAME")
        or node.findtext("STATENAME")
        or node.findtext(".//LEDMAILINGDETAILS.LIST/STATE")
        or node.findtext(".//LEDGSTREGDETAILS.LIST/STATE")
        or ""
    ).strip()

    place_of_supply = (
        node.findtext(".//LEDGSTREGDETAILS.LIST/PLACEOFSUPPLY") or ""
    ).strip()

    return {
        "exists": True,
        "name": (node.get("NAME") or name).strip(),
        "parent": (node.findtext("PARENT") or "").strip(),
        "gstin": (
            node.findtext("PARTYGSTIN")
            or node.findtext("GSTREGISTRATIONNO")
            or node.findtext(".//LEDGSTREGDETAILS.LIST/GSTIN")
            or ""
        ).strip().upper(),
        "duty_type": (node.findtext("TAXTYPE") or "").strip(),
        "tax_type": (node.findtext("GSTDUTYHEAD") or "").strip(),
        "gst_rate": gst_rate,
        "outer_gst_rate": direct_rate or gst_rate,
        "gst_applicable": (node.findtext("GSTAPPLICABLE") or "").strip(),
        "gst_rate_details": (
            (effective_history.findtext("SRCOFGSTDETAILS") or "").strip()
            if effective_history is not None
            else ""
        ),
        "gst_rate_history_exists": bool(gst_history_nodes),
        "gst_rate_details_popup_exists": bool(rate_details_nodes),
        "set_alter_gst_rate_details": "Yes" if rate_details_nodes else "No",
        "gst_applicable_from": (
            (effective_history.findtext("APPLICABLEFROM") or "").strip()
            if effective_history is not None
            else ""
        ),
        "gst_nature_of_transaction": (
            (effective_history.findtext("GSTNATUREOFTRANSACTION") or "").strip()
            if effective_history is not None
            else ""
        ),
        "taxability": (
            (effective_history.findtext("TAXABILITY") or "").strip()
            if effective_history is not None
            else ""
        ),
        "supply_type": (
            node.findtext("GSTTYPEOFSUPPLY")
            or node.findtext(".//SUPPLYTYPE")
            or ""
        ).strip(),
        "rounding_method": rounding_method,
        "state": state,
        "place_of_supply": place_of_supply,
        "country": (
            node.findtext("COUNTRYNAME")
            or node.findtext("COUNTRYOFRESIDENCE")
            or node.findtext(".//LEDMAILINGDETAILS.LIST/COUNTRY")
            or ""
        ).strip(),
        "registration_type": (
            node.findtext("GSTREGISTRATIONTYPE")
            or node.findtext(".//LEDGSTREGDETAILS.LIST/GSTREGISTRATIONTYPE")
            or ""
        ).strip(),
        "pincode": (
            node.findtext("PINCODE")
            or node.findtext(".//LEDMAILINGDETAILS.LIST/PINCODE")
            or ""
        ).strip(),
        "address": ", ".join(
            text for text in (
                (elem.text or "").strip()
                for elem in node.findall(".//LEDMAILINGDETAILS.LIST/ADDRESS.LIST/ADDRESS")
            ) if text
        ),
        "gst_rates": rates,
        "gst_rate_valuation_types": rate_valuation_types,
        "master_id": (node.findtext("MASTERID") or "").strip(),
        "raw": text,
        "read_transport": "XML",
    }


def ledger_details(name, company="", client=None):
    """
    Query one exact Ledger and return the properties required by preflight.

    Native JSONEx is attempted first because the master write path now uses
    native JSON and the JSON export preserves GSTDETAILS/STATEWISEDETAILS/
    RATEDETAILS. XML remains a safe compatibility fallback.
    """
    active_client = client or TallyClient()

    try:
        result = _ledger_details_via_json(
            name,
            company=company,
            client=active_client,
        )
        logger.info(
            "Ledger '%s' read back using native JSONEx; gst_rates=%s",
            name,
            result.get("gst_rates"),
        )
        return result

    except (
        TallyConnectionError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        logger.info(
            "Native JSON ledger read unavailable for '%s'; using XML fallback: %s",
            name,
            exc,
        )

        return _ledger_details_via_xml(
    name,
    company=company,
    client=active_client,
)

