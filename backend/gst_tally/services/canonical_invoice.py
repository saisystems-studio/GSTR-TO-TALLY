"""Single canonical parsing + normalization layer for GST return source files
(Excel/CSV/JSON) across GSTR-1, GSTR-2A and GSTR-2B.

Architecture:

    Excel ─┐
    CSV   ─┼─> format reader (read_excel/read_csv/read_json) -> raw rows
    JSON  ─┘   (literal source header/key -> raw cell value, per row)
                    |
                    v
        normalize_row() -- return-type-aware canonical mapping
                    |
                    v
        parse_excel / parse_csv / parse_json / parse_source
        (adds diagnostics, party/company metadata, validates)

Every valid row is normalized into exactly the GSTInvoice-storable field
names, so callers can do ``GSTInvoice(**row)`` directly:

    invoice_date, supplier_gstin, supplier_name, customer_gstin, customer_name,
    invoice_no, invoice_type, invoice_value, taxable_value, tax_percent,
    cgst, sgst, igst, cess, place_of_supply, state_code, reverse_charge,
    filing_period, filing_type, filing_date, source_type, source_line

(plus, for JSON rows only, the item-level extras GSTR-1's JSON already
carries per line item: item_name, description, hsn_sac, quantity, unit,
rate, discount, supply_type, other_charges, round_off.)

``customer_gstin`` keeps meaning exactly what the existing Tally
voucher-building pipeline (tally/mappings.py) already reads for every return
type: the counterparty GSTIN to book the voucher against -- the recipient
for GSTR-1, the supplier for GSTR-2A/2B. For a purchase return that value is
mirrored from ``supplier_gstin`` so nothing downstream of import needs to
change. ``supplier_gstin``/``supplier_name``/``customer_name`` are the new,
correctly-populated fields that make the source direction explicit for
display and for party extraction (see views.py's
_selected_party_gstin_field), instead of relying on customer_gstin's
overloaded meaning.
"""
import csv
import io
import logging
from decimal import Decimal

from openpyxl import Workbook, load_workbook

from gst_tally.tally.validators import state_name
from gst_tally.utils.file_utils import first_value, json_safe, normalize_header, parse_date, parse_decimal
from gst_tally.utils.uploaded_files import decode_csv_upload, load_json_upload, validate_upload_type

from .party_lookup import normalize_gstin, valid_gstin
from .source_party import add_source_party, log_source_parties

logger = logging.getLogger(__name__)

RETURN_TYPES = {"GSTR1", "GSTR2A", "GSTR2B"}
ALLOWED_EXTENSIONS = {"json", "csv", "xlsx", "xls"}

CANONICAL_FIELDS = ["invoice_date", "supplier_gstin", "supplier_name", "customer_gstin", "customer_name",
                     "invoice_no", "invoice_type", "invoice_value", "taxable_value", "tax_percent",
                     "cgst", "sgst", "igst", "cess", "place_of_supply", "state_code", "reverse_charge"]

# ==================================================================
# Return-type-aware alias registry -- one dict, shared by every format
# reader. Adding/broadening an alias here fixes Excel, CSV and JSON at once.
# ==================================================================
_COMMON_ALIASES = {
    "invoice_no": ["Invoice number", "Invoice No", "Invoice Number", "invoice_no", "inum"],
    "invoice_type": ["Invoice type", "Invoice Type", "invoice_type", "inv_typ"],
    "invoice_date": ["Invoice Date", "invoice_date", "idt"],
    "invoice_value": ["Invoice Value", "Invoice Value (₹)", "Total Value", "invoice_value", "val"],
    "taxable_value": ["Taxable Value", "taxable_value", "txval"],
    "tax_rate": ["Rate (%)", "GST Rate", "Rate", "Tax %", "tax_rate", "rt"],
    "igst": ["Integrated Tax", "IGST", "igst", "iamt"],
    "cgst": ["Central Tax", "CGST", "cgst", "camt"],
    "sgst": ["State/UT Tax", "SGST", "sgst", "samt"],
    "cess": ["Cess", "cess", "csamt", "Cess (₹)", "Cess(₹)", "Tax Amount / Cess(₹)"],
    "place_of_supply": ["Place of supply", "Place of Supply", "Place Of Supply", "place_of_supply", "pos"],
    "reverse_charge": ["Reverse Charge", "Supply Attract Reverse Charge", "reverse_charge", "rchrg"],
    "filing_period": ["Filing Period", "Return Period", "filing_period", "fp"],
    "filing_date": ["Filing Date", "filing_date"],
}

RETURN_TYPE_ALIASES = {
    "GSTR1": {
        **_COMMON_ALIASES,
        "customer_gstin": ["GSTIN/UIN of Recipient", "Recipient GSTIN", "Customer GSTIN", "Customer GSTIN/UIN",
                            "customer_gstin", "recipient_gstin", "ctin", "Buyer GSTIN"],
        "customer_name": ["Receiver Name", "Recipient Name", "Customer Name", "Trade Name", "Legal Name",
                           "customer_name", "recipient_name"],
    },
    "GSTR2A": {
        **_COMMON_ALIASES,
        "supplier_gstin": ["GSTIN of supplier", "Supplier GSTIN", "GSTIN/UIN of Supplier",
                            "Customer GSTIN", "Customer GSTIN/UIN", "GSTIN/UIN of Customer",
                            "supplier_gstin", "gstin", "ctin"],
        "supplier_name": ["Trade/Legal name", "Supplier Name", "Trade Name", "Legal Name",
                           "supplier_name", "trade_name", "legal_name"],
    },
}
RETURN_TYPE_ALIASES["GSTR2B"] = RETURN_TYPE_ALIASES["GSTR2A"]

PARTY_FIELDS = {"GSTR1": ("customer_gstin", "customer_name"),
                "GSTR2A": ("supplier_gstin", "supplier_name"),
                "GSTR2B": ("supplier_gstin", "supplier_name")}

COMPANY_ALIASES = ["GSTIN of recipient", "Recipient GSTIN", "Company GSTIN", "My GSTIN", "Taxpayer GSTIN"]

KNOWN_GST_RATES = [Decimal(v) for v in ("0", "0.1", "0.25", "1", "1.5", "3", "5", "6", "7.5", "12", "18", "28", "40")]


# ==================================================================
# Tax rate: only from an explicit rate column, or a reliable derivation.
# Never defaulted -- see task spec section 4.
# ==================================================================
def _derive_tax_rate(cgst, sgst, igst, taxable_value):
    if not taxable_value or taxable_value <= 0:
        return None
    present = [value for value in (cgst, sgst, igst) if value is not None]
    if not present:
        return None
    derived = (sum(present) / taxable_value) * 100
    nearest = min(KNOWN_GST_RATES, key=lambda rate: abs(rate - derived))
    if abs(nearest - derived) <= Decimal("0.5"):
        return nearest.quantize(Decimal("0.001"))
    return None


def _split_place_of_supply(value):
    """A GSTR export commonly writes Place of Supply as "33-Tamil Nadu" or
    just the bare 2-digit code "33"; sometimes it's already the state name.
    Populate both place_of_supply (name) and state_code (2-digit) whenever
    either form lets us derive the other."""
    text = str(value or "").strip()
    if not text:
        return "", ""
    if "-" in text:
        code, _, name = text.partition("-")
        code, name = code.strip(), name.strip()
        if code.isdigit() and len(code) <= 2 and name:
            return code.zfill(2), name
    if text.isdigit() and len(text) <= 2:
        code = text.zfill(2)
        return code, state_name(code)
    return "", text


def normalize_row(source_row, return_type):
    """``source_row``: dict of literal header/key -> raw value for one row.
    Returns the canonical dict, keyed exactly by GSTInvoice field names."""
    aliases = RETURN_TYPE_ALIASES[return_type]
    get = lambda field: first_value(source_row, aliases.get(field, []))
    party_gstin_field, party_name_field = PARTY_FIELDS[return_type]

    cgst = parse_decimal(get("cgst"))
    sgst = parse_decimal(get("sgst"))
    igst = parse_decimal(get("igst"))
    cess = parse_decimal(get("cess"))
    taxable_value = parse_decimal(get("taxable_value"))
    tax_rate = parse_decimal(get("tax_rate"))
    if tax_rate is None:
        tax_rate = _derive_tax_rate(cgst, sgst, igst, taxable_value)

    state_code, place_of_supply = _split_place_of_supply(get("place_of_supply"))

    row = {
        "invoice_date": parse_date(get("invoice_date")),
        "customer_gstin": "", "customer_name": "", "supplier_gstin": "", "supplier_name": "",
        "invoice_no": str(get("invoice_no") or "").strip(),
        "invoice_type": str(get("invoice_type") or "").strip(),
        "invoice_value": parse_decimal(get("invoice_value")),
        "taxable_value": taxable_value,
        "tax_percent": tax_rate,
        "cgst": cgst, "sgst": sgst, "igst": igst, "cess": cess,
        "place_of_supply": place_of_supply, "state_code": state_code,
        "reverse_charge": str(get("reverse_charge") or "").strip(),
        "filing_period": str(get("filing_period") or "").strip(),
        "filing_date": parse_date(get("filing_date")),
    }
    party_gstin = str(get(party_gstin_field) or "").strip().upper()
    party_name = str(get(party_name_field) or "").strip()
    row[party_gstin_field] = party_gstin
    row[party_name_field] = party_name
    if return_type in ("GSTR2A", "GSTR2B"):
        # customer_gstin is the field tally/mappings.py already reads for
        # every return type when building vouchers -- mirror the supplier
        # GSTIN into it so that pipeline needs zero changes.
        row["customer_gstin"] = party_gstin
    return row


# ==================================================================
# Header detection + flattening -- shared by Excel and CSV. Handles a
# two-level header (a merged group row plus a sub-column row, e.g.
# "Invoice Details" spanning Invoice number/type/Date/Value) by scanning
# for whichever row(s) contain at least 2 recognizable alias tokens.
# ==================================================================
def _alias_token_set():
    tokens = set()
    for aliases in RETURN_TYPE_ALIASES.values():
        for alias_list in aliases.values():
            tokens.update(normalize_header(alias) for alias in alias_list)
    return tokens


_ALIAS_TOKENS = _alias_token_set()


def _row_alias_hits(values):
    return sum(1 for value in values if value not in (None, "") and normalize_header(value) in _ALIAS_TOKENS)


def find_header_rows(matrix, max_scan=30):
    """Returns (header_row_index, subheader_row_index_or_None), 0-based."""
    limit = min(len(matrix), max_scan)
    header = None
    for index in range(limit):
        if _row_alias_hits(matrix[index]) >= 2:
            header = index
            break
    if header is None:
        return None, None
    if header + 1 < len(matrix) and _row_alias_hits(matrix[header + 1]) >= 2:
        return header, header + 1
    return header, None


def flatten_headers(matrix, header_row, subheader_row):
    """Combine a group-header row with an optional sub-header row into one
    column-name list: merged/blank group cells are forward-filled across
    their span, and a non-blank sub-header cell (the more specific name)
    always wins over the forward-filled group label above it."""
    main = list(matrix[header_row])
    if subheader_row is None:
        return [str(value).strip() if value not in (None, "") else "" for value in main]
    sub = list(matrix[subheader_row])
    width = max(len(main), len(sub))
    main += [None] * (width - len(main))
    sub += [None] * (width - len(sub))
    filled_main, last = [], ""
    for value in main:
        if value not in (None, ""):
            last = str(value).strip()
        filled_main.append(last)
    flattened = []
    for index in range(width):
        value = sub[index]
        flattened.append(str(value).strip() if value not in (None, "") else filled_main[index])
    return flattened


def _extract_company_gstin_from_summary(matrix, header_row):
    """The GST portal's GSTR-2A/2B (and GSTR-1) Excel/CSV exports print the
    return-owner's own GSTIN exactly once, in a label/value summary block
    above the invoice table (e.g. row "1. GSTIN" | "33CSCPM4566P1Z7") --
    never as a per-invoice column the way the party/supplier GSTIN is.
    COMPANY_ALIASES (used per-row, below) deliberately excludes a bare
    "GSTIN" label so it can never latch onto a per-row supplier/recipient
    GSTIN column; here a bare "GSTIN" label IS safe, because this scan is
    restricted to rows strictly above the detected table header and never
    sees invoice/party rows at all.

    Returns the first value that (a) sits in a row whose label plainly
    means "this taxpayer's own GSTIN" (not a supplier/recipient/ctin party
    label) and (b) is itself a validly-formatted GSTIN -- never a first
    guess or the first GSTIN-shaped string found anywhere in the sheet.
    """
    for row in matrix[:header_row]:
        for index, cell in enumerate(row or []):
            label = normalize_header(cell)
            if not label or "gstin" not in label:
                continue
            if "supplier" in label or "ctin" in label:
                continue
            for neighbour in list(row[index + 1:index + 4]) + [cell]:
                candidate = normalize_gstin(neighbour)
                if valid_gstin(candidate):
                    return candidate
    return ""


def _matrix_to_raw_rows(matrix, headers, data_start):
    width = len(headers)
    rows = []
    for source_row_number, values in enumerate(matrix[data_start:], data_start + 1):
        source = {headers[i] or f"column_{i}": values[i] for i in range(min(width, len(values)))}
        if not any(str(v).strip() for v in source.values() if v is not None):
            continue
        rows.append((source_row_number, source))
    return rows


# ==================================================================
# Excel reader
# ==================================================================
def _load_source_workbook(file_obj):
    extension = validate_upload_type(file_obj, {"xlsx", "xls"})
    if extension != "xls":
        return load_workbook(file_obj, read_only=True, data_only=True)
    try:
        import xlrd
    except ImportError as exc:
        raise ValueError("Legacy .xls support requires the xlrd package") from exc
    raw = file_obj.read()
    legacy = xlrd.open_workbook(file_contents=raw)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for legacy_sheet in legacy.sheets():
        sheet = workbook.create_sheet(legacy_sheet.name)
        for row_index in range(legacy_sheet.nrows):
            values = []
            for cell in legacy_sheet.row(row_index):
                value = xlrd.xldate_as_datetime(cell.value, legacy.datemode) if cell.ctype == xlrd.XL_CELL_DATE else cell.value
                values.append(value)
            sheet.append(values)
    return workbook


def _select_sheet(workbook):
    """First worksheet whose first 30 rows contain a recognizable invoice
    header; falls back to the first worksheet in the file."""
    for sheet in workbook.worksheets:
        matrix = [list(row) for row in sheet.iter_rows(values_only=True)]
        header, _ = find_header_rows(matrix[:30])
        if header is not None:
            return sheet, matrix
    sheet = workbook.worksheets[0]
    return sheet, [list(row) for row in sheet.iter_rows(values_only=True)]


def read_excel(file_obj):
    try:
        workbook = _load_source_workbook(file_obj)
        sheet, matrix = _select_sheet(workbook)
        header_row, subheader_row = find_header_rows(matrix[:30])
        if header_row is None:
            raise ValueError("No supported invoice sheet/header was found")
        headers = flatten_headers(matrix, header_row, subheader_row)
        data_start = (subheader_row if subheader_row is not None else header_row) + 1
        raw_rows = _matrix_to_raw_rows(matrix, headers, data_start)
        diagnostics = {"detected_header_row": header_row + 1,
                        "detected_subheader_row": (subheader_row + 1) if subheader_row is not None else None,
                        "source_rows": len(raw_rows),
                        "summary_gstin": _extract_company_gstin_from_summary(matrix, header_row)}
        return raw_rows, str(sheet.title or "B2B").upper(), diagnostics
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Invalid Excel file: {exc}") from exc


# ==================================================================
# CSV reader
# ==================================================================
_CSV_DELIMITERS = (",", ";", "\t", "|")


def _detect_csv_matrix(text):
    sample = text[:65536]
    try:
        detected = csv.Sniffer().sniff(sample, delimiters="".join(_CSV_DELIMITERS)).delimiter
    except csv.Error:
        counts = {delimiter: sample.count(delimiter) for delimiter in _CSV_DELIMITERS}
        detected = max(counts, key=counts.get) if any(counts.values()) else ","
    candidates = [detected, *[item for item in _CSV_DELIMITERS if item != detected]]
    rows = None
    for delimiter in candidates:
        try:
            candidate = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter,
                                         quotechar='"', doublequote=True, skipinitialspace=False, strict=False))
        except csv.Error:
            continue
        if rows is None or max((len(r) for r in candidate), default=0) > max((len(r) for r in rows), default=0):
            rows = candidate
        if any(len(r) > 1 for r in candidate):
            rows = candidate
            break
    if rows is None:
        raise ValueError("Unable to read this CSV file. Please check whether the file is damaged or unsupported.")
    width = max((len(r) for r in rows), default=0)
    return [list(r) + [""] * (width - len(r)) for r in rows]


def read_csv(file_obj):
    text = decode_csv_upload(file_obj)
    matrix = _detect_csv_matrix(text)
    header_row, subheader_row = find_header_rows(matrix[:30])
    if header_row is None:
        raise ValueError("CSV does not contain a recognizable header row")
    headers = flatten_headers(matrix, header_row, subheader_row)
    data_start = (subheader_row if subheader_row is not None else header_row) + 1
    raw_rows = _matrix_to_raw_rows(matrix, headers, data_start)
    diagnostics = {"detected_header_row": header_row + 1,
                    "detected_subheader_row": (subheader_row + 1) if subheader_row is not None else None,
                    "source_rows": len(raw_rows),
                    "summary_gstin": _extract_company_gstin_from_summary(matrix, header_row)}
    return raw_rows, "B2B", diagnostics


# ==================================================================
# JSON reader -- portal-shaped b2b[].inv[].itms[] nesting, shared across all
# three return types (only the meaning of "ctin" -- recipient vs supplier --
# changes, via normalize_row's return-type-aware party mapping).
# ==================================================================
def _flatten_leaf(value, output):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                _flatten_leaf(child, output)
            else:
                output[key] = child
    return output


def read_json(file_obj):
    payload = load_json_upload(file_obj)
    if not isinstance(payload, dict):
        raise ValueError("Unsupported JSON structure")
    seller = payload.get("SellerDtls", {})
    company_candidates = [payload.get("gstin"), payload.get("supplier_gstin"), payload.get("seller_gstin"), seller.get("Gstin")]
    filing_period = str(payload.get("fp", ""))
    raw_rows = []
    for party_index, party in enumerate(payload.get("b2b", []) or [], 1):
        gstin = str(party.get("ctin") or party.get("gstin") or "").strip().upper()
        for invoice_index, invoice in enumerate(party.get("inv", []) or [], 1):
            items = invoice.get("itms", []) or [{}]
            for item_index, item in enumerate(items, 1):
                detail = item.get("itm_det", item) if isinstance(item, dict) else {}
                flat = {}
                _flatten_leaf(party, flat)
                _flatten_leaf(invoice, flat)
                _flatten_leaf(detail, flat)
                flat["ctin"] = gstin
                label = f"b2b:{party_index}:inv:{invoice_index}:item:{item_index}"
                raw_rows.append((label, flat, party, invoice, detail))
    diagnostics = {"detected_header_row": None, "detected_subheader_row": None, "source_rows": len(raw_rows)}
    return raw_rows, "B2B", diagnostics, filing_period, company_candidates, seller


# ==================================================================
# Per-format entry points -- each finalizes raw rows into the canonical
# schema, extracts party/company metadata, and computes diagnostics.
# ==================================================================
def _finalize_tabular(raw_rows, return_type, source_type, header_diag, source_format):
    rows, parties, company_candidates, company_source = [], {}, [], {}
    party_gstin_field, _ = PARTY_FIELDS[return_type]
    # The return-owner's own GSTIN, when the sheet has one (see
    # _extract_company_gstin_from_summary): checked first so a genuine
    # summary-block GSTIN wins even on a sheet that also happens to carry a
    # per-row COMPANY_ALIASES column.
    summary_gstin = header_diag.get("summary_gstin", "")
    if summary_gstin:
        company_candidates.append(summary_gstin)
    for row_number, source in raw_rows:
        canonical = normalize_row(source, return_type)
        canonical["filing_type"] = return_type
        canonical["source_type"] = source_type
        canonical["source_line"] = json_safe({"source_row_number": row_number, **source})
        rows.append(canonical)
        add_source_party(parties, canonical.get(party_gstin_field, ""), source)
        company_value = first_value(source, COMPANY_ALIASES)
        if company_value:
            company_candidates.append(company_value)
            company_source = source
    log_source_parties(parties)
    return _wrap(rows, return_type, header_diag, parties, company_candidates, company_source, source_format)


def parse_excel(file_obj, return_type):
    if return_type not in RETURN_TYPES:
        raise ValueError("Unsupported return type")
    raw_rows, source_type, header_diag = read_excel(file_obj)
    return _finalize_tabular(raw_rows, return_type, source_type, header_diag, "EXCEL")


def parse_csv(file_obj, return_type):
    if return_type not in RETURN_TYPES:
        raise ValueError("Unsupported return type")
    raw_rows, source_type, header_diag = read_csv(file_obj)
    return _finalize_tabular(raw_rows, return_type, source_type, header_diag, "CSV")


def parse_json(file_obj, return_type):
    if return_type not in RETURN_TYPES:
        raise ValueError("Unsupported return type")
    raw_rows, source_type, header_diag, filing_period, company_candidates, seller = read_json(file_obj)
    rows, parties = [], {}
    party_gstin_field, _ = PARTY_FIELDS[return_type]
    company_source = seller
    for label, flat, party, invoice, detail in raw_rows:
        canonical = normalize_row(flat, return_type)
        canonical["filing_period"] = canonical.get("filing_period") or filing_period
        canonical["filing_type"] = return_type
        canonical["filing_date"] = canonical.get("filing_date") or parse_date(invoice.get("fldtr1") or party.get("fldtr1"))
        canonical["source_type"] = source_type
        canonical.update({
            "item_name": str(detail.get("nm") or detail.get("item_name") or detail.get("description") or "").strip(),
            "description": str(detail.get("desc") or detail.get("description") or "").strip(),
            "hsn_sac": str(detail.get("hsn_sc") or detail.get("hsn") or detail.get("sac") or "").strip(),
            "quantity": parse_decimal(detail.get("qty") or detail.get("quantity")),
            "unit": str(detail.get("uqc") or detail.get("unit") or "").strip(),
            "rate": parse_decimal(detail.get("urt") or detail.get("rate")),
            "discount": parse_decimal(detail.get("discount")),
            "supply_type": str(detail.get("supply_type") or invoice.get("supply_type") or "").strip(),
            "other_charges": parse_decimal(invoice.get("other_charges")),
            "round_off": parse_decimal(invoice.get("round_off")),
        })
        canonical["source_line"] = json_safe({"source_row_number": label, **detail}) if isinstance(detail, dict) else {}
        rows.append(canonical)
        add_source_party(parties, canonical.get(party_gstin_field, ""), party, invoice, detail)
        if not company_source:
            company_source = party
    log_source_parties(parties)
    result = _wrap(rows, return_type, header_diag, parties, company_candidates, company_source, "JSON")
    result[1]["return_period"] = filing_period
    return result


def parse_source(file_obj, return_type, extension=None):
    """Auto-detects the file format (used by the generic import/preview
    endpoints, which accept any of the three formats for one return type)."""
    extension = extension or validate_upload_type(file_obj, ALLOWED_EXTENSIONS)
    if extension == "json":
        return parse_json(file_obj, return_type)
    if extension == "csv":
        return parse_csv(file_obj, return_type)
    if extension in ("xlsx", "xls"):
        return parse_excel(file_obj, return_type)
    raise ValueError("Unsupported input format")


def _diagnostics_counts(rows, return_type):
    def count(field):
        return sum(1 for row in rows if row.get(field) not in (None, ""))
    return {
        "supplier_gstin_count": count("supplier_gstin"),
        "customer_gstin_count": count("customer_gstin") if return_type == "GSTR1" else 0,
        "invoice_number_count": count("invoice_no"),
        "invoice_date_count": count("invoice_date"),
        "invoice_value_count": count("invoice_value"),
        "taxable_value_count": count("taxable_value"),
        "tax_rate_count": count("tax_percent"),
    }


def _validate_normalized(diagnostics):
    """If the source clearly had rows but essentially nothing mapped to a
    real field, the header/alias mapping failed -- don't silently continue
    and persist a batch of blank invoices (task spec section 11)."""
    source_rows = diagnostics.get("source_rows", 0)
    if source_rows < 3:
        return
    signal = (diagnostics["invoice_number_count"] + diagnostics["supplier_gstin_count"] + diagnostics["customer_gstin_count"] + diagnostics["invoice_date_count"])
    if signal == 0:
        raise ValueError(
            "SOURCE_COLUMN_MAPPING_FAILED: the file contains rows, but no invoice number, party GSTIN or invoice "
            f"date could be mapped from the detected header. Diagnostics: {diagnostics}")


def _wrap(rows, return_type, header_diag, parties, company_candidates, company_source, source_format):
    diagnostics = {"return_type": return_type, "source_format": source_format, **header_diag,
                    "normalized_invoice_rows": len(rows), **_diagnostics_counts(rows, return_type)}
    _validate_normalized(diagnostics)
    logger.info("Normalized invoice sample (first 3) for %s: %s", return_type, json_safe(rows[:3]))
    from .company import source_company_metadata
    metadata = {"parties": parties, "diagnostics": diagnostics, **source_company_metadata(company_candidates, company_source)}
    return rows, metadata
