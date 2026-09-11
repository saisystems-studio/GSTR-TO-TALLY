"""Read-only Tally discovery. Active writes use the configured JSON/XML adapter."""
import logging
import re
from django.conf import settings
from .client import TallyClient, endpoint, tcp_probe
from .read_requests import build_company_query_xml

logger = logging.getLogger(__name__)


def normalize_gstin(value):
    return "".join(str(value or "").split()).upper()

def normalize_company_name(value):
    value = re.sub(r"\s+", " ", str(value or "").strip()).upper()
    return re.sub(r"[,.]+$", "", value).rstrip()

def _valid_gstin(value): return bool(re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]", normalize_gstin(value)))
def _gstin_state(gstin):
    from .validators import state_name
    return state_name(normalize_gstin(gstin)[:2]) if _valid_gstin(gstin) else ""


def _iso_date(value):
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def _display_period(start, end):
    if not start or not end:
        return ""
    from datetime import date
    def _parse(value):
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None
    parsed_start, parsed_end = _parse(start), _parse(end)
    if not parsed_start or not parsed_end:
        return ""
    return f"{parsed_start:%d %b %Y} - {parsed_end:%d %b %Y}"


def _active_financial_period(client=None):
    from .read_parsers import parse_company_query_response
    request_xml = build_company_query_xml()
    try:
        raw = (client or TallyClient()).post(request_xml)
        parsed = parse_company_query_response(raw)
        company = (parsed.get("companies") or [{}])[0]
        financial_year_from = _iso_date(company.get("financial_year_from"))
        financial_year_to = _iso_date(company.get("financial_year_to"))
        financial_year = str(company.get("financial_year") or _display_period(financial_year_from, financial_year_to)).strip()
    except Exception as exc:
        financial_year_from = ""
        financial_year_to = ""
        financial_year = ""
        error = f"TALLY_FINANCIAL_YEAR_UNAVAILABLE: {exc}"
    else:
        error = "" if financial_year_from and financial_year_to else "TALLY_FINANCIAL_YEAR_UNAVAILABLE"
    print("=== TALLY FINANCIAL PERIOD ===")
    print("from =", financial_year_from)
    print("to =", financial_year_to)
    print("formatted =", financial_year)
    return {"financial_year_from": financial_year_from,
            "financial_year_to": financial_year_to,
            "financial_year": financial_year,
            "financial_year_available": bool(financial_year_from and financial_year_to),
            "financial_year_error": error}

# The connection-level codes Step 4 must distinguish instead of collapsing
# everything into "GSTIN could not be read". Order here is the priority used
# to pick one overall failure_type when several conditions are simultaneously true.
TALLY_NOT_REACHABLE = "TALLY_NOT_REACHABLE"
TALLY_HTTP_UNAVAILABLE = "TALLY_HTTP_UNAVAILABLE"
TALLY_ODBC_UNAVAILABLE = "TALLY_ODBC_UNAVAILABLE"
TALLY_COMPANY_NOT_DETECTED = "TALLY_COMPANY_NOT_DETECTED"
TALLY_COMPANY_NOT_OPEN = "TALLY_COMPANY_NOT_OPEN"
TALLY_COMPANY_GSTIN_READ_FAILED = "TALLY_COMPANY_GSTIN_READ_FAILED"
CONNECTION_FAILURE_CODES = {TALLY_NOT_REACHABLE, TALLY_HTTP_UNAVAILABLE, TALLY_ODBC_UNAVAILABLE,
                            TALLY_COMPANY_NOT_DETECTED, TALLY_COMPANY_NOT_OPEN, TALLY_COMPANY_GSTIN_READ_FAILED}


def classify_odbc_error(exc):
    message = str(exc)
    upper = message.upper()
    if "IM002" in upper or "DATA SOURCE NAME NOT FOUND" in upper or "NO DEFAULT DRIVER SPECIFIED" in upper:
        return "ODBC_DRIVER_NOT_AVAILABLE", message
    return TALLY_ODBC_UNAVAILABLE, message


def odbc_company_status(connect=None, requested_company="", read_client=None, expected_gstin=""):
    """Trace every read channel independently (TCP reachability, HTTP JSON, ODBC)
    and merge whatever each one found, instead of one channel's exception wiping
    out data another channel already read. See CONNECTION_FAILURE_CODES for the
    exact set of root causes this distinguishes."""
    try:
        _, probe_host, probe_port = endpoint(settings.TALLY_BASE_URL)
    except Exception:
        probe_host, probe_port = settings.TALLY_HOST, settings.TALLY_PORT
    port_reachable, _port_code, _port_detail = tcp_probe(probe_host, probe_port, settings.TALLY_CONNECT_TIMEOUT)

    json_registration = {}
    active_period = {}
    # Company discovery is a read concern, independent of the configured write
    # transport. Tally's supported JSON object export works over the HTTP server
    # in both XML-write and JSON-write configurations.
    http_attempted = bool(requested_company)
    if http_attempted:
        from .json_company_reader import get_tally_company_gst_registration
        json_registration = get_tally_company_gst_registration(requested_company, expected_gstin, read_client)
        active_period = _active_financial_period(read_client)
    http_connected = bool(json_registration.get("json_company_response_received"))
    http_error = json_registration.get("error", "")

    # Native JSON company discovery can fail on a Tally build/configuration
    # where the HTTP Connectivity server itself is fine but JSON export isn't
    # available -- the same situation Step 3's connection check (see
    # connection.py::_step3_connection_check) already treats as a successful
    # XML fallback, never a connection failure. Company Verification must
    # agree: fall back to that same proven XML Collection read here too,
    # instead of reporting "Tally Connection Failed" just because JSON
    # transport specifically didn't work.
    if http_attempted and not http_connected:
        from .connection import read_current_company_via_xml
        try:
            xml_snapshot = read_current_company_via_xml(read_client or TallyClient())
        except Exception as exc:
            http_error = http_error or str(exc)
        else:
            if xml_snapshot.get("company_open"):
                http_connected = True
                http_error = ""
                json_registration = {
                    **json_registration,
                    "json_company_response_received": True,
                    "company_open": True,
                    "company_name": xml_snapshot.get("company_name", ""),
                    "gstin": xml_snapshot.get("company_gstin", ""),
                    "state": xml_snapshot.get("company_state", ""),
                    "read_source": "XML_COMPANY",
                }
                active_period = active_period or {
                    "financial_year_from": xml_snapshot.get("financial_year_from", ""),
                    "financial_year_to": xml_snapshot.get("financial_year_to", ""),
                    "financial_year": xml_snapshot.get("financial_year", ""),
                    "financial_year_available": bool(xml_snapshot.get("financial_year_from") and xml_snapshot.get("financial_year_to")),
                    "financial_year_error": "",
                }

    identity = {key: json_registration.get(key, "") for key in ("pan", "legal_name", "trade_name")}
    registration = ({"gstin": json_registration.get("gstin", ""), "state": json_registration.get("state", ""),
                     "registration_type": json_registration.get("registration_type", ""),
                     "registration_status": json_registration.get("registration_status") or "Active",
                     "read_source": json_registration.get("read_source", "")}
                    if _valid_gstin(json_registration.get("gstin")) else {})

    odbc_attempted = bool(settings.TALLY_ODBC_ENABLED)
    odbc_connected = False
    odbc_error = "" if odbc_attempted else "Tally ODBC verification is disabled."
    row, open_rows = None, []
    wanted = str(requested_company or "").strip().casefold()
    if odbc_attempted:
        try:
            if connect is None:
                import pyodbc
                connect = pyodbc.connect
            connection_string = settings.TALLY_ODBC_CONNECTION_STRING.strip()
            if not connection_string:
                connection_string = f"DSN={settings.TALLY_ODBC_DSN}"
            connection = connect(connection_string, timeout=settings.TALLY_ODBC_TIMEOUT)
            odbc_connected = True
            try:
                cursor = connection.cursor()
                # Tally exposes every currently open company through the Company collection.
                cursor.execute('SELECT $Name, $StateName, $GSTRegistrationNumber FROM Company')
                fetched = cursor.fetchall() if hasattr(cursor, "fetchall") else [cursor.fetchone()]
                open_rows = [item for item in fetched if item]
                row = next((item for item in open_rows if not wanted or str(item[0] or "").strip().casefold() == wanted), None)
                if expected_gstin:
                    row = next((item for item in open_rows if normalize_gstin(item[2]) == normalize_gstin(expected_gstin)), row)
                if row:
                    # These are standard Company methods, but older Tally/ODBC builds may
                    # not expose all of them. Identity discovery must never break GSTIN discovery.
                    try:
                        cursor.execute('SELECT $Name, $IncomeTaxNumber, $MailingName, $StateName FROM Company')
                        identity_rows = cursor.fetchall() if hasattr(cursor, "fetchall") else [cursor.fetchone()]
                        identity_row = next((item for item in identity_rows if item and (not wanted or str(item[0] or "").strip().casefold() == wanted)), None)
                        if identity_row:
                            identity.update(pan=str(identity_row[1] or "").strip(), legal_name=str(identity_row[2] or "").strip(),
                                            trade_name=str(identity_row[0] or "").strip())
                    except Exception:
                        pass
                    if not _valid_gstin(row[2]) and not registration:
                        for query in ('SELECT $Name, $GSTRegNumber, $StateName, $GSTRegistrationType FROM TaxUnit',
                                      'SELECT $Name, $GSTRegistrationNumber, $StateName, $GSTRegistrationType FROM TaxUnit'):
                            try:
                                cursor.execute(query)
                                units = cursor.fetchall() if hasattr(cursor, "fetchall") else [cursor.fetchone()]
                                unit = next((item for item in units if item and _valid_gstin(item[1])), None)
                                if unit:
                                    registration = {"gstin": normalize_gstin(unit[1]), "state": str(unit[2] or "").strip(),
                                                    "registration_type": str(unit[3] or "").strip(), "registration_status": "Configured",
                                                    "read_source": "ODBC_TAXUNIT"}
                                    break
                            except Exception:
                                continue
            finally:
                connection.close()
        except Exception as exc:
            _odbc_code, odbc_error = classify_odbc_error(exc)
            logger.warning("Tally ODBC company status check failed: %s", odbc_error)
            odbc_connected, row, open_rows = False, None, []

    # A channel that actually produced a row proves the port was reachable, even
    # if a raw probe (blocked by a firewall, or racing a mocked `connect`) said otherwise.
    port_reachable = port_reachable or odbc_connected or http_connected
    read_connected = odbc_connected or http_connected
    http_company_open = bool(http_attempted and json_registration.get("company_open"))
    http_company_name = str(identity.get("trade_name") or json_registration.get("company_name") or "").strip()
    http_company_matched = bool(http_company_open and normalize_company_name(http_company_name) == normalize_company_name(requested_company))
    if expected_gstin and http_company_open:
        http_company_matched = normalize_gstin(registration.get("gstin")) == normalize_gstin(expected_gstin)
    company_matched = bool(row) or http_company_matched
    any_company_open = bool(open_rows) or http_company_open

    if row:
        name, state = str(row[0] or "").strip(), str(row[1] or "").strip()
        direct_gstin = normalize_gstin(row[2])
        if _valid_gstin(direct_gstin):
            registration = {"gstin": direct_gstin, "state": state, "registration_type": "",
                            "registration_status": "Configured", "read_source": "ODBC_COMPANY"}
    elif http_company_open:
        name = http_company_name
        state = str(json_registration.get("state") or "").strip()
    else:
        name, state = "", ""

    gstin = registration.get("gstin", "")
    state = registration.get("state") or state or _gstin_state(gstin)
    open_company_names = list(dict.fromkeys(str(item[0] or "").strip() for item in open_rows if item and item[0]))

    if not read_connected:
        if not port_reachable:
            failure_type, detail = TALLY_NOT_REACHABLE, ""
            message = f"Tally is not reachable on {probe_host}:{probe_port}. Start TallyPrime and enable its Connectivity/ODBC server."
        elif odbc_attempted and not http_attempted:
            failure_type, detail = classify_odbc_error(odbc_error)[0], odbc_error
            message = f"The Tally ODBC connection failed: {odbc_error}"
        elif http_attempted and not odbc_attempted:
            failure_type, detail = TALLY_HTTP_UNAVAILABLE, http_error
            message = f"The Tally HTTP read failed: {http_error}"
        else:
            # Both channels were attempted and both failed while the port itself
            # answered -- report the HTTP failure (the default/JSON write path)
            # as the headline cause but keep the ODBC detail alongside it.
            failure_type, detail = (TALLY_HTTP_UNAVAILABLE, http_error) if http_attempted else (classify_odbc_error(odbc_error)[0], odbc_error)
            message = f"The Tally HTTP read failed: {http_error}. ODBC also failed: {odbc_error}"
    elif not company_matched:
        failure_type = TALLY_COMPANY_NOT_OPEN if any_company_open else TALLY_COMPANY_NOT_DETECTED
        message = ("Tally is connected, but no company is open." if failure_type == TALLY_COMPANY_NOT_DETECTED else
                   f"The requested Tally company '{requested_company}' is not open." +
                   (f" Tally currently has '{', '.join(open_company_names)}' open." if open_company_names else ""))
    elif not gstin:
        failure_type = TALLY_COMPANY_GSTIN_READ_FAILED
        message = f"Tally company '{name}' is open, but its GST registration/GSTIN could not be read from the company record or its tax units."
    else:
        failure_type = ""
        message = "Tally company and GST registration detected."

    connection = {"host": probe_host, "port": probe_port, "port_reachable": port_reachable,
                  "http_connected": http_connected, "odbc_connected": odbc_connected,
                  "read_connected": read_connected}
    company_read = {"requested_company": requested_company, "detected_company": name,
                    "company_open": company_matched, "state": state, "gstin": gstin,
                    "gstin_read_source": registration.get("read_source", ""), "failure_type": failure_type,
                    **active_period}
    return {"odbc_connected": odbc_connected, "read_connected": read_connected,
            "company_detected": company_matched, "company_open": company_matched,
            "company_name": name, "company": name, "company_gstin": gstin, "gstin": gstin,
            "company_state": state, "state": state, "failure_type": failure_type,
            **active_period,
            "gst_registration_status": registration.get("registration_status", "Unknown"),
            "registration_type": registration.get("registration_type", ""),
            "gstin_read_source": registration.get("read_source", ""),
            "available_tally_gstins": json_registration.get("available_gstins", [gstin] if gstin else []),
            "tally_version_detected": json_registration.get("tally_version", ""),
            **identity, **{key: json_registration.get(key) for key in ("selected_company", "uploaded_gstin", "json_company_request_attempted", "json_company_response_received", "gst_registrations_found", "gst_registration_records_found", "gstins_found", "selected_gstin", "match")},
            "can_import": bool(name and gstin), "dry_run": settings.TALLY_DRY_RUN, "message": message,
            "port_reachable": port_reachable, "http_connected": http_connected, "http_attempted": http_attempted,
            "odbc_attempted": odbc_attempted, "odbc_error": odbc_error, "http_error": http_error,
            "open_companies": open_company_names,
            "connection": connection, "company_read": company_read}


def verify_tally_company(source_gstin, status, source_company=None):
    """Verify the currently open Tally company primarily by exact GSTIN match.

    GSTIN is the authoritative company identity for GST-enabled companies: once
    the normalized source GSTIN and the normalized GSTIN of the currently open
    Tally company are both readable and equal, the company is verified even if
    the company-name strings differ only in punctuation/spacing/capitalization,
    or are different display names entirely. A GSTIN mismatch fails verification
    even when the names match exactly. ``name_match`` is retained only as an
    informational diagnostic and must never override an exact GSTIN match.
    """
    source_company = source_company or {}
    source = normalize_gstin(source_gstin)
    tally = normalize_gstin(status.get("company_gstin") or status.get("gstin"))
    uploaded_name = str(source_company.get("selected_tally_company") or source_company.get("company_name")
                        or source_company.get("trade_name") or source_company.get("legal_name") or "").strip()
    tally_name = str(status.get("company_name") or status.get("company") or "").strip()
    normalized_uploaded_name = normalize_company_name(uploaded_name)
    normalized_tally_name = normalize_company_name(tally_name)
    name_match = bool(normalized_uploaded_name and normalized_tally_name and normalized_uploaded_name == normalized_tally_name)
    gstin_status = "UNREADABLE" if not tally else "MATCH" if source and tally == source else "MISMATCH"
    gstin_match = None if gstin_status == "UNREADABLE" else gstin_status == "MATCH"
    connected = bool(status.get("read_connected", status.get("odbc_connected")) and status.get("company_detected"))
    # GSTIN is the authoritative identity: an exact GSTIN match verifies the
    # company regardless of company-name formatting/display differences.
    verified = bool(connected and source and tally and gstin_match is True)
    upstream_failure = str(status.get("failure_type") or "")
    if verified:
        state = "MATCHED"
    elif upstream_failure in CONNECTION_FAILURE_CODES:
        # The connection layer already knows exactly why the GSTIN is unreadable
        # (port down, ODBC down, wrong/no company open, ...). Surface that specific
        # cause instead of collapsing every unreadable case into GSTIN_NOT_READABLE.
        state = upstream_failure
    elif gstin_status == "UNREADABLE":
        state = "GSTIN_NOT_READABLE"
    elif gstin_status == "MISMATCH":
        state = "GSTIN_MISMATCH"
    else:
        state = TALLY_COMPANY_NOT_OPEN
    code = state
    default_messages = {
        "MATCHED": "Tally company verified successfully using GSTIN.",
        "GSTIN_MISMATCH": "The selected Tally company GSTIN does not match the uploaded company GSTIN.",
        "GSTIN_NOT_READABLE": "The selected Tally company GSTIN could not be read.",
        TALLY_NOT_REACHABLE: "Tally is not reachable. Start TallyPrime and enable its Connectivity/ODBC server.",
        TALLY_ODBC_UNAVAILABLE: "The Tally ODBC connection failed.",
        TALLY_HTTP_UNAVAILABLE: "The Tally HTTP read failed.",
        TALLY_COMPANY_NOT_DETECTED: "Tally is connected, but no company is open.",
        TALLY_COMPANY_NOT_OPEN: "The selected company is not the company currently open in Tally.",
        TALLY_COMPANY_GSTIN_READ_FAILED: "The Tally company is open, but its GST registration/GSTIN could not be read.",
    }
    # A connection-layer message (naming the exact host/port/company) is more
    # specific than the generic default -- prefer it whenever this is a connection failure.
    message = status.get("message") if state in CONNECTION_FAILURE_CODES and status.get("message") else default_messages.get(state, "Tally company verification failed.")
    verification_diagnostics = {
        "uploaded_company": uploaded_name, "normalized_uploaded_company": normalized_uploaded_name,
        "detected_company": tally_name, "normalized_detected_company": normalized_tally_name,
        "uploaded_gstin": source, "tally_gstin": tally, "gstin_readable": bool(tally),
        "gstin_match": gstin_match, "company_name_match": name_match, "verification_code": state,
    }
    return {"tally_connection": status, "source_company": {**source_company, "gstin": source},
            "company_verified": verified, "verification": state, "verification_code": state,
            "ready_for_master_check": verified,
            "ready_for_voucher_import": verified, "can_import": verified, "ready": verified,
            "verification_method": "GSTIN" if verified else "",
            "uploaded_company_name": uploaded_name, "tally_company_name": tally_name,
            "normalized_uploaded_company_name": normalized_uploaded_name,
            "normalized_tally_company_name": normalized_tally_name, "company_name_match": name_match,
            "tally_gstin": tally, "uploaded_gstin": source, "gstin_readable": bool(tally),
            "gstin_status": gstin_status, "gstin_match": gstin_match, "warning": "",
            "code": code, "message": message,
            "connection": status.get("connection") or {
                          "host": status.get("host", ""), "port": status.get("port"),
                          "port_reachable": status.get("port_reachable"), "http_connected": status.get("http_connected"),
                          "odbc_connected": status.get("odbc_connected"), "read_connected": status.get("read_connected")},
            "verification_diagnostics": verification_diagnostics,
            "company_read": status.get("company_read") or {"requested_company": "", "detected_company": tally_name,
                          "state": status.get("company_state") or status.get("state") or "", "gstin": tally,
                          "financial_year_from": status.get("financial_year_from", ""),
                          "financial_year_to": status.get("financial_year_to", ""),
                          "financial_year": status.get("financial_year", ""),
                          "financial_year_available": bool(status.get("financial_year_from") and status.get("financial_year_to")),
                          "financial_year_error": "" if status.get("financial_year_from") and status.get("financial_year_to") else "TALLY_FINANCIAL_YEAR_UNAVAILABLE",
                          "gstin_read_source": status.get("gstin_read_source", ""), "failure_type": upstream_failure}}


# Backward-compatible name for callers outside the Step 4 service.
verify_source_company = verify_tally_company

def odbc_existing_masters(connect=None, include_details=False):
    """Return master names/GSTIN mappings through read-only ODBC.

    ``names`` maps ``casefold(name) -> raw Tally name`` so a reuse decision can
    write vouchers against the ledger's exact existing spelling instead of a
    regenerated canonical name (see ``tally.mappings.normalize_ledger_key``).
    """
    if connect is None:
        import pyodbc
        connect = pyodbc.connect
    connection_string = settings.TALLY_ODBC_CONNECTION_STRING.strip() or f"DSN={settings.TALLY_ODBC_DSN}"
    connection = connect(connection_string, timeout=settings.TALLY_ODBC_TIMEOUT)
    names, gstins, details = {}, {}, {}
    try:
        cursor = connection.cursor()
        ledger_query = ('SELECT $Name, $GSTRegistrationNo, $Parent, $GSTDutyHead, $RateOfTaxCalculation, $GSTApplicable, $GSTTypeOfSupply FROM Ledger'
                        if include_details else 'SELECT $Name, $GSTRegistrationNo FROM Ledger')
        for object_name, query in (("Ledger", ledger_query),
                                   ("Stock Item", 'SELECT $Name, $GSTRegistrationNo FROM StockItem'),
                                   ("Unit", 'SELECT $Name, $GSTRegistrationNo FROM Unit')):
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
            except Exception:
                if object_name != "Ledger": continue
                cursor.execute('SELECT $Name, $GSTRegistrationNo FROM Ledger')
                rows = cursor.fetchall()
            for row in rows:
                name = str(row[0] or "").strip(); gstin = normalize_gstin(row[1] if len(row) > 1 else "")
                if name: names[name.casefold()] = name
                if name and _valid_gstin(gstin): gstins[gstin] = name
                if name and object_name == "Ledger":
                    details[name.casefold()] = {"name": name, "gstin": gstin,
                        "parent": str(row[2] or "").strip() if len(row) > 2 else "",
                        "tax_type": str(row[3] or "").strip() if len(row) > 3 else "",
                        "gst_rate": str(row[4] or "").strip() if len(row) > 4 else "",
                        "taxability": str(row[5] or "").strip() if len(row) > 5 else "",
                        "supply_type": str(row[6] or "").strip() if len(row) > 6 else ""}
    finally:
        connection.close()
    return (names, gstins, details) if include_details else (names, gstins)


def odbc_sales_prerequisites(ledger_names, connect=None):
    """Read exact ledger groups and Sales voucher numbering configuration."""
    if connect is None:
        import pyodbc
        connect = pyodbc.connect
    connection_string = settings.TALLY_ODBC_CONNECTION_STRING.strip() or f"DSN={settings.TALLY_ODBC_DSN}"
    connection = connect(connection_string, timeout=settings.TALLY_ODBC_TIMEOUT)
    result = {"ledgers": {}, "voucher_type": {}}
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT $Name, $Parent FROM Ledger')
        wanted = {str(name).strip().casefold() for name in ledger_names}
        for row in cursor.fetchall():
            name = str(row[0] or "").strip()
            if name.casefold() in wanted:
                result["ledgers"][name] = {"group": str(row[1] or "").strip()}
        for query in ('SELECT $Name, $MethodOfVchNumbering, $IsActive FROM VoucherType',
                      'SELECT $Name, $MethodOfVoucherNumbering, $IsActive FROM VoucherType'):
            try:
                cursor.execute(query)
                row = next((item for item in cursor.fetchall() if str(item[0] or "").strip().casefold() == "sales"), None)
                if row:
                    result["voucher_type"] = {"name": str(row[0] or "").strip(),
                                              "numbering_method": str(row[1] or "").strip(),
                                              "active": str(row[2] or "").strip()}
                    break
            except Exception:
                continue
    finally:
        connection.close()
    return result


def odbc_company_period(connect=None, requested_company=""):
    """Read the selected company's financial-year/books range without writes."""
    if connect is None:
        import pyodbc
        connect = pyodbc.connect
    connection_string = settings.TALLY_ODBC_CONNECTION_STRING.strip() or f"DSN={settings.TALLY_ODBC_DSN}"
    try:
        connection = connect(connection_string, timeout=settings.TALLY_ODBC_TIMEOUT)
    except Exception as exc:
        if requested_company and getattr(settings, "TALLY_WRITE_FORMAT", "XML") == "JSON":
            from .json_company_reader import get_tally_company_gst_registration
            result = get_tally_company_gst_registration(requested_company)
            if result.get("company_open"):
                return {"company": requested_company, "financial_year_from": result.get("financial_year_from"),
                        "books_from": result.get("books_from"), "ending_at": result.get("ending_at"),
                        "read_source": "JSON_COMPANY", "odbc_error": str(exc)}
        raise
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT $Name, $StartingFrom, $BooksFrom, $EndingAt FROM Company')
        wanted = str(requested_company or "").strip().casefold()
        row = next((item for item in cursor.fetchall() if item and (not wanted or str(item[0] or "").strip().casefold() == wanted)), None)
        if not row: return {"company": "", "financial_year_from": None, "books_from": None, "ending_at": None}
        books_from, ending_at = row[2], row[3]
        # $EndingAt reported over ODBC does not track the interactive Alt+F2
        # "Change Period" the way it visually appears to -- it reads back equal
        # to $BooksFrom regardless of what period the user has set, so treating
        # it as a real upper bound produces a false TALLY_CURRENT_PERIOD_MISMATCH
        # for every company. json_company_reader.py already discards it on the
        # same condition for the JSON read path; mirror that here.
        if books_from and ending_at and ending_at <= books_from: ending_at = None
        return {"company": str(row[0] or "").strip(), "financial_year_from": row[1], "books_from": books_from, "ending_at": ending_at}
    finally:
        connection.close()
