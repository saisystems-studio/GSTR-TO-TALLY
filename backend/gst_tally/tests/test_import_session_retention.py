from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from gst_tally.models import (
    GSTCompanyImportSummary,
    GSTImportBatch,
    GSTInvoice,
    GSTTallyVoucherRegistry,
    TallyVoucherMapping,
)
from gst_tally.services.import_service import cleanup_expired_processing_rows, import_file
from gst_tally.services.import_summary import IMPORTED, NEEDS_ATTENTION, upsert_voucher_registry


COMPANY_GSTIN = "33AFHPM6103Q1Z8"
SUPPLIER_GSTIN = "33AAACB2894G1ZJ"
HEADER = (
    b"GSTIN of supplier,GSTIN of recipient,Invoice number,Invoice Date,"
    b"Taxable Value,Invoice Value\n"
)


def upload(name, corrected=False):
    rows = []
    for number in range(1, 51):
        total = "118" if number <= 45 or corrected else "100"
        rows.append(
            f"{SUPPLIER_GSTIN},{COMPANY_GSTIN},INV-{number},01-08-2026,100,{total}\n".encode()
        )
    stream = BytesIO(HEADER + b"".join(rows))
    stream.name = name
    return stream


class ImportSessionRetentionTests(TestCase):
    def _mark_first_45_complete_and_last_5_failed(self, batch):
        for invoice in batch.invoices.order_by("id"):
            upsert_voucher_registry(
                batch,
                invoice,
                import_status=IMPORTED if int(invoice.invoice_no.split("-")[1]) <= 45 else NEEDS_ATTENTION,
                tally_created=int(invoice.invoice_no.split("-")[1]) <= 45,
                imported_at=timezone.now() if int(invoice.invoice_no.split("-")[1]) <= 45 else None,
            )

    def test_correction_reupload_reuses_active_session_and_only_corrected_rows_are_actionable(self):
        first = import_file(upload("may.csv"), "GSTR2A", "")
        self._mark_first_45_complete_and_last_5_failed(first)

        retry = import_file(upload("may-corrected.csv", corrected=True), "GSTR2A", "")

        self.assertEqual(retry.pk, first.pk)
        self.assertEqual(retry.total_rows, 50)
        self.assertEqual(retry.invoices.count(), 50)
        self.assertEqual(
            list(retry.invoices.exclude(invoice_no__in=[f"INV-{number}" for number in range(46, 51)])),
            list(first.invoices.exclude(invoice_no__in=[f"INV-{number}" for number in range(46, 51)])),
        )
        self.assertEqual(retry.invoices.filter(invoice_value="118").count(), 50)

    @patch("gst_tally.tally.service.import_batch")
    def test_expiry_cleanup_keeps_only_summary_and_makes_no_tally_call(self, tally_import):
        batch = import_file(upload("may.csv", corrected=True), "GSTR2A", "")
        invoice = batch.invoices.first()
        upsert_voucher_registry(batch, invoice, import_status=IMPORTED, tally_created=True, imported_at=timezone.now())
        TallyVoucherMapping.objects.create(
            batch=batch,
            invoice=invoice,
            idempotency_key="session-retention-test",
            source_invoice_number=invoice.invoice_no,
            party_gstin=invoice.customer_gstin,
            tally_company="Test Company",
            import_status="Imported",
        )
        summary_id = batch.company_import_summary_id
        GSTImportBatch.objects.filter(pk=batch.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

        cleanup_expired_processing_rows()

        self.assertFalse(GSTImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertFalse(GSTInvoice.objects.filter(import_batch_id=batch.pk).exists())
        self.assertFalse(TallyVoucherMapping.objects.filter(idempotency_key="session-retention-test").exists())
        self.assertFalse(GSTTallyVoucherRegistry.objects.filter(latest_invoice_id=invoice.pk).exists())
        self.assertTrue(GSTCompanyImportSummary.objects.filter(pk=summary_id).exists())
        tally_import.assert_not_called()

    def test_identical_file_after_expiry_creates_fresh_actionable_session(self):
        first = import_file(upload("may.csv", corrected=True), "GSTR2A", "")
        first_id = first.pk
        GSTImportBatch.objects.filter(pk=first_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        cleanup_expired_processing_rows()

        second = import_file(upload("may.csv", corrected=True), "GSTR2A", "")

        self.assertNotEqual(second.pk, first_id)
        self.assertEqual(second.invoices.count(), 50)
        self.assertEqual(second.duplicate_rows, 0)
