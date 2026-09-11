"""Thin GSTR-1/JSON entry point -- actual parsing/normalization lives in
canonical_invoice.py (the shared Excel/CSV/JSON -> canonical schema layer).
Kept as its own module so existing direct callers/tests
(``from .gstr1_parser import parse``) keep working unchanged."""
from .canonical_invoice import parse_json


def parse(file_obj, return_type="GSTR1"):
    return parse_json(file_obj, return_type)
