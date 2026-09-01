import json
import logging
from datetime import datetime
from django.conf import settings
from .client import TallyClient, TallyConnectionError
def normalize_gstin(value): return "".join(str(value or "").split()).upper()

logger = logging.getLogger(__name__)


COMPANY_FIELDS = ["*"]
TAX_UNIT_FIELDS = ["*"]


def _value(value):
    if isinstance(value, dict) and "value" in value: return value.get("value")
    return value


def _date(value):
    text = str(_value(value) or "").strip()
    try: return datetime.strptime(text, "%Y%m%d").date()
    except ValueError: return None


def _find_value(value, *keys):
    wanted = {key.casefold() for key in keys}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in wanted:
                found = _value(child)
                if found not in (None, "", []): return found
        for child in value.values():
            found = _find_value(child, *keys)
            if found not in (None, "", []): return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, *keys)
            if found not in (None, "", []): return found
    return None


def _gstins_in(value):
    found = []
    if isinstance(value, dict):
        for child in value.values(): found.extend(_gstins_in(child))
    elif isinstance(value, list):
        for child in value: found.extend(_gstins_in(child))
    else:
        candidate = normalize_gstin(value)
        if len(candidate) == 15 and candidate[:2].isdigit() and candidate[2:7].isalpha() and candidate[7:11].isdigit() and candidate[13] == "Z":
            found.append(candidate)
    return list(dict.fromkeys(found))


def _request(client, company, subtype, object_id, fields):
    body = {"static_variables": [{"name": "svExportFormat", "value": "jsonex"},
                                  {"name": "svCurrentCompany", "value": company}], "fetch_list": fields}
    headers = {"Content-Type": "application/json", "version": "1", "tallyrequest": "Export",
               "type": "Object", "subtype": subtype, "id": object_id, "detailed-response": "Yes"}
    raw = client.post(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(), headers=headers)
    payload = json.loads(raw.decode("utf-8-sig", "replace"))
    return payload, payload.get("tallymessage", []) if isinstance(payload, dict) else []


def read_current_company_via_json(client):
    """Read the CURRENT/open Tally company via a JSON Object export, with no
    prior knowledge of its name required -- a blank object id/company on the
    singleton Company object resolves to whichever company is actually
    active in this Tally session, exactly like the XML Collection query
    (TYPE=Company, no name) already relies on for the same reason. This is
    also how the running Tally version (prod_maj_rel/prod_min_rel) is
    detected, since both come from the same response.

    Raises TallyConnectionError on a genuine transport failure (propagated,
    never swallowed here) and json.JSONDecodeError/ValueError/TypeError if
    the response wasn't valid/parseable JSON (e.g. a pre-JSON Tally) --
    callers distinguish "Tally unreachable" from "JSON unavailable".
    """
    payload, rows = _request(client, "", "Company", "", COMPANY_FIELDS)
    company = rows[0] if rows else {}
    name = str(_value(company.get("name")) or "").strip()
    state = str(_value(company.get("statename")) or "").strip()
    gstins = _gstins_in(company)
    gstin = gstins[0] if gstins else ""
    if name and (not gstin or not state):
        registration = get_tally_company_gst_registration(name, gstin, client=client)
        gstin = gstin or registration.get("gstin", "")
        state = state or registration.get("state", "")
    version = f"{payload.get('prod_maj_rel', '')}.{payload.get('prod_min_rel', '')}".strip(".")
    financial_year_from = _date(company.get("startingfrom"))
    financial_year_to = _date(company.get("endingat"))
    return {"transport": "JSON", "company_open": bool(name), "company_name": name,
            "company_gstin": gstin, "company_state": state,
            "financial_year_from": financial_year_from.isoformat() if financial_year_from else "",
            "financial_year_to": financial_year_to.isoformat() if financial_year_to else "",
            "financial_year": "", "tally_version_detected": version,
            "raw_request": {"static_variables": [{"name": "svExportFormat", "value": "jsonex"}],
                            "fetch_list": COMPANY_FIELDS},
            "raw_response": payload}


def get_tally_company_gst_registration(company_name, expected_gstin="", client=None):
    client = client or TallyClient(); company_name = str(company_name or "").strip(); expected = normalize_gstin(expected_gstin)
    diagnostics = {"selected_company": company_name, "uploaded_gstin": expected,
                   "json_company_request_attempted": True, "json_company_response_received": False,
                   "gst_registrations_found": 0, "gst_registration_records_found": 0,
                   "gstins_found": [], "selected_gstin": "", "match": False, "gstin_read_source": ""}
    try:
        company_payload, company_rows = _request(client, company_name, "Company", company_name, COMPANY_FIELDS)
        diagnostics["json_company_response_received"] = True
        company = company_rows[0] if company_rows else {}
        active_unit = str(_value(company.get("exciseunitname")) or "").strip()
        company_state = str(_value(company.get("statename")) or "").strip()
        diagnostics["company_response_fields"] = sorted(company.keys()) if settings.DEBUG else []
        diagnostics["company_gstin_field"] = _value(company.get("gstregistrationnumber")) or ""
        if settings.DEBUG:
            logger.debug("Tally Company read diagnostics: %s", {"company_name": _value(company.get("name")),
                         "response_fields": diagnostics["company_response_fields"],
                         "gstregistrationnumber": diagnostics["company_gstin_field"],
                         "state": company_state, "numgsttaxunit": _value(company.get("numgsttaxunit"))})
        registrations = []
        for direct in _gstins_in(company):
            registrations.append({"name": active_unit, "gstin": direct,
                                  "registration_type": str(_find_value(company, "registrationtype", "gstregistrationtype") or "").strip(),
                                  "state": str(_find_value(company, "statename") or "").strip(),
                                  "applicable_from": _date(_find_value(company, "fromdate", "applicablefrom")), "status": "Active"})
        # The active TaxUnit name is a standard Company method. Do not enumerate
        # invented/custom GSTRegistration collections: unsupported definitions can
        # leave Tally waiting on an Error in TDL dialog.
        # Current Tally Prime exposes the configured GST registration on the native
        # TaxUnit object as `gstregnumber`. The Company response does not expose the
        # unit ID, so use its state-derived native object name plus the standard
        # default name. These remain Object reads: no Collection or custom TDL.
        unit_names = list(dict.fromkeys(filter(None, (active_unit, f"{company_state} Registration" if company_state else "", "Default Tax Unit"))))
        diagnostics["tax_unit_objects_attempted"] = unit_names
        diagnostics["tax_unit_response_fields"] = {}
        diagnostics["tax_unit_gstin_fields"] = {}
        diagnostics["tax_unit_read_errors"] = {}
        for unit_name in unit_names:
            try:
                _, rows = _request(client, company_name, "TaxUnit", unit_name, TAX_UNIT_FIELDS)
            except (TallyConnectionError, json.JSONDecodeError, ValueError, TypeError) as exc:
                diagnostics["tax_unit_read_errors"][unit_name] = str(exc)
                continue
            for row in rows:
                diagnostics["tax_unit_response_fields"][unit_name] = sorted(row.keys()) if settings.DEBUG else []
                raw_gstin = _value(row.get("gstregnumber")) or ""
                diagnostics["tax_unit_gstin_fields"][unit_name] = raw_gstin
                if settings.DEBUG:
                    logger.debug("Tally TaxUnit read diagnostics: %s", {"object_id": unit_name,
                                 "response_fields": diagnostics["tax_unit_response_fields"][unit_name],
                                 "gstregnumber": raw_gstin})
                details = row.get("gstregistrationdetails") or [row]
                if not isinstance(details, list): details = [details]
                candidates = _gstins_in(raw_gstin) or _gstins_in(row)
                for index, candidate in enumerate(candidates):
                    detail = details[min(index, len(details) - 1)] if details else row
                    if candidate and candidate not in [item["gstin"] for item in registrations]:
                        registrations.append({"name": unit_name, "gstin": candidate,
                                              "registration_type": str(_find_value(detail, "registrationtype", "gstregistrationtype") or "").strip(),
                                              "state": str(_find_value(row, "statename") or _find_value(company, "statename") or "").strip(),
                                              "applicable_from": _date(_find_value(detail, "fromdate", "applicablefrom")), "status": "Active"})
            if expected and expected in [item["gstin"] for item in registrations]:
                break
        gstins = [item["gstin"] for item in registrations]
        selected = next((item for item in registrations if expected and item["gstin"] == expected), None)
        if not selected and len(registrations) == 1: selected = registrations[0]
        diagnostics.update(gst_registrations_found=len(registrations), gst_registration_records_found=len(registrations), gstins_found=gstins,
                           selected_gstin=selected["gstin"] if selected else "",
                           match=bool(selected and expected and selected["gstin"] == expected),
                           gstin_read_source="JSON_GST_REGISTRATION" if selected else "")
        version = f"{company_payload.get('prod_maj_rel', '')}.{company_payload.get('prod_min_rel', '')}".strip(".")
        books_from, ending_at = _date(company.get("booksfrom")), _date(company.get("endingat"))
        if books_from and ending_at and ending_at <= books_from: ending_at = None
        return {"company_name": company_name, "company_open": bool(company_rows), "gst_enabled": bool(_value(company.get("isgston"))),
                "pan": str(_find_value(company, "incometaxnumber", "panno", "pan") or "").strip(),
                "legal_name": str(_find_value(company, "mailingname", "legalname") or "").strip(),
                "trade_name": str(_find_value(company, "name", "companyname") or company_name).strip(),
                "gstin": selected["gstin"] if selected else "", "available_gstins": gstins,
                "registration_type": selected["registration_type"] if selected else "",
                "registration_status": selected.get("status", "") if selected else "",
                "state": selected["state"] if selected else company_state,
                "registration_name": selected["name"] if selected else active_unit,
                "applicable_from": selected["applicable_from"] if selected else None,
                "financial_year_from": _date(company.get("startingfrom")), "books_from": books_from,
                "ending_at": ending_at, "tally_version": version,
                "read_source": "JSON_GST_REGISTRATION" if selected else "JSON_COMPANY_AND_TAXUNIT", **diagnostics}
    except (TallyConnectionError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return {"company_name": company_name, "company_open": False, "gstin": "", "available_gstins": [],
                "read_source": "", "error": str(exc), **diagnostics}
