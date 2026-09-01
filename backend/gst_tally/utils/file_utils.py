import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from openpyxl.utils.datetime import from_excel

HEADER_RE = re.compile(r"[^a-z0-9]+")

def normalize_header(value):
    return HEADER_RE.sub("", str(value or "").strip().lower())

def first_value(row, aliases, default=None):
    normalized = {normalize_header(k): v for k, v in row.items()}
    for alias in aliases:
        value = normalized.get(normalize_header(alias))
        if value not in (None, ""):
            return value
    return default

def parse_decimal(value):
    if value in (None, "", "-"):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None

def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            converted = from_excel(float(value))
            if isinstance(converted, datetime): return converted.date()
            return converted if isinstance(converted, date) else None
        except (TypeError, ValueError, OverflowError):
            return None
    text = str(value).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None

def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
