from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from gst_tally.auth_service import secret_hash
from gst_tally.models import GSTImportBatch, GSTInvoice, ProductLicense, TallyVoucherMapping
from gst_tally.services.import_identity import (
    DuplicateImportError,
    file_content_fingerprint,
    find_duplicate_batch,
    normalized_source_fingerprint,
)
from gst_tally.services.import_service import import_file

User = get_user_model()

CSV_HEADER = b"GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Invoice Value\n"
CSV_ROW = b"33AAACB2894G1ZJ,INV-1,01-08-2026,100,118\n"
COMPANY_GSTIN = "33AFHPM6103Q1Z8"
COMPANY_B_GSTIN = "29XYZDE5678G1Z2"

# Same shape as CSV_HEADER, but with a company-identifying column
# (canonical_invoice.COMPANY_ALIASES) so the parsed batch actually resolves a
# company_gstin -- needed to exercise GSTIN-scoped dedup instead of the
# no-GSTIN-known fallback.
CSV_HEADER_WITH_COMPANY = (b"GSTIN of supplier,GSTIN of recipient,Invoice number,Invoice Date,"
                           b"Taxable Value,Invoice Value\n")


def csv_file(name, extra_row=b""):
    stream = BytesIO(CSV_HEADER + CSV_ROW + extra_row)
    stream.name = name
    return stream


def csv_file_for_company(name, company_gstin, rows):
    body = b"".join(
        f"33AAACB2894G1ZJ,{company_gstin},{invoice_no},{date},{taxable},{value}\n".encode()
        for invoice_no, date, taxable, value in rows
    )
    stream = BytesIO(CSV_HEADER_WITH_COMPANY + body)
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
        self.assertIsNone(find_duplicate_batch(file_hash=""))

    def test_file_content_fingerprint_uses_uploaded_file_bytes(self):
        file_obj = csv_file("APR2025.csv")

        digest, size = file_content_fingerprint(file_obj)

        self.assertEqual(digest, "f0adce1d98d61c00ad422946705b5f298416761b44bda6672b9bc14e01a59113")
        self.assertEqual(size, len(CSV_HEADER + CSV_ROW))
        self.assertEqual(file_obj.read(), CSV_HEADER + CSV_ROW)


class DuplicateUploadGateTests(TestCase):
    """AB.29-33: filename is never the identity, and any existing batch for
    the exact same uploaded bytes blocks a re-upload -- whether or not that
    earlier batch was ever pushed into Tally -- so the same rows are never
    inserted twice. A genuinely different file (different byte hash) for the
    same GSTIN is always allowed."""

    def _make_confirmed_batch(self, extra_row=b""):
        batch = import_file(csv_file("APR2025.csv", extra_row), "GSTR2A", "")
        invoice = batch.invoices.first()
        TallyVoucherMapping.objects.create(
            idempotency_key=f"key-{batch.id}", batch=batch, invoice=invoice,
            source_invoice_number=invoice.invoice_no, party_gstin=invoice.customer_gstin,
            tally_company="D", tally_voucher_identifier="1", import_status="Imported")
        return batch

    def test_second_upload_of_a_confirmed_batch_is_allowed_again(self):
        self._make_confirmed_batch()

        second = import_file(csv_file("APR2025.csv"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)
        self.assertEqual(second.imported_rows, 0)
        self.assertEqual(second.duplicate_rows, 1)

    def test_renamed_identical_file_is_allowed_again(self):
        self._make_confirmed_batch()

        second = import_file(csv_file("APR2025-copy.csv"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)

    def test_changed_content_with_the_same_filename_is_allowed(self):
        self._make_confirmed_batch()

        second = import_file(csv_file("APR2025.csv", b"33AAACB2894G1ZJ,INV-2,02-08-2026,200,236\n"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)

    def test_an_upload_that_was_never_confirmed_imported_still_allows_retry(self):
        import_file(csv_file("APR2025.csv"), "GSTR2A", "")

        second = import_file(csv_file("APR2025-again.csv"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)

    def test_different_content_for_the_same_gstin_is_always_allowed(self):
        # Same company GSTIN, genuinely different file content (extra row) --
        # never blocked, confirmed or not.
        import_file(csv_file("APR2025.csv"), "GSTR2A", "")

        second = import_file(
            csv_file("myname.csv", b"33AAACB2894G1ZJ,INV-2,02-08-2026,200,236\n"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)

    def test_deleting_the_batch_allows_the_same_file_again(self):
        first = import_file(csv_file("APR2025.csv"), "GSTR2A", "")
        first.delete()

        second = import_file(csv_file("APR2025.csv"), "GSTR2A", "")

        self.assertIsInstance(second, GSTImportBatch)

    def test_same_invoice_data_with_changed_file_bytes_is_skipped_as_duplicate_invoice(self):
        self._make_confirmed_batch()

        # Same parsed accounting data, different raw bytes -- the whole-file
        # byte-hash guard doesn't fire (bytes differ), but invoice-level
        # dedup (services/import_identity.py::invoice_fingerprint) still
        # recognizes this as the same invoice and skips it instead of
        # inserting it a second time.
        changed_stream = BytesIO(CSV_HEADER + b"33AAACB2894G1ZJ,INV-1,01-08-2026,100,118\r\n")
        changed_stream.name = "APR2025.csv"
        changed_bytes = import_file(changed_stream, "GSTR2A", "")

        self.assertIsInstance(changed_bytes, GSTImportBatch)
        self.assertEqual(changed_bytes.imported_rows, 0)
        self.assertEqual(changed_bytes.duplicate_rows, 1)
        self.assertEqual(GSTInvoice.objects.filter(invoice_no="INV-1").count(), 1)

    def test_multiple_files_same_gstin_all_new_invoices_are_all_imported(self):
        # request item #1 -- no fixed limit on how many files one GSTIN can
        # import, as long as each carries genuinely new invoice data.
        first = import_file(csv_file("file1.csv"), "GSTR2A", "")
        second = import_file(csv_file("file2.csv", b"33AAACB2894G1ZJ,INV-2,02-08-2026,200,236\n"), "GSTR2A", "")
        third = import_file(csv_file("file3.csv", b"33AAACB2894G1ZJ,INV-3,03-08-2026,300,354\n"), "GSTR2A", "")

        self.assertEqual(first.imported_rows, 1)
        self.assertEqual(second.imported_rows, 1)
        self.assertEqual(third.imported_rows, 1)
        self.assertEqual(GSTInvoice.objects.filter(invoice_no__in=["INV-1", "INV-2", "INV-3"]).count(), 3)

    def test_file_mixing_previously_imported_and_new_invoices_only_imports_the_new_ones(self):
        import_file(csv_file("file1.csv"), "GSTR2A", "")  # INV-1 already imported

        mixed = BytesIO(CSV_HEADER + CSV_ROW + b"33AAACB2894G1ZJ,INV-2,02-08-2026,200,236\n")
        mixed.name = "file2.csv"
        second = import_file(mixed, "GSTR2A", "")

        self.assertEqual(second.total_rows, 2)
        self.assertEqual(second.imported_rows, 1)
        self.assertEqual(second.duplicate_rows, 1)
        self.assertEqual(GSTInvoice.objects.filter(invoice_no="INV-1").count(), 1)
        self.assertEqual(GSTInvoice.objects.filter(invoice_no="INV-2").count(), 1)

    def test_different_company_gstin_same_invoice_number_both_accepted(self):
        # request items #3/#6 -- invoice identity is scoped by company GSTIN;
        # two different companies both having an "INV-100" is not a clash.
        company_a = csv_file_for_company("a.csv", COMPANY_GSTIN, [("INV-100", "01-08-2026", "100", "118")])
        company_b = csv_file_for_company("b.csv", COMPANY_B_GSTIN, [("INV-100", "01-08-2026", "100", "118")])

        batch_a = import_file(company_a, "GSTR2A", "")
        batch_b = import_file(company_b, "GSTR2A", "")

        self.assertEqual(batch_a.imported_rows, 1)
        self.assertEqual(batch_b.imported_rows, 1)
        self.assertEqual(GSTInvoice.objects.filter(invoice_no="INV-100").count(), 2)

    def test_deleting_the_batch_allows_the_same_invoice_data_again(self):
        # request item #9 -- deleting a batch (and its cascaded invoices)
        # must free the invoice fingerprint for re-import, not just the
        # whole-file byte hash.
        first = import_file(csv_file("APR2025.csv"), "GSTR2A", "")
        first.delete()

        changed_stream = BytesIO(CSV_HEADER + b"33AAACB2894G1ZJ,INV-1,01-08-2026,100,118\r\n")
        changed_stream.name = "APR2025-retry.csv"
        second = import_file(changed_stream, "GSTR2A", "")

        self.assertEqual(second.imported_rows, 1)
        self.assertEqual(second.duplicate_rows, 0)


class ProductLicenseBatchScopeTests(TestCase):
    """Traces the reported "Company B rejected because of Company A" bug to
    its root cause: services/import_service.py::_current_product_license
    used to fall back to "this user's most-recently-verified license, any
    GSTIN" whenever the upload's own company_gstin wasn't resolved yet (the
    normal case for GSTR-2A/2B at upload time, before Company Verification
    runs) -- silently attaching a *different* company's ProductLicense to
    the new batch. Fixed by returning no license at all when the GSTIN isn't
    known yet, rather than guessing."""

    def _make_license(self, user, *, gstin, serial, key):
        return ProductLicense.objects.create(
            customer=user, activation_key_hash=secret_hash(key), display_activation_key=key,
            licensed_gstin=gstin, licensed_tally_serial=serial, status=ProductLicense.ACTIVE,
            last_verified_at=timezone.now(),
        )

    def test_batch_with_unresolved_company_gstin_is_not_stamped_with_a_different_companys_license(self):
        user = User.objects.create_user(username="owner", email="owner@example.com", password="x")
        company_a_license = self._make_license(user, gstin=COMPANY_GSTIN, serial="111", key="KEY-A")
        company_a_license.last_verified_at = timezone.now()
        company_a_license.save(update_fields=["last_verified_at"])
        self._make_license(user, gstin=COMPANY_B_GSTIN, serial="222", key="KEY-B")

        # This upload's own company_gstin is unresolved (plain CSV_HEADER has
        # no company-identifying column) -- it must NOT be stamped with
        # Company A's license just because Company A was verified more
        # recently.
        batch = import_file(csv_file("company_b_file.csv"), "GSTR2A", "", user=user)

        self.assertIsNone(batch.product_license)

    def test_batch_with_resolved_company_gstin_gets_that_companys_own_license(self):
        user = User.objects.create_user(username="owner2", email="owner2@example.com", password="x")
        self._make_license(user, gstin=COMPANY_GSTIN, serial="111", key="KEY-C")
        company_b_license = self._make_license(user, gstin=COMPANY_B_GSTIN, serial="222", key="KEY-D")

        batch = import_file(
            csv_file_for_company("company_b_file.csv", COMPANY_B_GSTIN, [("INV-1", "01-08-2026", "100", "118")]),
            "GSTR2A", "", user=user,
        )

        self.assertEqual(batch.product_license_id, company_b_license.id)
