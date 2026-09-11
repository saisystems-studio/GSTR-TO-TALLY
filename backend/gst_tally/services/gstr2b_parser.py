"""Thin GSTR-2B/Excel entry point -- actual parsing/normalization lives in
canonical_invoice.py (the shared Excel/CSV/JSON -> canonical schema layer).
Kept as its own module so existing direct callers/tests
(``from .gstr2b_parser import parse``) keep working unchanged.
``_load_source_workbook`` is re-exported because source_preview.py's legacy
.xls preview path imports it from here."""
from .canonical_invoice import _load_source_workbook, parse_excel

__all__ = ["parse", "_load_source_workbook"]


def parse(file_obj, return_type="GSTR2B"):
    return parse_excel(file_obj, return_type)
