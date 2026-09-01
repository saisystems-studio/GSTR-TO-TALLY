from decimal import Decimal
from io import BytesIO

from django.test import SimpleTestCase, TestCase

from gst_tally.models import GSTImportBatch, GSTInvoice, TallyVoucherMapping
from gst_tally.services.import_identity import DuplicateImportError, find_confirmed_duplicate_batch, normalized_source_fingerprint
from gst_tally.services.import_service import import_file

CSV_HEADER = b"GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Invoice Value\n"
CSV_ROW = b"33AAACB2894G1ZJ,INV-1,01-08-2026,100,118\n"
COMPANY_GSTIN = "33AFHPM6103Q1Z8"


def csv_file(name, extra_row=b""):
    stream = BytesIO(CSV_HEADER + CSV_ROW + extra_row)
    stream.name = name
    return stream


class FingerprintTests(SimpleTestCase):
    def test_identical_rows_produce_the_same_fingerprint(self):
        rows = [{"invoice_no": "1", "taxable_value": Decimal("100.00")}]
        self.assertEqual(normalized_source_fingerprint(rows), normalized_source_fingerprint(rows))

    def test_changed_content_produces_a_different_fingerprint(self):
        a = [{"invoice_no": "1", "taxable_value": Decimal("100.00")}]
        b = [{"invoice_no": "1", "taxable_value": Decimal("101.00")}]
        self.assertNotEqual(normalized_source_fingerprint(a), normalized_source_fingerprint(b))

    def test_no_fingerprint_never_matches_as_a_duplicate(self):
        self.assertIsNone(find_confirmed_duplicate_batch("33AAA", "GSTR2A", "082026", ""))


class DuplicateUploadGateTests(TestCase):
    """AB.29-33: filename is never the identity, and only a confirmed prior
    import blocks a re-upload of the same content."""

    def _make_confirmed_batch(self, extra_row=b""):
        batch = import_file(csv_file("APR2025.csv", extra_row), "GSTR2A", "")
        invoice = batch.invoices.first()
        TallyVoucherMapping.objects.create(
            idempotency_key=f"key-{batch.id}", batch=batch, invoice=invoice,
            source_invoice_number=invoice.invoice_no, party_gstin=invoice.customer_gstin,
            tally_company="D", tally_voucher_identifier="1", import_status="Imported")
        return batch

    def test_second_upload_of_a_confirmed_batch_is_rejected(self):
        self._make_confirmed_batch()

        with self.assertRaises(DuplicateImportError):
            import_file(csv_file("APR2025.csv"), "GSTR2A", "")

    def test_renamed_identical_file_is_still_rejected(self):
        self._make_confirmed_batch()

        with self.assertRaises(DuplicateImportError):
            import_file(csv_file("APR2025-copy.csv"), "GSTR2A", "")

    def test_changed_content_with_the_same_filename_is_allowed(self):
        self._make_confirmed_batch()

        second = import_file(csv_file("APR2025.csv", b"33AAACB2894G1ZJ,INV-2,02-08-2026,200,236\n"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)

    def test_an_upload_that_was_never_confirmed_imported_does_not_block_a_retry(self):
        # First upload exists but was never actually imported into Tally.
        import_file(csv_file("APR2025.csv"), "GSTR2A", "")

        second = import_file(csv_file("APR2025-again.csv"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)

    def test_duplicate_scope_requires_matching_company_gstin_and_return_type(self):
        self._make_confirmed_batch()

        # Different return type -- the same content is a distinct scope and is allowed.
        different_scope = import_file(csv_file("APR2025.csv"), "GSTR2B", "")

        self.assertIsInstance(different_scope, GSTImportBatch)
