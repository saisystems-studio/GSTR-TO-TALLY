"""Regression coverage for File Preview value preservation: preview_file()
must return the exact normalized source values (a real zero as "0.00", a
genuinely missing value as None/"" -- never a "-" placeholder, which is a
display-only concern for the frontend), using the permanent 13-column
canonical order shared by GSTR-1, GSTR-2A and GSTR-2B.
"""
import json
from io import BytesIO

from django.test import TestCase
from openpyxl import Workbook

from gst_tally.services.source_preview import PREVIEW_COLUMNS, preview_file


def stream(name, content):
    file_obj = BytesIO(content)
    file_obj.name = name
    return file_obj


class PreviewColumnMappingTests(TestCase):
    EXPECTED_COLUMNS = [
        ("invoice_date", "Invoice Date"),
        ("customer_gstin", "Customer GSTIN"),
        ("invoice_no", "Invoice No"),
        ("taxable_value", "Taxable Value"),
        ("tax_percent", "Tax %"),
        ("cgst", "CGST"),
        ("sgst", "SGST"),
        ("igst", "IGST"),
        ("cess", "Cess"),
        ("invoice_value", "Invoice Value"),
        ("state_code", "State code"),
        ("reverse_charge", "Reverse Charge"),
        ("invoice_type", "Invoice Type"),
    ]

    def test_gstr1_gstr2a_gstr2b_share_the_same_13_column_canonical_order(self):
        for return_type in ("GSTR1", "GSTR2A", "GSTR2B"):
            columns = [(key, label) for key, label, _ in PREVIEW_COLUMNS[return_type]]
            self.assertEqual(columns, self.EXPECTED_COLUMNS)
            self.assertEqual(len(columns), 13)

    def test_cess_immediately_follows_igst_for_every_return_type(self):
        for return_type in ("GSTR1", "GSTR2A", "GSTR2B"):
            keys = [key for key, _, _ in PREVIEW_COLUMNS[return_type]]
            self.assertEqual(keys.index("cess"), keys.index("igst") + 1)


class Gstr2bAcceptanceRowPreviewTests(TestCase):
    """Task spec section 16's exact acceptance row, through the actual
    preview endpoint (not just the normalizer)."""

    def _workbook(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "GSTR-2B"
        sheet.append(["GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice Date",
                      "Taxable Value", "Rate", "Central Tax", "State/UT Tax", "Integrated Tax", "Cess",
                      "Invoice Value", "Place of supply", "Supply Attract Reverse Charge"])
        sheet.append(["33FISPS8174Q1ZW", "K S TRADERS", "KST/IN25-26/997", "15-04-2025",
                      3887.46, 18, 349.87, 349.87, 0.00, 0.00, 4587.00, "Tamil Nadu", "No"])
        buf = BytesIO()
        workbook.save(buf)
        buf.seek(0)
        buf.name = "gstr2b_acceptance.xlsx"
        return buf

    def _row(self):
        preview = preview_file(self._workbook(), "GSTR2B")
        self.assertEqual(len(preview["rows"]), 1)
        return preview["rows"][0]

    def test_igst_zero_is_preserved_not_replaced_with_dash(self):
        row = self._row()
        self.assertEqual(row["igst"], "0.00")
        self.assertNotEqual(row["igst"], "-")

    def test_cess_zero_is_preserved_not_replaced_with_dash(self):
        row = self._row()
        self.assertEqual(row["cess"], "0.00")
        self.assertNotEqual(row["cess"], "-")

    def test_state_code_not_derivable_from_a_bare_state_name_is_empty_not_dash(self):
        row = self._row()
        # "Tamil Nadu" has no embedded 2-digit code to derive state_code from.
        self.assertIn(row["state_code"], ("", None))
        self.assertNotEqual(row["state_code"], "-")

    def test_all_other_fields_match_the_source_exactly(self):
        row = self._row()
        # customer_gstin is mirrored from supplier_gstin for a purchase
        # return (GSTR-2A/2B) -- see canonical_invoice.normalize_row.
        self.assertEqual(row["customer_gstin"], "33FISPS8174Q1ZW")
        self.assertEqual(row["invoice_no"], "KST/IN25-26/997")
        self.assertEqual(row["invoice_date"], "2025-04-15")
        self.assertEqual(row["taxable_value"], "3887.46")
        self.assertEqual(row["tax_percent"], "18.00")
        self.assertEqual(row["cgst"], "349.87")
        self.assertEqual(row["sgst"], "349.87")
        self.assertEqual(row["invoice_value"], "4587.00")
        self.assertEqual(row["reverse_charge"], "No")


class ZeroAndEmptyPreservationAcrossFormatsTests(TestCase):
    """Same zero/empty preservation contract for CSV and JSON, and for
    GSTR-1's customer-side columns."""

    def test_csv_preview_preserves_zero_igst_and_cess(self):
        text = ("GSTIN of supplier,Trade/Legal name,Invoice number,Invoice Date,Taxable Value,Rate,Integrated Tax,Cess,Invoice Value\n"
                "33FISPS8174Q1ZW,K S TRADERS,KST/IN25-26/997,15-04-2025,3887.46,18,0.00,0.00,4587.00\n")
        preview = preview_file(stream("gstr2a.csv", text.encode()), "GSTR2A")
        row = preview["rows"][0]
        self.assertEqual(row["igst"], "0.00")
        self.assertEqual(row["cess"], "0.00")

    def test_json_preview_preserves_zero_igst_and_missing_fields_as_empty(self):
        payload = {"b2b": [{"ctin": "29AAAAA0000A1Z5", "inv": [{
            "inum": "INV-1", "idt": "15-04-2025", "val": 1000, "itms": [{"itm_det": {"txval": 1000, "iamt": 0}}]}]}]}
        preview = preview_file(stream("gstr1.json", json.dumps(payload).encode()), "GSTR1")
        row = preview["rows"][0]
        self.assertEqual(row["igst"], "0.00")
        self.assertIn(row["cgst"], ("", None))
        self.assertNotEqual(row["cgst"], "-")

    def test_gstr1_customer_gstin_column_is_populated_from_recipient_gstin(self):
        # The preview grid has no supplier-side "GSTIN" column at all (see
        # source_preview.CANONICAL_COLUMNS) -- for GSTR-1 the only GSTIN
        # column shown is Customer GSTIN, the recipient.
        text = "Recipient GSTIN,Invoice No,Invoice Date,Taxable Value,Invoice Value\n29AAAAA0000A1Z5,INV-1,15-04-2025,1000,1180\n"
        preview = preview_file(stream("gstr1.csv", text.encode()), "GSTR1")
        row = preview["rows"][0]
        self.assertNotIn("supplier_gstin", row)
        self.assertEqual(row["customer_gstin"], "29AAAAA0000A1Z5")


class PreviewResponseShapeTests(TestCase):
    def test_response_carries_batch_ready_column_and_row_data_in_one_call(self):
        text = "GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Invoice Value\n33FISPS8174Q1ZW,INV-1,15-04-2025,1000,1180\n"
        preview = preview_file(stream("gstr2a.csv", text.encode()), "GSTR2A")
        self.assertEqual(preview["return_type"], "GSTR2A")
        self.assertIn("columns", preview)
        self.assertIn("rows", preview)
        self.assertEqual(preview["row_count"], len(preview["rows"]))
        self.assertTrue(all("key" in column and "label" in column for column in preview["columns"]))


class CessMappingTests(TestCase):
    """Cess mapping, verified separately from the column-order tests above:
    GSTR-2A/2B must recognize the rupee-symbol and combined header variants,
    GSTR-1 uses whatever cess/cess-amount field the source already carries,
    and a genuine zero/blank is preserved -- never invented, never copied
    from another tax column."""

    def _preview_row(self, return_type, cess_header, cess_value):
        text = (f"GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Integrated Tax,{cess_header},Invoice Value\n"
                f"33FISPS8174Q1ZW,INV-1,15-04-2025,1000,180,{cess_value},1180\n")
        preview = preview_file(stream("source.csv", text.encode()), return_type)
        return preview["rows"][0]

    def test_gstr2a_recognizes_cess_header_variants(self):
        for header in ("Cess", "Cess (₹)", "Cess(₹)"):
            row = self._preview_row("GSTR2A", header, "12.50")
            self.assertEqual(row["cess"], "12.50")

    def test_gstr2b_recognizes_cess_header_variants_including_tax_amount_cess(self):
        for header in ("Cess", "Cess (₹)", "Cess(₹)", "Tax Amount / Cess(₹)"):
            row = self._preview_row("GSTR2B", header, "12.50")
            self.assertEqual(row["cess"], "12.50")

    def test_gstr1_uses_existing_cess_field_from_json(self):
        payload = {"b2b": [{"ctin": "29AAAAA0000A1Z5", "inv": [{
            "inum": "INV-1", "idt": "15-04-2025", "val": 1180,
            "itms": [{"itm_det": {"txval": 1000, "iamt": 180, "csamt": 12.5}}]}]}]}
        preview = preview_file(stream("gstr1.json", json.dumps(payload).encode()), "GSTR1")
        self.assertEqual(preview["rows"][0]["cess"], "12.50")

    def test_cess_zero_is_preserved_not_invented(self):
        row = self._preview_row("GSTR2A", "Cess", "0")
        self.assertEqual(row["cess"], "0.00")

    def test_cess_blank_is_preserved_not_copied_from_another_tax_value(self):
        text = ("GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Integrated Tax,Invoice Value\n"
                "33FISPS8174Q1ZW,INV-1,15-04-2025,1000,180,1180\n")
        preview = preview_file(stream("source.csv", text.encode()), "GSTR2A")
        row = preview["rows"][0]
        self.assertIn(row["cess"], ("", None))
        self.assertNotEqual(row["cess"], row["igst"])
