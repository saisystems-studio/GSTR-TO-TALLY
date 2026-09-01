import json
from .response_parser import TallyResponse


def parse_json_response(raw):
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, bytes) else str(raw or "")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return TallyResponse(errors=1, error=f"Invalid Tally JSON response: {exc}", raw=text)
    data = payload.get("data") if isinstance(payload, dict) else {}
    result = data.get("import_result", {}) if isinstance(data, dict) else {}
    def number(name):
        try: return int(result.get(name, 0) or 0)
        except (TypeError, ValueError): return 0
    def find_text(value, *names):
        wanted = {name.casefold() for name in names}
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).casefold() in wanted and child not in (None, "", [], {}):
                    if not isinstance(child, (dict, list)): return str(child).strip()
                    if isinstance(child, dict) and "value" in child: return str(child["value"]).strip()
            for child in value.values():
                found = find_text(child, *names)
                if found: return found
        elif isinstance(value, list):
            for child in value:
                found = find_text(child, *names)
                if found: return found
        return ""
    status = int(str(payload.get("status", "0")) == "1")
    line_error = find_text(payload, "line_error", "lineerror", "error_message", "errormessage")
    exception_text = find_text(payload, "exception", "exception_text", "exceptionmessage", "exception_description")
    description = find_text(payload, "description", "error_description", "errordesc")
    error_code = find_text(payload, "error_code", "errorcode", "code")
    errors, exceptions, cancelled = number("errors"), number("exceptions"), number("cancelled")
    if not status and not errors: errors = 1
    error = line_error or exception_text
    if not error and exceptions:
        voucher_number = str(result.get("vchnumber") or "").strip()
        subject = f"voucher {voucher_number}" if voucher_number else "voucher import"
        error = description or (f"Tally rejected {subject}: CREATED={number('created')}, ALTERED={number('altered')}, "
                                f"IGNORED={number('ignored')}, ERRORS={errors}, EXCEPTIONS={exceptions}; "
                                "no error text was returned by Tally.")
    if not error and cancelled: error = f"Tally cancelled {cancelled} object(s)"
    if not error and errors: error = description or "Tally rejected the JSON import"
    return TallyResponse(status=status, created=number("created"), altered=number("altered"), deleted=number("deleted"),
                         errors=errors, ignored=number("ignored"), exceptions=exceptions, cancelled=cancelled,
                         combined=number("combined"), last_vch_id=str(result.get("lastvchid") or ""),
                         last_mid=str(result.get("lastmid") or ""), line_error=line_error,
                         description=description, exception_text=exception_text, error_code=error_code, error=error, raw=text)
