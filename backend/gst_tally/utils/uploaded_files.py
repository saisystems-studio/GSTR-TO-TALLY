import json
from pathlib import Path


CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
EXCEL_TYPES = {"xlsx": "EXCEL", "xls": "EXCEL"}
FILE_TYPES = {"json": "JSON", "csv": "CSV", **EXCEL_TYPES}
ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def upload_extension(file_obj):
    return Path(getattr(file_obj, "name", "")).suffix.lower().lstrip(".")


def _read_bytes(file_obj):
    if isinstance(file_obj, bytes):
        return file_obj
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    raw = file_obj.read()
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    return raw if isinstance(raw, bytes) else str(raw or "").encode("utf-8")


def _starts_with_binary_excel(raw):
    return raw.startswith(ZIP_MAGIC) or raw.startswith(OLE_MAGIC)


def validate_upload_type(file_obj, allowed=None):
    extension = upload_extension(file_obj)
    allowed = set(allowed or FILE_TYPES)
    raw = _read_bytes(file_obj)
    if extension not in allowed:
        if raw.startswith(ZIP_MAGIC) and "xlsx" in allowed:
            return "xlsx"
        if raw.startswith(OLE_MAGIC) and "xls" in allowed:
            return "xls"
        raise ValueError("Unsupported input format")
    if extension in {"csv", "json"} and _starts_with_binary_excel(raw):
        raise ValueError("Uploaded file content does not match the selected input format")
    if extension == "xlsx" and raw and not raw.startswith(ZIP_MAGIC):
        raise ValueError("Uploaded file content does not match the selected input format")
    if extension == "xls" and raw and not raw.startswith(OLE_MAGIC):
        raise ValueError("Uploaded file content does not match the selected input format")
    return extension


def file_type_label(extension):
    return FILE_TYPES[extension]


def decode_csv_upload(file_obj):
    raw = _read_bytes(file_obj)
    if _starts_with_binary_excel(raw):
        raise ValueError("Uploaded file content does not match the selected input format")
    for encoding in CSV_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to read this CSV file. Please check whether the file is damaged or unsupported.")


def load_json_upload(file_obj, **kwargs):
    raw = _read_bytes(file_obj)
    if _starts_with_binary_excel(raw):
        raise ValueError("Uploaded file content does not match the selected input format")
    try:
        text = raw.decode("utf-8-sig")
        return json.loads(text, **kwargs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON file: {exc}") from exc
