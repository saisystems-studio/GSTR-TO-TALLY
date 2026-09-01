"""Parser for Tally **write** (import) acknowledgements.

Read/export responses are parsed by :mod:`gst_tally.tally.read_parsers`; the two
are never interchanged.
"""
from dataclasses import dataclass
from xml.etree import ElementTree as ET


@dataclass
class TallyResponse:
    status: int = 0
    created: int = 0
    altered: int = 0
    errors: int = 0
    ignored: int = 0
    exceptions: int = 0
    cancelled: int = 0
    last_vch_id: str = ""
    last_mid: str = ""
    combined: int = 0
    deleted: int = 0
    line_error: str = ""
    description: str = ""
    exception_text: str = ""
    error_code: str = ""
    error: str = ""
    raw: str = ""
    tally_exception_details: str = ""

    @property
    def accepted(self):
        return not self.errors and not self.exceptions and not self.cancelled and (self.created > 0 or self.altered > 0)


def parse_response(raw):
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return TallyResponse(errors=1, error=f"Invalid Tally response: {exc}", raw=text)
    def number(name):
        try: return int(root.findtext(f".//{name}") or 0)
        except ValueError: return 0
    def first_text(*names):
        for name in names:
            for node in root.findall(f".//{name}"):
                value = " ".join(part.strip() for part in node.itertext() if part and part.strip()).strip()
                if value: return value
        return ""
    line_error = first_text("LINEERROR", "LINEERROR.LIST")
    exception_text = first_text("EXCEPTION", "EXCEPTIONMESSAGE", "EXCEPTIONDESC", "EXCEPTIONTEXT", "EXCEPTIONS.LIST")
    description = first_text("DESCRIPTION", "ERRORDESC", "STATUSDESC", "DESC", "MESSAGE", "WARNING")
    error_code = first_text("ERRORCODE", "CODE", "STATUSCODE")
    error = (line_error or exception_text or first_text("ERROR") or description).strip()
    errors = number("ERRORS"); exceptions = number("EXCEPTIONS"); cancelled = number("CANCELLED")
    if error and not errors: errors = 1
    # Known structural/counter tags that carry no diagnostic content of their
    # own -- excluded from the last-resort scan below so it surfaces only
    # genuinely unexplored text, not a restatement of fields already parsed.
    _known_tags = {"ENVELOPE", "HEADER", "VERSION", "TALLYREQUEST", "STATUS", "BODY", "DATA", "IMPORTRESULT",
                   "CREATED", "ALTERED", "DELETED", "ERRORS", "IGNORED", "EXCEPTIONS", "CANCELLED", "COMBINED",
                   "LASTVCHID", "LASTMID", "MASTERID", "REQUESTDATA", "REQUESTDESC", "REPORTNAME"}
    tally_exception_details = ""
    if exceptions and not error:
        # Section 15: LINEERROR/EXCEPTION*/DESCRIPTION were all empty, but
        # Tally reported EXCEPTIONS>0 -- don't throw the response away. Scan
        # every remaining leaf node (no children, non-empty text) for
        # anything the structured search above didn't recognise, so a
        # nonstandard/nested diagnostic tag is still captured instead of
        # silently discarded.
        leftovers = [f"{node.tag}={(node.text or '').strip()}" for node in root.iter()
                    if node.tag.upper() not in _known_tags and (node.text or "").strip() and not list(node)]
        tally_exception_details = "; ".join(dict.fromkeys(leftovers))
        error = (f"Tally reported {exceptions} exception(s); full response captured for diagnosis"
                 if not tally_exception_details else f"Tally reported {exceptions} exception(s): {tally_exception_details}")
    elif not error and cancelled:
        error = f"Tally cancelled {cancelled} object(s)"
    return TallyResponse(status=number("STATUS"), created=number("CREATED"), altered=number("ALTERED"),
                         deleted=number("DELETED"), errors=errors, ignored=number("IGNORED"),
                         exceptions=exceptions, cancelled=cancelled, combined=number("COMBINED"),
                         last_vch_id=(root.findtext(".//LASTVCHID") or root.findtext(".//MASTERID") or "").strip(),
                         last_mid=(root.findtext(".//LASTMID") or "").strip(), line_error=line_error,
                         description=description, exception_text=exception_text, error_code=error_code, error=error,
                         raw=text, tally_exception_details=tally_exception_details)


# Explicit name for the write-acknowledgement parser, paired with the import builders.
parse_import_response = parse_response
