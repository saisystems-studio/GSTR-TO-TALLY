from django.utils import timezone

from .source_preview import CANONICAL_COLUMNS


PAGE_WIDTH = 841.89
PAGE_HEIGHT = 595.28
MARGIN_X = 24
MARGIN_TOP = 28
MARGIN_BOTTOM = 28
TABLE_WIDTH = PAGE_WIDTH - (MARGIN_X * 2)
HEADER_HEIGHT = 24
ROW_HEIGHT = 20
MESSAGE_ROW_HEIGHT = 34

# Compact widths add up to the printable A4 landscape width.
COLUMN_WIDTHS = [
    45, 45, 72, 62, 49, 34, 43, 43, 43, 38, 53, 38, 46, 48, 48, 84,
]
COLUMN_LABELS = [
    "Invoice Date", "Voucher Date", "Customer GSTIN", "Invoice No",
    "Taxable Value", "Tax %", "CGST", "SGST", "IGST", "Cess",
    "Invoice Value", "State Code", "Reverse Charge", "Invoice Type",
    "Status", "Message",
]


def _pdf_text(value):
    text = "" if value is None else str(value)
    return text.encode("latin-1", "replace").decode("latin-1")


def _escape(value):
    return _pdf_text(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(value, width):
    words = _pdf_text(value).split()
    if not words:
        return [""]
    lines = []
    current = ""
    for word in words:
        while len(word) > width:
            lines.append(word[:width])
            word = word[width:]
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _format_value(value, fmt):
    if value in (None, ""):
        return ""
    if fmt == "percent":
        return f"{value}%"
    return str(value)


def _row_values(invoice):
    values = [
        _format_value(getattr(invoice, key, ""), fmt)
        for key, _, fmt in CANONICAL_COLUMNS
    ]
    state = getattr(invoice, "processing_state", "PENDING")
    status = "Already Imported" if state in {"DUPLICATE", "COMPLETED"} else "Ready"
    message = (
        "Previously imported or duplicate; will not be sent again."
        if status == "Already Imported" else ""
    )
    return values + [status, message]


def _draw_text(commands, x, y, text, size=6.2, align="left", width=None, bold=False):
    text = _pdf_text(text)
    if align == "right" and width is not None:
        x += width - (len(text) * size * 0.48)
    elif align == "center" and width is not None:
        x += max(0, (width - (len(text) * size * 0.48)) / 2)
    font = "F2" if bold else "F1"
    # Set the fill colour immediately before every text object. PDF text
    # inherits the previous graphics-state colour unless explicitly reset.
    commands.append(f"0 0 0 rg BT /{font} {size:g} Tf {x:g} {y:g} Td ({_escape(text)}) Tj ET")


def _draw_page_header(commands, batch, total, generated, page_number, total_pages):
    _draw_text(commands, MARGIN_X, PAGE_HEIGHT - MARGIN_TOP, "GSTR 2 Tally", 13, bold=True)
    _draw_text(commands, MARGIN_X, PAGE_HEIGHT - MARGIN_TOP - 17, "Invoice Preview", 9)
    _draw_text(commands, PAGE_WIDTH / 2 - 28, PAGE_HEIGHT - MARGIN_TOP, f"Batch: #{batch.id}", 9, bold=True)
    _draw_text(commands, PAGE_WIDTH - MARGIN_X - 150, PAGE_HEIGHT - MARGIN_TOP, f"Generated: {generated}", 7, align="right", width=150)
    _draw_text(commands, PAGE_WIDTH - MARGIN_X - 150, PAGE_HEIGHT - MARGIN_TOP - 14, f"Total Invoices: {total}", 8, align="right", width=150)
    _draw_text(commands, MARGIN_X, 14, f"Batch #{batch.id} | Page {page_number} of {total_pages}", 7)


def _draw_table_header(commands, top):
    x = MARGIN_X
    y = top - 13
    for label, width in zip(COLUMN_LABELS, COLUMN_WIDTHS):
        commands.append(f"0.8627 0.9255 0.9686 rg {x:g} {top - HEADER_HEIGHT:g} {width:g} {HEADER_HEIGHT:g} re f")
        commands.append(f"0.6157 0.7412 0.8275 RG {x:g} {top - HEADER_HEIGHT:g} {width:g} {HEADER_HEIGHT:g} re S")
        _draw_text(commands, x, y, label, 6.5, align="center", width=width, bold=True)
        x += width


def _draw_row(commands, values, top):
    height = _row_height(values)
    x = MARGIN_X
    y = top - 12
    formats = [fmt for _, _, fmt in CANONICAL_COLUMNS] + [None, None]
    alignments = [
        "center", "center", "left", "center", "right", "center", "right",
        "right", "right", "right", "right", "center", "center", "center",
        "center", "left",
    ]
    for value, width, fmt, alignment in zip(values, COLUMN_WIDTHS, formats, alignments):
        commands.append(f"0.6157 0.7412 0.8275 RG {x:g} {top - height:g} {width:g} {height:g} re S")
        lines = _wrap(value, max(4, int(width / 3.4))) if value else [""]
        for line_index, line in enumerate(lines[:2]):
            _draw_text(commands, x + 2, y - (line_index * 9), line, 6.5, alignment, width - 4)
        x += width
    return height


def _row_height(values):
    return MESSAGE_ROW_HEIGHT if any(
        len(_wrap(value, max(4, int(width / 3.4)))) > 1
        for value, width in zip(values, COLUMN_WIDTHS)
        if value
    ) else ROW_HEIGHT


def build_preview_pdf(batch):
    invoices = batch.invoices.order_by("id").only(
        "invoice_date", "voucher_date", "customer_gstin", "invoice_no",
        "taxable_value", "tax_percent", "cgst", "sgst", "igst", "cess",
        "invoice_value", "state_code", "reverse_charge", "invoice_type",
        "processing_state",
    )
    total = invoices.count()
    rows = (_row_values(invoice) for invoice in invoices.iterator(chunk_size=1000))
    generated = timezone.localtime().strftime("%d-%m-%Y %H:%M")
    pages = []
    current = []
    page_number = 1
    top = PAGE_HEIGHT - 84
    for values in rows:
        row_height = _row_height(values)
        if current and (top - row_height - MARGIN_BOTTOM < 24):
            pages.append((page_number, current))
            page_number += 1
            current = []
            top = PAGE_HEIGHT - 52
        current.append(values)
        top -= row_height
    pages.append((page_number, current))
    total_pages = len(pages)

    streams = []
    for number, page_rows in pages:
        commands = []
        _draw_page_header(commands, batch, total, generated, number, total_pages)
        table_top = PAGE_HEIGHT - (84 if number == 1 else 52)
        _draw_table_header(commands, table_top)
        top = table_top - HEADER_HEIGHT
        for values in page_rows:
            top -= _draw_row(commands, values, top)
        streams.append("\n".join(commands).encode("latin-1", "replace"))

    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_refs = []
    for index in range(len(streams)):
        page_refs.append(f"{3 + index * 2} 0 R")
    objects.append(f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>".encode())
    for index, stream in enumerate(streams):
        page_id = 3 + index * 2
        content_id = page_id + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:g} {PAGE_HEIGHT:g}] "
            f"/Resources << /Font << /F1 {3 + len(streams) * 2} 0 R /F2 {4 + len(streams) * 2} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(obj if isinstance(obj, bytes) else obj.encode())
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)
