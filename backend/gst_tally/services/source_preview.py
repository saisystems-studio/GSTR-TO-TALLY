"""Pre-import preview: reads the same canonical, normalized invoice rows the
real import will persist (see canonical_invoice.py), so what the user sees
on the Preview screen can never disagree with what actually gets saved.

Returns dict-shaped rows keyed by canonical field name plus a matching
column list (key/label/format) chosen per return type, so the frontend never
has to guess which raw header meant what.
"""
from gst_tally.utils.file_utils import first_value, json_safe
from gst_tally.utils.uploaded_files import file_type_label, validate_upload_type
from . import canonical_invoice

RETURN_TYPES = canonical_invoice.RETURN_TYPES

# Permanent canonical preview/table-grid column order -- identical across
# GSTR-1, GSTR-2A and GSTR-2B, and across JSON/Excel/CSV sources (the preview
# grid always uses this exact 13-column order; Cess always sits immediately
# after IGST). "Customer GSTIN" is the customer_gstin field, which already
# means the same thing for every return type -- the counterparty GSTIN
# tally/mappings.py books the voucher against (see
# canonical_invoice.normalize_row). The supplier_gstin-derived "GSTIN" column
# was dropped: for GSTR-2A/2B it always duplicated Customer GSTIN, and for
# GSTR-1 it was never populated per row.
CANONICAL_COLUMNS = [
    ("invoice_date", "Invoice Date", "date"),
    ("voucher_date", "Voucher Date", "date"),
    ("customer_gstin", "Customer GSTIN", None),
    ("invoice_no", "Invoice No", None),
    ("taxable_value", "Taxable Value", "money"),
    ("tax_percent", "Tax %", "percent"),
    ("cgst", "CGST", "money"),
    ("sgst", "SGST", "money"),
    ("igst", "IGST", "money"),
    ("cess", "Cess", "money"),
    ("invoice_value", "Invoice Value", "money"),
    ("state_code", "State code", None),
    ("reverse_charge", "Reverse Charge", None),
    ("invoice_type", "Invoice Type", None),
]

PREVIEW_COLUMNS = {return_type: CANONICAL_COLUMNS for return_type in RETURN_TYPES}


EXTRA_COLUMN_PREFIX = "extra:"

# Preview-only extra header aliases, layered on top of (never edited into)
# canonical_invoice.RETURN_TYPE_ALIASES -- the registry real parsing/import
# uses. These exist purely to widen what the read-only preview can resolve
# straight from source_line when the canonical field came back empty, e.g. a
# bare "GSTIN" or "Party Name" column that canonical_invoice.py doesn't (and,
# for import correctness, should not on its own) treat as authoritative for
# every return type. Kept return-type-directional on purpose: GSTR-2A/2B
# never gets a "recipient"-worded alias here, since for a purchase return
# that column is the filer's own GSTIN (see COMPANY_ALIASES), not the
# supplier's -- reusing it would silently swap in the wrong party, not just
# fix a blank cell.
_PREVIEW_EXTRA_ALIASES = {
    "GSTR1": {
        "customer_gstin": ["GSTIN", "GSTIN of Recipient", "Party GSTIN", "gstin", "cgstin"],
        "customer_name": ["Party Name", "party_name"],
    },
    "GSTR2A": {
        "supplier_gstin": ["GSTIN", "Party GSTIN", "gstin", "cgstin"],
        "supplier_name": ["Party Name", "party_name"],
    },
}
_PREVIEW_EXTRA_ALIASES["GSTR2B"] = _PREVIEW_EXTRA_ALIASES["GSTR2A"]


def _preview_field_aliases(return_type, key):
    canonical_aliases = canonical_invoice.RETURN_TYPE_ALIASES.get(return_type, {}).get(key, [])
    extra_aliases = _PREVIEW_EXTRA_ALIASES.get(return_type, {}).get(key, [])
    return [*canonical_aliases, *extra_aliases]


def _resolve_preview_value(row, return_type, key):
    """The value already normalized onto the canonical row if present;
    otherwise a best-effort re-read of the raw source row (source_line)
    against every known alias for this field -- case/space/underscore/hyphen
    -insensitive via normalize_header, same as canonical_invoice's own
    matching. Never invents data: a value only appears here if some source
    column, under some recognized name, actually carried it."""
    value = row.get(key)
    if value not in (None, ""):
        return value
    source = row.get("source_line")
    if not isinstance(source, dict):
        return value
    aliases = _preview_field_aliases(return_type, key)
    if not aliases:
        return value
    fallback = first_value(source, aliases)
    return fallback if fallback not in (None, "") else value


def _extra_source_columns(rows, return_type):
    """Source headers present in the upload that aren't already covered by
    one of this return type's canonical field aliases -- e.g. a portal
    export's extra "IRN"/"Filing Date" column. Preserves first-seen order
    across all rows so the column set stays stable, and never drops a column
    just because one row happens to leave it blank (task spec section: show
    all meaningful source data, not just the fixed schema)."""
    aliases = canonical_invoice.RETURN_TYPE_ALIASES.get(return_type, {})
    consumed = {canonical_invoice.normalize_header(alias) for alias_list in aliases.values() for alias in alias_list}
    # Also exclude the preview-only fallback aliases (see _resolve_preview_value)
    # -- a header like "Party Name" that just filled a blank Customer/Supplier
    # Name cell must not additionally reappear as its own "extra" column.
    consumed |= {canonical_invoice.normalize_header(alias)
                 for type_aliases in _PREVIEW_EXTRA_ALIASES.values()
                 for alias_list in type_aliases.values() for alias in alias_list}
    headers = {}
    for row in rows:
        source = row.get("source_line") or {}
        if not isinstance(source, dict):
            continue
        for header in source:
            if header == "source_row_number" or header in headers:
                continue
            token = canonical_invoice.normalize_header(header)
            if not token or token in consumed:
                continue
            headers[header] = True
    return list(headers)


def preview_file(file_obj, return_type, sheet_name=None, *, page=None, page_size=50):
    if return_type not in RETURN_TYPES:
        raise ValueError("Unsupported return type")
    extension = validate_upload_type(file_obj, canonical_invoice.ALLOWED_EXTENSIONS)
    rows, metadata = canonical_invoice.parse_source(file_obj, return_type, extension=extension)
    diagnostics = metadata.get("diagnostics", {})
    column_defs = PREVIEW_COLUMNS[return_type]
    columns = [{"key": key, "label": label, "format": fmt} for key, label, fmt in column_defs]
    columns += [{"key": "_preview_status", "label": "Status", "format": None},
                {"key": "_preview_message", "label": "Message", "format": None}]
    extra_headers = _extra_source_columns(rows, return_type)
    columns += [{"key": f"{EXTRA_COLUMN_PREFIX}{header}", "label": header, "format": None} for header in extra_headers]
    total_rows = len(rows)
    if page is not None:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 50), 1), 200)
        rows = rows[(page - 1) * page_size:page * page_size]
    preview_rows = []
    for row in rows:
        source = row.get("source_line") or {}
        values = {key: _resolve_preview_value(row, return_type, key) for key, _, _ in column_defs}
        values.update({f"{EXTRA_COLUMN_PREFIX}{header}": source.get(header) if isinstance(source, dict) else None for header in extra_headers})
        values["_preview_status"] = "READY"
        values["_preview_message"] = ""
        preview_rows.append(json_safe(values))
    return {"return_type": return_type, "file_name": file_obj.name, "file_type": file_type_label(extension),
            "sheet_name": None, "sheets": [], "columns": columns, "rows": preview_rows,
            "row_count": total_rows, "diagnostics": diagnostics,
            **({"page": page, "page_size": page_size,
                "total_pages": max(1, (total_rows + page_size - 1) // page_size)} if page is not None else {})}


def _invoice_row_dict(invoice, *, include_source_line=True):
    """A GSTInvoice's persisted fields, reshaped into the same row dict shape
    preview_file() builds from a freshly parsed source row -- the model's
    field names already match CANONICAL_COLUMNS's keys 1:1 (see models.py),
    and source_line is stored on the invoice for the same alias fallback
    _resolve_preview_value() already does for a live upload."""
    return {**{key: getattr(invoice, key) for key, _, _ in CANONICAL_COLUMNS},
            "processing_state": invoice.processing_state,
            "source_line": (invoice.source_line or {}) if include_source_line else {}}


def preview_batch(batch, *, page=1, page_size=50, search=""):
    """Refresh-safe reconstruction of the exact same preview shape as
    preview_file(), read from the invoices already persisted for this batch
    instead of re-parsing the original upload -- the browser can never hand
    JS back the original File object after a reload, so Step 2 must be able
    to rebuild its table from the stored batch alone. Reuses the same column
    defs and value resolution as the live upload preview, so the two can
    never disagree."""
    return_type = batch.gst_return_type
    if return_type not in RETURN_TYPES:
        raise ValueError("Unsupported return type")
    # Preview is an inspection of the uploaded source, not an eligibility
    # query. Retain every persisted row, including duplicate/already-imported
    # rows; downstream voucher generation still selects actionable states.
    # The preview endpoint is a lazy grid data source: never materialise a
    # batch which may contain hundreds of thousands of invoices.
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 1), 200)
    search = (search or "").strip()
    invoices = batch.invoices.order_by("id")
    if search:
        from django.db.models import Q
        invoices = invoices.filter(
            Q(invoice_no__icontains=search) | Q(customer_gstin__icontains=search) |
            Q(customer_name__icontains=search) | Q(supplier_gstin__icontains=search) |
            Q(supplier_name__icontains=search) | Q(invoice_type__icontains=search) |
            Q(state_code__icontains=search)
        )
    row_count = invoices.count()
    start = (page - 1) * page_size
    # Preview uses only its fixed fields. Excluding source_line avoids loading
    # an often large JSON blob for every visible row.
    rows = list(invoices.only(
        "id", "invoice_date", "voucher_date", "customer_gstin", "invoice_no",
        "taxable_value", "tax_percent", "cgst", "sgst", "igst", "cess",
        "invoice_value", "state_code", "reverse_charge", "invoice_type",
        "processing_state",
    )[start:start + page_size])
    column_defs = PREVIEW_COLUMNS[return_type]
    columns = [{"key": key, "label": label, "format": fmt} for key, label, fmt in column_defs]
    columns += [{"key": "_preview_status", "label": "Status", "format": None},
                {"key": "_preview_message", "label": "Message", "format": None}]
    preview_rows = []
    for row in rows:
        # The preview query deliberately defers source_line, which can be a
        # large JSON payload. Canonical fields are selected directly, so
        # rendering a page never triggers one deferred-field query per row.
        row = _invoice_row_dict(row, include_source_line=False)
        values = {key: _resolve_preview_value(row, return_type, key) for key, _, _ in column_defs}
        values["_preview_status"] = "ALREADY_IMPORTED" if row.get("processing_state") in {"DUPLICATE", "COMPLETED"} else "READY"
        values["_preview_message"] = "Previously imported or duplicate; will not be sent again." if values["_preview_status"] == "ALREADY_IMPORTED" else ""
        preview_rows.append(json_safe(values))
    return {"return_type": return_type, "file_name": batch.file_name, "file_type": batch.file_type,
            "sheet_name": None, "sheets": [], "columns": columns, "rows": preview_rows,
            "row_count": row_count, "page": page, "page_size": page_size,
            "total_pages": max(1, (row_count + page_size - 1) // page_size),
            "search": search, "diagnostics": {}}
