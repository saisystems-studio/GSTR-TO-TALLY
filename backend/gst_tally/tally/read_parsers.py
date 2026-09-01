"""Parsers for Tally **read** (export/query) responses.

These are deliberately separate from :mod:`gst_tally.tally.response_parser`,
which parses *write* acknowledgements. A read response is never fed through the
import parser and an import acknowledgement is never accepted as query data.

Envelope classification
-----------------------
TallyPrime does not echo the request verb we sent. A report exported as XML is
emitted in **re-importable** form: its HEADER carries ``Import Data`` and its
REQUESTDESC carries ``All Masters`` so the payload can be fed straight back into
another company. Verified against the live TallyPrime instance:

* ``Export Data``/``Day Book`` with no vouchers in range returns
  ``<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST>...<IMPORTDATA>
  <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>...<REQUESTDATA/>``.
* A request Tally rejects returns ``<RESPONSE><LINEERROR>...</LINEERROR></RESPONSE>``.

So the header text is *not* a usable discriminator; the payload container is.
A response is a data export when it carries an export payload section
(``REQUESTDATA`` or ``BODY/DATA``) and is neither an error envelope nor an
import *result* (``IMPORTRESULT`` / ``RESPONSE`` counters).
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from .odbc import normalize_company_name, normalize_gstin

# XML 1.0 forbids these control characters, but some TallyPrime exports emit
# numeric references to them. They carry no accounting data and would otherwise
# make a valid export unparsable, so only those forbidden refs are stripped.
_FORBIDDEN_DECIMAL = re.compile(r"&#(?:[0-8]|1[1-2]|1[4-9]|2[0-9]|3[01]);")
_FORBIDDEN_HEX = re.compile(r"&#x(?:[0-8BCEF]|1[0-9A-F]);", re.IGNORECASE)

IMPORT_RESULT_COUNTERS = ("CREATED", "ALTERED", "IGNORED", "ERRORS", "EXCEPTIONS", "COMBINED", "LASTVCHID")


def _decode(raw):
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")


def _sanitize(text):
    return _FORBIDDEN_HEX.sub("", _FORBIDDEN_DECIMAL.sub("", text))


def _text(node, *names):
    for name in names:
        for found in node.iter(name):
            value = " ".join(part.strip() for part in found.itertext() if part and part.strip()).strip()
            if value:
                return value
    return ""


def _tally_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = text.replace("-", "").replace("/", "")
    if len(cleaned) == 8 and cleaned.isdigit():
        for year_slice, month_slice, day_slice in ((slice(0, 4), slice(4, 6), slice(6, 8)),
                                                   (slice(4, 8), slice(2, 4), slice(0, 2))):
            try:
                return date(int(cleaned[year_slice]), int(cleaned[month_slice]), int(cleaned[day_slice])).isoformat()
            except ValueError:
                pass
    for pattern in ("%d-%b-%y", "%d-%b-%Y", "%d %b %y", "%d %b %Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return text


def _display_period(from_date, to_date):
    if not from_date or not to_date:
        return ""
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except ValueError:
        return f"{from_date} - {to_date}"
    return f"{start.strftime('%d %b %Y')} - {end.strftime('%d %b %Y')}"


def _rate(value):
    text = str(value or "").strip().replace("%", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return ""
    return format(number.normalize(), "f")


def _rate_from_name(name):
    match = re.fullmatch(r"\s*(?:gst\s*purchase|input\s*(?:c|s|i)gst)\s*@?\s*(\d+(?:\.\d+)?)\s*%\s*",
                         str(name or ""), re.IGNORECASE)
    return _rate(match.group(1)) if match else ""


def _ledger_entry(entry):
    name = _text(entry, "LEDGERNAME")
    explicit = [_rate(node.text) for tag in ("RATEOFINVOICETAX", "BASICRATEOFINVOICETAX")
                for node in entry.iter(tag) if _rate(node.text)]
    detail_rates = []
    detail_fields = []
    for child in entry:
        if "RATE" in child.tag.upper() or "GST" in child.tag.upper():
            detail_fields.append(child.tag)
    for detail in entry.iter("RATEDETAILS.LIST"):
        duty = _text(detail, "GSTRATEDUTYHEAD", "GSTDUTYHEAD", "DUTYHEAD")
        value = _rate(_text(detail, "GSTRATE", "RATE"))
        if value:
            detail_rates.append({"duty_head": duty, "rate": value})
    gst_rate = explicit[0] if explicit else ""
    if not gst_rate and detail_rates:
        by_head = {row["duty_head"].casefold(): Decimal(row["rate"]) for row in detail_rates}
        central = next((rate for head, rate in by_head.items() if "central" in head or "cgst" in head), Decimal("0"))
        state = next((rate for head, rate in by_head.items()
                      if "state" in head or "sgst" in head or "utgst" in head), Decimal("0"))
        integrated = next((rate for head, rate in by_head.items() if "integrated" in head or "igst" in head), Decimal("0"))
        gst_rate = _rate(integrated or (central + state) or Decimal(detail_rates[0]["rate"]))
    rate_source = "EXPLICIT_ALLOCATION" if explicit else "GST_RATE_DETAILS" if gst_rate else ""
    return {"ledger": name, "amount": _text(entry, "AMOUNT"),
            "is_deemed_positive": _text(entry, "ISDEEMEDPOSITIVE"),
            "gst_classification": _text(entry, "GSTDUTYHEAD", "TAXTYPE", "TAXABILITY"),
            "gst_rate": gst_rate, "gst_rate_source": rate_source,
            "gst_rate_details": detail_rates, "rate_detail_fields": sorted(set(detail_fields))}


def _is_import_result(root):
    """True only for a write acknowledgement, never for exported data."""
    if root.find(".//IMPORTRESULT") is not None:
        return True
    return root.tag.upper() == "RESPONSE" and any(root.find(f".//{tag}") is not None for tag in IMPORT_RESULT_COUNTERS)


def classify_envelope(root):
    """Return ``ERROR`` | ``IMPORT_RESULT`` | ``REPORT_EXPORT`` | ``DATA_EXPORT`` | ``UNKNOWN``."""
    error_text = _text(root, "LINEERROR", "EXCEPTIONDESC", "EXCEPTIONMESSAGE")
    if error_text:
        return "ERROR"
    if _is_import_result(root):
        return "IMPORT_RESULT"
    if root.find(".//REQUESTDATA") is not None:
        return "REPORT_EXPORT"
    if root.find("BODY/DATA") is not None or root.find(".//DATA") is not None:
        return "DATA_EXPORT"
    return "UNKNOWN"


def parse_export_envelope(raw, expected_company="", operation="tally_read"):
    """Validate a Tally read response and expose its metadata.

    Returns ``{"query_valid", "kind", "reason", "request", "report", "company",
    "company_match", "root", "raw"}``. ``query_valid`` is true only for a genuine
    export payload from the expected company; it is never inferred from a
    failure, and an empty payload is a valid result.
    """
    text = _decode(raw)
    base = {"query_valid": False, "kind": "", "reason": "", "request": "", "report": "",
            "company": "", "company_match": None, "root": None, "raw": text, "operation": operation}
    try:
        root = ET.fromstring(_sanitize(text))
    except ET.ParseError as exc:
        return {**base, "kind": "UNPARSABLE", "reason": f"Tally returned unparsable XML for {operation}: {exc}"}

    kind = classify_envelope(root)
    request = _text(root, "TALLYREQUEST")
    report = _text(root, "REPORTNAME")
    company = _text(root, "SVCURRENTCOMPANY")
    expected = normalize_company_name(expected_company)
    returned = normalize_company_name(company)
    company_match = None if not (expected and returned) else expected == returned
    result = {**base, "kind": kind, "request": request, "report": report,
              "company": company, "company_match": company_match, "root": root}

    if kind == "ERROR":
        return {**result, "reason": f"Tally rejected the {operation} request: {_text(root, 'LINEERROR', 'EXCEPTIONDESC', 'EXCEPTIONMESSAGE')}"}
    if kind == "IMPORT_RESULT":
        return {**result, "reason": (f"Tally returned an import acknowledgement, not query data, for {operation} "
                                     f"(request={request or '-'}, report={report or '-'})")}
    if kind == "UNKNOWN":
        return {**result, "reason": (f"Tally response carries no export payload for {operation} "
                                     f"(request={request or '-'}, report={report or '-'})")}
    if company_match is False:
        return {**result, "reason": (f"Tally answered {operation} for company '{company}' "
                                     f"but '{expected_company}' was requested")}
    return {**result, "query_valid": True}


def _voucher_record(node):
    number = _text(node, "VOUCHERNUMBER", "DSPVCHNUMBER", "VCHNUMBER")
    voucher_type = _text(node, "VOUCHERTYPENAME", "DSPVCHTYPE", "VCHTYPE")
    if not number and not voucher_type:
        return None
    master_id = (node.get("MASTERID") or _text(node, "MASTERID")).strip()
    guid = _text(node, "GUID")
    voucher_id = _text(node, "VOUCHERID")
    # TallyPrime's Invoice Voucher View export emits the same accounting
    # postings twice: once under LEDGERENTRIES.LIST and again under
    # ALLLEDGERENTRIES.LIST (its superset/consolidated view). Reading both
    # would double every entry; ALLLEDGERENTRIES.LIST is preferred when present
    # since it is the complete view, with LEDGERENTRIES.LIST as the fallback
    # for exports (and other report types) that only carry the plain list.
    entry_nodes = node.findall(".//ALLLEDGERENTRIES.LIST") or node.findall(".//LEDGERENTRIES.LIST")
    ledger_entries = [_ledger_entry(entry) for entry in entry_nodes]
    taxable_allocations = [entry for entry in ledger_entries
                           if entry["gst_rate"] and re.match(r"\s*gst\s*purchase", entry["ledger"], re.IGNORECASE)]
    return {"voucher_number": number, "voucher_type": voucher_type,
            "date": _text(node, "DATE", "VOUCHERDATE", "DSPVCHDATE"),
            "reference": _text(node, "REFERENCE", "VOUCHERREF"),
            "party": _text(node, "PARTYLEDGERNAME", "DSPVCHLEDACCOUNT", "PARTYNAME", "LEDGERNAME"),
            "guid": guid, "master_id": master_id,
            "alter_id": (node.get("ALTERID") or _text(node, "ALTERID")).strip(),
            "cancelled": _text(node, "ISCANCELLED").casefold() == "yes",
            "taxable_allocations": taxable_allocations,
            "ledger_entries": ledger_entries,
            "identifier": master_id or voucher_id or guid}


def _collect_vouchers(root):
    records, seen = [], set()
    for tag in ("VOUCHER", "DSPVCHDETAILS"):
        for node in root.iter(tag):
            record = _voucher_record(node)
            if not record:
                continue
            key = (record["voucher_type"].casefold(), record["voucher_number"].casefold(),
                   record["identifier"], record["date"])
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
    return records


def parse_voucher_query_response(raw, expected_company="", operation="voucher_collection_query"):
    """Parse a voucher **collection** export into identifier records."""
    envelope = parse_export_envelope(raw, expected_company, operation)
    if not envelope["query_valid"]:
        return {**envelope, "vouchers": [], "source": "VOUCHER_COLLECTION"}
    return {**envelope, "vouchers": _collect_vouchers(envelope["root"]), "source": "VOUCHER_COLLECTION"}


def parse_daybook_export(raw, expected_company="", operation="daybook_query"):
    """Parse a standard Day Book report export into identifier records."""
    envelope = parse_export_envelope(raw, expected_company, operation)
    if not envelope["query_valid"]:
        return {**envelope, "vouchers": [], "source": "DAY_BOOK"}
    return {**envelope, "vouchers": _collect_vouchers(envelope["root"]), "source": "DAY_BOOK"}


def parse_master_query_response(raw, expected_company="", operation="master_query"):
    """Parse a ledger/stock-item/unit collection export.

    ``names`` maps ``casefold(name) -> raw Tally name``, preserving the exact
    spelling/spacing/punctuation Tally has on record. Reuse decisions must
    write vouchers against that raw name, not a regenerated canonical one --
    see ``tally.mappings.normalize_ledger_key`` for the fuzzy-match key.
    """
    envelope = parse_export_envelope(raw, expected_company, operation)
    names, gstins = {}, {}
    if not envelope["query_valid"]:
        return {**envelope, "names": names, "gstins": gstins, "source": "MASTER_COLLECTION"}
    for tag in ("LEDGER", "STOCKITEM", "UNIT"):
        for node in envelope["root"].iter(tag):
            name = (node.get("NAME") or node.findtext("NAME") or "").strip()
            gstin = normalize_gstin(node.findtext(".//GSTREGISTRATIONNO") or "")
            if name:
                names[name.casefold()] = name
            if name and gstin:
                gstins[gstin] = name
    return {**envelope, "names": names, "gstins": gstins, "source": "MASTER_COLLECTION"}


def parse_company_query_response(raw, operation="company_query"):
    """Parse the open-company collection export."""
    envelope = parse_export_envelope(raw, operation=operation)
    if not envelope["query_valid"]:
        return {**envelope, "companies": [], "source": "COMPANY_COLLECTION"}
    companies = []
    for node in envelope["root"].iter("COMPANY"):
        name = (node.get("NAME") or node.findtext("NAME") or "").strip()
        if not name:
            continue
        financial_year_from = _tally_date(node.findtext("FINANCIALYEARFROM"))
        financial_year_to = _tally_date(node.findtext("FINANCIALYEARTO"))
        companies.append({"name": name, "state": (node.findtext("STATENAME") or "").strip(),
                          "gstin": normalize_gstin(node.findtext("GSTREGISTRATIONNUMBER") or ""),
                          "financial_year_from": financial_year_from,
                          "financial_year_to": financial_year_to,
                          "financial_year": _display_period(financial_year_from, financial_year_to),
                          "financial_year_available": bool(financial_year_from and financial_year_to),
                          "financial_year_error": "" if financial_year_from and financial_year_to else "TALLY_FINANCIAL_YEAR_UNAVAILABLE"})
    return {**envelope, "companies": companies, "source": "COMPANY_COLLECTION"}
