import csv
import io
import logging
import re

from gst_tally.utils.uploaded_files import decode_csv_upload

logger = logging.getLogger(__name__)
DELIMITERS = (",", ";", "\t", "|")


def _decode(raw):
    return decode_csv_upload(raw)


def _detected_delimiter(text):
    sample = text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        counts = {delimiter: sample.count(delimiter) for delimiter in DELIMITERS}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def _read(text, delimiter):
    return list(csv.reader(
        io.StringIO(text, newline=""),
        delimiter=delimiter,
        quotechar='"',
        doublequote=True,
        skipinitialspace=False,
        strict=False,
    ))


def _column_name(index):
    name = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _header_row_count(rows):
    for index, row in enumerate(rows):
        values = [cell.strip() for cell in row if cell.strip()]
        if not values:
            continue
        numeric = sum(bool(re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?%?", value)) for value in values)
        identifiers = sum(bool(re.search(r"\d", value)) and len(value) >= 10 for value in values)
        if index > 0 and (numeric >= max(2, len(values) // 3) or identifiers >= 2):
            return index
    return 1 if rows else 0


def parse_csv_preview(file_obj):
    text = decode_csv_upload(file_obj)
    detected = _detected_delimiter(text)
    candidates = [detected, *[item for item in DELIMITERS if item != detected]]
    technical_errors = []
    rows = None
    for delimiter in candidates:
        try:
            candidate = _read(text, delimiter)
            # A delimiter producing multiple cells is preferred. A genuine
            # single-column source remains valid after all candidates are tried.
            if rows is None or max((len(row) for row in candidate), default=0) > max((len(row) for row in rows), default=0):
                rows = candidate
            if any(len(row) > 1 for row in candidate):
                rows = candidate
                break
        except (csv.Error, UnicodeError) as exc:
            technical_errors.append(f"{repr(delimiter)}: {exc}")
    if rows is None:
        logger.warning("CSV preview parsing failed: %s", "; ".join(technical_errors))
        raise ValueError("Unable to read this CSV file. Please check whether the file is damaged or unsupported.")
    width = max((len(row) for row in rows), default=0)
    padded_rows = [list(row) + [""] * (width - len(row)) for row in rows]
    detected_header = next((index for index, row in enumerate(padded_rows)
                            if any(cell.strip().casefold() == "gstin of supplier" for cell in row)), None)
    data_start = detected_header + 1 if detected_header is not None else _header_row_count(padded_rows)
    candidates = padded_rows[:data_start]
    header_index = detected_header if detected_header is not None else (max(range(len(candidates)), key=lambda index: sum(bool(cell.strip()) for cell in candidates[index])) if candidates else None)
    columns = candidates[header_index] if header_index is not None else (padded_rows[0] if padded_rows else [])
    title_rows = [row for index, row in enumerate(candidates) if index != header_index and sum(bool(cell.strip()) for cell in row) == 1]
    titles = [next(cell for cell in row if cell.strip()) for row in title_rows]
    data_rows = padded_rows[data_start:]
    return {"sheet_name": None, "sheets": [], "columns": columns, "rows": data_rows,
            "column_count": width, "header_row_count": 0,
            "title": titles[0] if titles else "", "section_title": titles[1] if len(titles) > 1 else ""}
