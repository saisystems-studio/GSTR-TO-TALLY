from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.utils import timezone

from gst_tally.models import GSTImportBatch, GSTInvoice, TallyVoucherMapping
from gst_tally.services.import_service import import_file
from gst_tally.services.import_summary import (
    IMPORTED,
    NEEDS_ATTENTION,
    voucher_identity_hash,
    update_company_import_summary,
    upsert_voucher_registry,
)


COMPANY_GSTIN = "33AFHPM6103Q1Z8"
OTHER_COMPANY_GSTIN = "29XYZDE5678G1Z2"
SUPPLIER_GSTIN = "33AAACB2894G1ZJ"
CSV_HEADER = (
    b"GSTIN of supplier,GSTIN of recipient,Invoice number,Invoice Date,"
    b"Taxable Value,Invoice Value\n"
)


def csv_upload(name, company_gstin, rows):
    body = b"".join(
        f"{SUPPLIER_GSTIN},{company_gstin},{invoice_no},{invoice_date},{taxable},{invoice_total}\n".encode()
        for invoice_no, invoice_date, taxable, invoice_total in rows
    )
    stream = BytesIO(CSV_HEADER + body)
    stream.name = name
    return stream


class ImportSummaryRegistryTests(TestCase):
    def test_same_gstin_separate_selected_tally_companies_are_independent(self):
        company_a = import_file(
            csv_upload("same.csv", COMPANY_GSTIN, [("INV-100", "01-08-2026", "100", "118")]),
            "GSTR2A", "", selected_tally_company="KUMARAN SUPER MARKET",
        )
        invoice_a = company_a.invoices.get()
        upsert_voucher_registry(company_a, invoice_a, import_status=IMPORTED, tally_created=True,
                                imported_at=timezone.now())

        same_company_retry = import_file(
            csv_upload("same.csv", COMPANY_GSTIN, [("INV-100", "01-08-2026", "100", "118")]),
            "GSTR2A", "", selected_tally_company="  KUMARAN   SUPER MARKET ",
        )
        company_b = import_file(
            csv_upload("same.csv", COMPANY_GSTIN, [("INV-100", "01-08-2026", "100", "118")]),
            "GSTR2A", "", selected_tally_company="KUMARAN SUPER MARKET - BRANCH",
        )

        self.assertEqual(same_company_retry.imported_rows, 0)
        self.assertEqual(same_company_retry.duplicate_rows, 1)
        self.assertEqual(company_b.imported_rows, 1)

    def test_import_file_creates_permanent_company_summary_from_business_record_count(self):
        batch = import_file(
            csv_upload(
                "april.csv",
                COMPANY_GSTIN,
                [("INV-1", "01-08-2026", "100", "118"), ("INV-2", "02-08-2026", "200", "236")],
            ),
            "GSTR2A",
            "",
        )

        summary = batch.company_import_summary
        self.assertEqual(summary.company_gstin, COMPANY_GSTIN)
        self.assertEqual(summary.return_type, "GSTR2A")
        self.assertEqual(summary.transaction_type, "Purchase")
        self.assertEqual(summary.total_source_count, 2)
        self.assertEqual(summary.pending_record_count, 2)
        self.assertEqual(summary.import_status, "PENDING")

    def test_corrected_reupload_skips_imported_rows_and_replaces_failed_working_data(self):
        first = import_file(
            csv_upload(
                "april.csv",
                COMPANY_GSTIN,
                [("INV-1", "01-08-2026", "100", "118"), ("INV-2", "02-08-2026", "200", "200")],
            ),
            "GSTR2A",
            "",
        )
        inv1 = first.invoices.get(invoice_no="INV-1")
        inv2 = first.invoices.get(invoice_no="INV-2")
        upsert_voucher_registry(first, inv1, import_status=IMPORTED, tally_created=True, imported_at=timezone.now())
        upsert_voucher_registry(first, inv2, import_status=NEEDS_ATTENTION, last_error_message="Total mismatch")
        update_company_import_summary(first.company_import_summary)

        retry = import_file(
            csv_upload(
                "april-corrected.csv",
                COMPANY_GSTIN,
                [("INV-1", "01-08-2026", "100", "118"), ("INV-2", "02-08-2026", "200", "236")],
            ),
            "GSTR2A",
            "",
        )

        self.assertEqual(retry.company_import_summary_id, first.company_import_summary_id)
        self.assertEqual(retry.total_rows, 2)
        self.assertEqual(retry.imported_rows, 1)
        self.assertEqual(retry.duplicate_rows, 1)
        corrected = retry.invoices.get(invoice_no="INV-2")
        self.assertEqual(corrected.invoice_value, Decimal("236.00"))
        summary = retry.company_import_summary
        summary.refresh_from_db()
        self.assertEqual(summary.total_source_count, 2)
        self.assertEqual(summary.successful_voucher_count, 1)
        self.assertEqual(summary.pending_record_count, 1)
        self.assertEqual(summary.skipped_duplicate_count, 1)

    def test_reupload_of_a_failed_voucher_with_a_tally_voucher_mapping_does_not_crash(self):
        """Regression: a prior attempt on a FAILED/NEEDS_ATTENTION invoice can
        already have written a TallyVoucherMapping row for it (e.g. a real
        Tally rejection, or a connection failure while sending the write --
        see service.py). That FK is PROTECT (models.py) -- correctly, since
        it is real import history -- so re-uploading the same invoice used
        to crash the request with a Django ProtectedError 500 while trying
        to hard-delete that history out from under the constraint.

        The fix is not to delete the invoice/mapping at all: retry means the
        re-uploaded row is revalidated as a fresh GSTInvoice, while the old
        one (and its TallyVoucherMapping) are preserved untouched as
        permanent audit history -- see prepare_invoice_for_retry."""
        first = import_file(
            csv_upload("april.csv", COMPANY_GSTIN, [("INV-1", "01-08-2026", "100", "118")]),
            "GSTR2A", "",
        )
        inv1 = first.invoices.get(invoice_no="INV-1")
        upsert_voucher_registry(first, inv1, import_status=NEEDS_ATTENTION, last_error_message="Tally rejected the voucher")
        TallyVoucherMapping.objects.create(
            batch=first, invoice=inv1, idempotency_key="test-protect-key",
            source_invoice_number="INV-1", party_gstin=SUPPLIER_GSTIN,
            tally_company="Test Co", import_status="Tally Failed",
            error_message="Tally rejected the voucher",
        )

        retry = import_file(
            csv_upload("april-retry.csv", COMPANY_GSTIN, [("INV-1", "01-08-2026", "100", "118")]),
            "GSTR2A", "",
        )

        self.assertEqual(retry.imported_rows, 1)
        retried_invoice = retry.invoices.get(invoice_no="INV-1")
        self.assertNotEqual(retried_invoice.pk, inv1.pk)
        # The failed attempt's history is never destroyed by a retry.
        self.assertTrue(GSTInvoice.objects.filter(pk=inv1.pk).exists())
        self.assertTrue(TallyVoucherMapping.objects.filter(idempotency_key="test-protect-key").exists())

    def test_already_imported_row_is_never_touched_or_resent_on_reupload(self):
        """Task spec's core business rule: a row whose voucher identity is
        already a final-success registry entry must be classified as
        ALREADY_IMPORTED on re-upload -- no new GSTInvoice created for it,
        and its existing GSTInvoice/TallyVoucherMapping history left exactly
        as-is (not even inspected for a possible delete)."""
        first = import_file(
            csv_upload("april.csv", COMPANY_GSTIN, [("INV-1", "01-08-2026", "100", "118")]),
            "GSTR2A", "",
        )
        inv1 = first.invoices.get(invoice_no="INV-1")
        upsert_voucher_registry(first, inv1, import_status=IMPORTED, tally_created=True, imported_at=timezone.now())
        TallyVoucherMapping.objects.create(
            batch=first, invoice=inv1, idempotency_key="test-already-imported-key",
            source_invoice_number="INV-1", party_gstin=SUPPLIER_GSTIN,
            tally_company="Test Co", import_status="Imported",
        )
        invoice_count_before = GSTInvoice.objects.filter(invoice_no="INV-1").count()

        retry = import_file(
            csv_upload("april-retry.csv", COMPANY_GSTIN, [("INV-1", "01-08-2026", "100", "118")]),
            "GSTR2A", "",
        )

        self.assertEqual(retry.imported_rows, 0)
        self.assertEqual(retry.duplicate_rows, 1)
        self.assertEqual(GSTInvoice.objects.filter(invoice_no="INV-1").count(), invoice_count_before)
        mapping = TallyVoucherMapping.objects.get(idempotency_key="test-already-imported-key")
        self.assertEqual(mapping.invoice_id, inv1.pk)
        self.assertEqual(mapping.import_status, "Imported")

    def test_voucher_identity_is_scoped_by_company_gstin(self):
        first = voucher_identity_hash(
            company_gstin=COMPANY_GSTIN,
            return_type="GSTR2B",
            party_gstin=SUPPLIER_GSTIN,
            invoice_number="INV-100",
            invoice_date="2026-08-01",
        )
        second = voucher_identity_hash(
            company_gstin=OTHER_COMPANY_GSTIN,
            return_type="GSTR2B",
            party_gstin=SUPPLIER_GSTIN,
            invoice_number="INV-100",
            invoice_date="2026-08-01",
        )

        self.assertNotEqual(first, second)

    def test_temporary_cleanup_keeps_permanent_summary_and_registry(self):
        batch = import_file(
            csv_upload("old.csv", COMPANY_GSTIN, [("INV-1", "01-08-2026", "100", "118")]),
            "GSTR2A",
            "",
        )
        invoice = batch.invoices.get()
        registry = upsert_voucher_registry(batch, invoice, import_status=IMPORTED, tally_created=True, imported_at=timezone.now())
        summary = batch.company_import_summary
        old_time = timezone.now() - timedelta(hours=25)
        GSTImportBatch.objects.filter(pk=batch.pk).update(created_at=old_time, uploaded_at=old_time)

        from django.core.management import call_command

        call_command("cleanup_gst_temporary_data", "--older-than-hours=24", verbosity=0)

        self.assertFalse(GSTImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertFalse(GSTInvoice.objects.filter(pk=invoice.pk).exists())
        self.assertTrue(type(summary).objects.filter(pk=summary.pk).exists())
        self.assertTrue(type(registry).objects.filter(pk=registry.pk).exists())

        retry = import_file(
            csv_upload("old.csv", COMPANY_GSTIN, [("INV-1", "01-08-2026", "100", "118")]),
            "GSTR2A", "",
        )
        self.assertEqual(retry.imported_rows, 0)
        self.assertEqual(retry.duplicate_rows, 1)
