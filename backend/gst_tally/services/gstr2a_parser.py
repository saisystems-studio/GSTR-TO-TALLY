"""Thin GSTR-2A/CSV entry point -- actual parsing/normalization lives in
canonical_invoice.py (the shared Excel/CSV/JSON -> canonical schema layer).
Kept as its own module so existing direct callers/tests
(``from .gstr2a_parser import parse``) keep working unchanged."""
from .canonical_invoice import parse_csv


def parse(file_obj, return_type="GSTR2A"):
    return parse_csv(file_obj, return_type)
