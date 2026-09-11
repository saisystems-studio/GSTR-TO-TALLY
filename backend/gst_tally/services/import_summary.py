import hashlib
import json
import re

from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone

from gst_tally.models import GSTCompanyImportSummary, GSTInvoice, GSTTallyVoucherRegistry
from gst_tally.services.party_lookup import normalize_gstin
IMPORTED = "IMPORTED"
FAILED = "FAILED"
NEEDS_ATTENTION = "NEEDS_ATTENTION"
PENDING = "PENDING"
TALLY_SUCCESS_DB_UPDATE_PENDING = "TALLY_SUCCESS_DB_UPDATE_PENDING"

RETRYABLE_STATUSES = {FAILED, NEEDS_ATTENTION, PENDING}
FINAL_SUCCESS_STATUSES = {IMPORTED}


def transaction_type_for_return(return_type):
    return "Sales" if str(return_type or "").upper() in {"GSTR1", "GSTR-1"} else "Purchase"


def canonical_return_type(return_type):
    return str(return_type or "").strip().upper().replace("-", "")


def _invoice_date_text(invoice_date):
    return invoice_date.isoformat() if hasattr(invoice_date, "isoformat") else str(invoice_date or "").strip()


def company_scope_id_for(*, company_gstin, return_type, selected_tally_company="",
                         stable_tally_company_id=""):
    """Build the selected-Tally-company scope, never using GSTIN alone."""
    stable_id = str(stable_tally_company_id or "").strip()
    company_name = re.sub(r"\s+", " ", str(selected_tally_company or "").strip()).upper()
    identity = stable_id or company_name
    payload = {
        "company_gstin": normalize_gstin(company_gstin),
        "return_type": canonical_return_type(return_type),
        "tally_company_identity": identity,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def voucher_identity_hash(*, company_gstin, return_type, party_gstin, invoice_number, invoice_date,
                          company_scope_id=""):
    payload = {
        "company_gstin": normalize_gstin(company_gstin),
        "return_type": canonical_return_type(return_type),
        "party_gstin": normalize_gstin(party_gstin),
        "invoice_number": str(invoice_number or "").strip().upper(),
        "invoice_date": _invoice_date_text(invoice_date),
        "company_scope_id": str(company_scope_id or "").strip(),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def source_identity_hash(voucher_hashes):
    serialized = json.dumps(sorted(str(value) for value in voucher_hashes), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def row_voucher_identity(row, *, company_gstin, return_type, party_field, company_scope_id=""):
    return voucher_identity_hash(
        company_gstin=company_gstin,
        return_type=return_type,
        party_gstin=row.get(party_field, "") if party_field else "",
        invoice_number=row.get("invoice_no", ""),
        invoice_date=row.get("invoice_date"),
        company_scope_id=company_scope_id,
    )


@transaction.atomic
def get_or_create_company_import_summary(*, company_gstin, company_name="", return_type, source_file_name,
                                         source_format, total_source_count, voucher_hashes,
                                         company_scope_id=None):
    scope_hash = source_identity_hash(voucher_hashes)
    company_scope_id = company_scope_id or company_scope_id_for(
        company_gstin=company_gstin,
        return_type=return_type,
        selected_tally_company=(company_name or ""),
    )
    now = timezone.now()
    summary, created = GSTCompanyImportSummary.objects.select_for_update().get_or_create(
        company_gstin=normalize_gstin(company_gstin),
        company_scope_id=company_scope_id,
        return_type=canonical_return_type(return_type),
        import_scope_hash=scope_hash,
        defaults={
            "company_name": company_name or "",
            "transaction_type": transaction_type_for_return(return_type),
            "source_file_name": source_file_name or "",
            "source_format": source_format or "",
            "total_source_count": total_source_count,
            "pending_record_count": total_source_count,
            "first_uploaded_at": now,
            "last_uploaded_at": now,
            "import_status": "PENDING",
        },
    )
    update_fields = ["last_uploaded_at", "updated_at"]
    summary.last_uploaded_at = now
    if not created:
        summary.last_retry_at = now
        update_fields.append("last_retry_at")
    if company_name and summary.company_name != company_name:
        summary.company_name = company_name
        update_fields.append("company_name")
    if company_scope_id and summary.company_scope_id != company_scope_id:
        summary.company_scope_id = company_scope_id
        update_fields.append("company_scope_id")
    if source_file_name:
        summary.source_file_name = source_file_name
        update_fields.append("source_file_name")
    if source_format:
        summary.source_format = source_format
        update_fields.append("source_format")
    if created or not summary.total_source_count:
        summary.total_source_count = total_source_count
        if "total_source_count" not in update_fields:
            update_fields.append("total_source_count")
    summary.save(update_fields=update_fields)
    return summary


def _status_from_counts(summary):
    unsuccessful = summary.failed_record_count + summary.needs_attention_count + summary.pending_record_count
    if summary.total_source_count and summary.successful_voucher_count >= summary.total_source_count:
        return "COMPLETED"
    if summary.successful_voucher_count and unsuccessful:
        return "PARTIALLY_IMPORTED"
    if summary.needs_attention_count and not summary.successful_voucher_count:
        return "NEEDS_ATTENTION"
    if summary.failed_record_count and not (summary.successful_voucher_count or summary.needs_attention_count or summary.pending_record_count):
        return "FAILED"
    return "PENDING"


@transaction.atomic
def update_company_import_summary(summary):
    locked = GSTCompanyImportSummary.objects.select_for_update().get(pk=summary.pk)
    rows = GSTTallyVoucherRegistry.objects.filter(import_summary=locked)
    locked.successful_voucher_count = rows.filter(import_status=IMPORTED).count()
    locked.failed_record_count = rows.filter(import_status=FAILED).count()
    locked.needs_attention_count = rows.filter(import_status=NEEDS_ATTENTION).count()
    locked.pending_record_count = rows.filter(import_status__in=[PENDING, TALLY_SUCCESS_DB_UPDATE_PENDING]).count()
    total_state_count = (
        locked.successful_voucher_count
        + locked.failed_record_count
        + locked.needs_attention_count
        + locked.pending_record_count
    )
    if total_state_count > locked.total_source_count:
        locked.total_source_count = total_state_count
    tally_times = rows.filter(import_status=IMPORTED, imported_at__isnull=False).aggregate(
        first_imported_at=Min("imported_at"),
        last_imported_at=Max("imported_at"),
    )
    if tally_times["first_imported_at"] and not locked.first_tally_import_at:
        locked.first_tally_import_at = tally_times["first_imported_at"]
    if tally_times["last_imported_at"]:
        locked.last_tally_import_at = tally_times["last_imported_at"]
    locked.import_status = _status_from_counts(locked)
    locked.save(update_fields=[
        "successful_voucher_count", "failed_record_count", "needs_attention_count",
        "pending_record_count", "total_source_count", "first_tally_import_at",
        "last_tally_import_at", "import_status", "updated_at",
    ])
    return locked


@transaction.atomic
def increment_summary_skipped(summary, skipped_count):
    if not summary or not skipped_count:
        return summary
    locked = GSTCompanyImportSummary.objects.select_for_update().get(pk=summary.pk)
    locked.skipped_duplicate_count += skipped_count
    locked.last_retry_at = timezone.now()
    locked.save(update_fields=["skipped_duplicate_count", "last_retry_at", "updated_at"])
    return locked


def invoice_identity(invoice, *, company_scope_id=""):
    return {
        "company_gstin": invoice.import_batch.company_gstin,
        "company_name": (invoice.import_batch.company_details or {}).get("company_name", ""),
        "return_type": invoice.import_batch.gst_return_type,
        "transaction_type": transaction_type_for_return(invoice.import_batch.gst_return_type),
        "party_gstin": normalize_gstin(invoice.customer_gstin or invoice.supplier_gstin),
        "invoice_number": invoice.invoice_no,
        "invoice_date": invoice.invoice_date,
        "voucher_identity_hash": voucher_identity_hash(
            company_gstin=invoice.import_batch.company_gstin,
            return_type=invoice.import_batch.gst_return_type,
            party_gstin=invoice.customer_gstin or invoice.supplier_gstin,
            invoice_number=invoice.invoice_no,
            invoice_date=invoice.invoice_date,
            company_scope_id=company_scope_id,
        ),
    }


@transaction.atomic
def upsert_voucher_registry(batch, invoice, *, import_status=PENDING, tally_created=False, tally_altered=False,
                            tally_voucher_identifier="", imported_at=None, last_error_code="", last_error_message="",
                            company_scope_id=None):
    now = timezone.now()
    company_scope_id = company_scope_id or company_scope_id_for(
        company_gstin=batch.company_gstin,
        return_type=batch.gst_return_type,
        selected_tally_company=str((batch.company_details or {}).get("selected_tally_company") or (batch.company_details or {}).get("company_name") or ""),
        stable_tally_company_id=(batch.company_details or {}).get("stable_tally_company_id", ""),
    )
    identity = invoice_identity(invoice, company_scope_id=company_scope_id)
    registry, _ = GSTTallyVoucherRegistry.objects.select_for_update().update_or_create(
        company_gstin=identity["company_gstin"],
        company_scope_id=company_scope_id,
        return_type=identity["return_type"],
        voucher_identity_hash=identity["voucher_identity_hash"],
        defaults={
            **identity,
            "company_scope_id": company_scope_id,
            "company_name": identity["company_name"],
            "import_summary": batch.company_import_summary,
            "latest_invoice": invoice,
            "import_status": import_status,
            "tally_created": tally_created,
            "tally_altered": tally_altered,
            "tally_voucher_identifier": tally_voucher_identifier or "",
            "last_seen_at": now,
            "last_retry_at": now if import_status in RETRYABLE_STATUSES else None,
            "imported_at": imported_at if import_status == IMPORTED else None,
            "last_error_code": last_error_code or "",
            "last_error_message": last_error_message or "",
        },
    )
    # NOTE: update_company_import_summary is intentionally NOT called here.
    # It runs 4 COUNT queries + a select_for_update aggregate per invocation.
    # Calling it per-voucher caused N*4 DB round-trips for every batch.
    # import_batch() in service.py calls it exactly once at batch end (after
    # the voucher loop) -- that single call is all that's needed for
    # correctness, since the summary only needs to be accurate at completion.
    return registry


def registry_rows_for_hashes(*, company_gstin, return_type, voucher_hashes, company_scope_id=None):
    queryset = GSTTallyVoucherRegistry.objects.filter(
        company_gstin=normalize_gstin(company_gstin),
        return_type=canonical_return_type(return_type),
        voucher_identity_hash__in=set(voucher_hashes),
    )
    if company_scope_id:
        queryset = queryset.filter(company_scope_id=company_scope_id)
    return {row.voucher_identity_hash: row for row in queryset}


def prepare_invoice_for_retry(registry):
    """Called by _dedupe_rows (import_service.py) for a re-uploaded row whose
    voucher identity already has a RETRYABLE registry entry (FAILED /
    NEEDS_ATTENTION / PENDING -- never a final-success one, see
    FINAL_SUCCESS_STATUSES, which is filtered out by the caller before this
    is ever reached).

    RETRY MUST NEVER DELETE. The previous attempt's GSTInvoice (and any
    TallyVoucherMapping row a real Tally write attempt already created for
    it) is permanent import history and is left exactly as it is --
    TallyVoucherMapping.invoice is deliberately PROTECT (see models.py) and
    that constraint is correct, not a bug to route around. This used to call
    GSTInvoice.objects.filter(pk=...).delete() here, which crashed with
    ProtectedError the moment a prior attempt had gotten far enough to write
    a TallyVoucherMapping row (a real Tally rejection, or a connection
    failure while sending the write -- see service.py's per-voucher loop).

    Nothing needs to be deleted or updated in place for a safe retry: the
    caller (_dedupe_rows) lets the re-uploaded row proceed exactly like a
    brand-new row, so import_file() creates a fresh GSTInvoice for it under
    the new batch. That new invoice's own Step 6 run reconciles safely with
    Tally purely through the voucher's idempotency_key (company/voucher
    type/invoice number/date/party GSTIN -- see service.py::_key, which never
    depends on the invoice's database row):
      - if a real Tally write was already accepted for this exact voucher
        (even one only recorded as "Unknown"/pending verification), the
        existing TallyVoucherMapping row for that idempotency_key is what
        the mandatory pre-write safety gate in service.py matches against --
        so it is never blindly resent, no matter which GSTInvoice row is
        driving this run;
      - once a fresh write for this idempotency_key does succeed, that same
        TallyVoucherMapping row is updated in place (its invoice FK moves
        forward to the new GSTInvoice) rather than a duplicate being
        created, since idempotency_key stays unique.
    The old GSTInvoice/TallyVoucherMapping rows from the failed attempt are
    simply left behind afterwards as historical audit trail -- exactly what
    the PROTECT relationship exists to preserve.

    This function is intentionally a no-op today; it exists as the single,
    documented seam for retry-preparation policy (e.g. an attempt counter)
    instead of leaving that decision inlined/undocumented in the caller.
    """
    return None


@transaction.atomic
def ensure_summary_for_batch(batch):
    if batch.company_import_summary_id or not normalize_gstin(batch.company_gstin):
        return batch.company_import_summary
    invoices = list(batch.invoices.order_by("id"))
    if not invoices:
        return None
    company_scope_id = getattr(batch, "company_scope_id", "") or company_scope_id_for(
        company_gstin=batch.company_gstin,
        return_type=batch.gst_return_type,
        selected_tally_company=str((batch.company_details or {}).get("selected_tally_company") or (batch.company_details or {}).get("company_name") or ""),
        stable_tally_company_id=(batch.company_details or {}).get("stable_tally_company_id", ""),
    )
    voucher_hashes = [
        invoice_identity(invoice, company_scope_id=company_scope_id)["voucher_identity_hash"]
        for invoice in invoices
    ]
    summary = get_or_create_company_import_summary(
        company_gstin=batch.company_gstin,
        company_name=(batch.company_details or {}).get("company_name", ""),
        return_type=batch.gst_return_type,
        source_file_name=batch.file_name,
        source_format=batch.file_type,
        total_source_count=batch.total_rows or len(invoices),
        voucher_hashes=voucher_hashes,
        company_scope_id=company_scope_id,
    )
    GSTImportBatch = batch.__class__
    GSTImportBatch.objects.filter(pk=batch.pk).update(company_import_summary=summary)
    batch.company_import_summary = summary
    for invoice in invoices:
        upsert_voucher_registry(batch, invoice, import_status=PENDING)
    return update_company_import_summary(summary)
