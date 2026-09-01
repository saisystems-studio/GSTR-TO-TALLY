import json
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from gst_tally.services.gstr2a_parser import parse as parse_gstr2a
from gst_tally.services.import_service import import_file
from gst_tally.services.source_preview import preview_file


CORRUPTED_MARKERS = ("鑄", "閾", "浸", "\ufffd", "□")
SUPPLIER_GSTIN = "33ABFFA3666C1ZU"


def stream(name, content):
    file_obj = BytesIO(content)
    file_obj.name = name
    return file_obj


def assert_no_garbled(testcase, value):
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    for marker in CORRUPTED_MARKERS:
        testcase.assertNotIn(marker, rendered)


def csv_bytes(prefix=b""):
    return prefix + (
        "GSTIN of supplier,Trade/Legal name,Invoice number,Invoice Date,Taxable Value,Invoice Value\n"
        f"{SUPPLIER_GSTIN},தமிழ் Traders,1,01-08-2026,1000,1180\n"
    ).encode("utf-8")


def json_bytes(prefix=b""):
    payload = {
        "fp": "082026",
        "b2b": [{
            "ctin": SUPPLIER_GSTIN,
            "inv": [{
                "inum": "1",
                "idt": "01-08-2026",
                "val": 1180,
                "itms": [{"itm_det": {"txval": 1000, "rt": 18, "camt": 90, "samt": 90}}],
            }],
        }],
    }
    return prefix + json.dumps(payload, ensure_ascii=False).encode("utf-8")


def xlsx_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "GSTR-2B"
    sheet.append(["GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice Date", "Taxable Value", "Invoice Value"])
    sheet.append([SUPPLIER_GSTIN, "தமிழ் Traders", "1", "01-08-2026", 1000, 1180])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


class SourceFileDecodingTests(TestCase):
    def test_xlsx_preview_reads_workbook_cells_without_decoding_binary_as_text(self):
        preview = preview_file(stream("gstr2b.xlsx", xlsx_bytes()), "GSTR2B")

        self.assertEqual(preview["file_type"], "EXCEL")
        self.assertEqual(preview["rows"][0][0], SUPPLIER_GSTIN)
        self.assertIn("தமிழ் Traders", preview["rows"][0])
        assert_no_garbled(self, preview)

    def test_csv_preview_decodes_utf8_text(self):
        preview = preview_file(stream("gstr2a.csv", csv_bytes()), "GSTR2A")

        self.assertEqual(preview["file_type"], "CSV")
        self.assertEqual(preview["rows"][0][0], SUPPLIER_GSTIN)
        self.assertIn("தமிழ் Traders", preview["rows"][0])
        assert_no_garbled(self, preview)

    def test_csv_preview_decodes_utf8_bom_text(self):
        preview = preview_file(stream("gstr2a.csv", csv_bytes(b"\xef\xbb\xbf")), "GSTR2A")

        self.assertEqual(preview["file_type"], "CSV")
        self.assertEqual(preview["columns"][0], "GSTIN of supplier")
        self.assertIn("தமிழ் Traders", preview["rows"][0])
        assert_no_garbled(self, preview)

    def test_json_preview_decodes_utf8_bom_and_returns_parsed_rows(self):
        preview = preview_file(stream("gstr1.json", json_bytes(b"\xef\xbb\xbf")), "GSTR1")

        self.assertEqual(preview["file_type"], "JSON")
        self.assertIn(SUPPLIER_GSTIN, preview["rows"][0])
        self.assertIsInstance(preview["rows"], list)
        assert_no_garbled(self, preview)

    def test_csv_import_uses_safe_text_decoding_fallbacks(self):
        cp1252_source = (
            "GSTIN of supplier,Trade/Legal name,Invoice number,Invoice Date,Taxable Value,Invoice Value\n"
            f"{SUPPLIER_GSTIN},Cafe \u20ac,1,01-08-2026,1000,1180\n"
        ).encode("cp1252")

        rows, metadata = parse_gstr2a(stream("gstr2a.csv", cp1252_source))

        self.assertEqual(rows[0]["customer_gstin"], SUPPLIER_GSTIN)
        self.assertIn("Cafe \u20ac", json.dumps(metadata, ensure_ascii=False, default=str))
        assert_no_garbled(self, {"rows": rows, "metadata": metadata})

    def test_binary_excel_uploaded_with_csv_extension_is_rejected_not_decoded(self):
        with self.assertRaisesRegex(ValueError, "Unsupported input format|does not match"):
            preview_file(stream("renamed.csv", xlsx_bytes()), "GSTR2A")

    def test_import_response_is_json_utf8_and_contains_only_parsed_fields(self):
        user = get_user_model().objects.create_user(username="decoder", password="pw")
        client = APIClient()
        client.force_authenticate(user=user)
        upload = SimpleUploadedFile("gstr2a.csv", csv_bytes(b"\xef\xbb\xbf"), content_type="text/csv")

        response = client.post("/api/gst-tally/import/", {"return_type": "GSTR2A", "file": upload}, format="multipart")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(response.data["file_type"], "CSV")
        self.assertEqual(response.data["invoices"][0]["customer_gstin"], SUPPLIER_GSTIN)
        assert_no_garbled(self, response.data)
