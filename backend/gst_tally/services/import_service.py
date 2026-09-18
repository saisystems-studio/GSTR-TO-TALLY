import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from gst_tally.models import GSTImportBatch, GSTInvoice, ProductLicense
from . import canonical_invoice
from .import_identity import (file_content_fingerprint, invoice_fingerprint, normalized_source_fingerprint)
from .import_summary import (
    FINAL_SUCCESS_STATUSES,
    PENDING,
    company_scope_id_for,
    get_or_create_company_import_summary,
    increment_summary_skipped,
    prepare_invoice_for_retry,
    registry_rows_for_hashes,
    row_voucher_identity,
    upsert_voucher_registry,
)
from .party_lookup import normalize_gstin
from gst_tally.utils.uploaded_files import file_type_label, validate_upload_type

logger = logging.getLogger(__name__)

RETURN_TYPES = canonical_invoice.RETURN_TYPES


def cleanup_expired_processing_rows(now=None):
    """Finalize audit summaries and delete expired temporary sessions only.

    This function deliberately imports no Tally/local-agent modules and
    performs no network work.  Cascade relationships remove invoices,
    mappings, registry rows, jobs, source JSON, and correction state.
    """
    now = now or timezone.now()
    with transaction.atomic():
        stale = list(GSTImportBatch.objects.select_for_update().filter(expires_at__lte=now))
        for batch in stale:
            if batch.company_import_summary_id:
                from .import_summary import update_company_import_summary
                update_company_import_summary(batch.company_import_summary)
        deleted, _ = GSTImportBatch.objects.filter(pk__in=[batch.pk for batch in stale]).delete()
    return {"expired_sessions": len(stale), "deleted_rows": deleted}


def _current_product_license(user, company_gstin):
    """The ProductLicense to (informationally) attach to a new batch -- never
    used for actual import authorization (see services/product_license.py,
    which always re-resolves the license live against whatever company is
    *currently* open in Tally). company_gstin is frequently still blank here
    (resolved later, at Company Verification -- see services/company.py),
    and a blank GSTIN must never fall back to "this user's most recently
    verified license" -- that silently attaches a DIFFERENT company's
    license to this batch, which then wrongly scopes duplicate detection
    against that other company's history (see find_duplicate_batch)."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    company_gstin = normalize_gstin(company_gstin)
    if not company_gstin:
        return None
    qs = ProductLicense.objects.filter(customer=user, licensed_gstin=company_gstin).exclude(status=ProductLicense.REVOKED)
    return qs.order_by("-last_verified_at", "-created_at").first()


def _dedupe_rows(rows, *, company_gstin, return_type, user, registry_by_hash=None, company_scope_id=None,
                 active_batch=None):
    """Split parsed rows into (new_rows, duplicate_count, already_imported_count,
    retry_candidate_count). duplicate_count includes already_imported_count
    (every already-imported row is also a duplicate of a prior successful
    import); already_imported_count/retry_candidate_count exist only to
    make that split visible for logging/reporting, not to change what gets
    counted where existing callers already read duplicate_count.

    Every row is classified exactly once, by the registered voucher identity
    (registry_by_hash) first and the raw content fingerprint only as a
    fallback for rows with no registry entry at all:

      A. ALREADY_IMPORTED -- a registry entry exists and its status is a
         final success (see FINAL_SUCCESS_STATUSES). Never re-validated,
         never sent to Tally again, and never touched in the database --
         see task spec "never resend, never delete".
      B. RETRY_CANDIDATE -- a registry entry exists but is not yet a final
         success (FAILED/NEEDS_ATTENTION/PENDING/still-unconfirmed). The
         previous attempt's GSTInvoice/TallyVoucherMapping rows are left
         exactly as they are (see prepare_invoice_for_retry's docstring for
         why that is always safe); this row proceeds as a fresh row so it
         gets fully revalidated against the newly uploaded data.
      C. NEW -- no registry entry, and no existing invoice shares this exact
         content fingerprint for this company-scoped context + return type.
      D. DUPLICATE -- no registry entry, but an identical invoice (by
         content fingerprint) already exists from an earlier upload that
         never even reached a tracked voucher identity (e.g. company GSTIN
         was not yet resolvable at that time -- see get_or_create_company_import_summary's
         `if company_gstin` guard above). Skipped the same as A, just without
         a registry match to attribute it to.

    Rows within this file are child invoice lines, not duplicate invoices,
    even when their amounts are identical. A selected Tally company scope
    keeps Company 1 and Company 2 independent even when both share the same
    GSTIN.
    """
    party_field = canonical_invoice.PARTY_FIELDS.get(return_type, (None,))[0]
    row_fingerprints = [
        invoice_fingerprint(
            company_gstin=company_gstin, return_type=return_type,
            party_gstin=row.get(party_field, "") if party_field else "",
            invoice_no=row.get("invoice_no", ""), invoice_date=row.get("invoice_date"),
            invoice_type=row.get("invoice_type", ""), taxable_value=row.get("taxable_value"),
            cgst=row.get("cgst"), sgst=row.get("sgst"), igst=row.get("igst"),
            invoice_value=row.get("invoice_value"),
        )
        for row in rows
    ]
    existing = set()
    if row_fingerprints:
        scope = GSTInvoice.objects.filter(
            dedup_fingerprint__in=set(row_fingerprints), import_batch=active_batch
        ) if active_batch else GSTInvoice.objects.none()
        existing = set(scope.values_list("dedup_fingerprint", flat=True))
    registry_by_hash = registry_by_hash or {}
    new_rows, prepared_retries, duplicate_count, already_imported_count = [], set(), 0, 0
    for row, fingerprint in zip(rows, row_fingerprints):
        voucher_hash = row.get("_voucher_identity_hash", "")
        registry = registry_by_hash.get(voucher_hash)
        if registry and registry.import_status in FINAL_SUCCESS_STATUSES:
            # A. ALREADY_IMPORTED -- counted, never mutated, never resent.
            row["_preview_status"] = "ALREADY_IMPORTED"
            duplicate_count += 1
            already_imported_count += 1
            continue
        if registry:
            if registry.import_status == PENDING and fingerprint in existing:
                # Nothing has ever actually been attempted for this voucher
                # (PENDING means "uploaded, not yet validated/sent"), and the
                # re-uploaded content is byte-for-byte identical to what is
                # already on file for it -- this is a plain duplicate of an
                # unresolved row, not a correction, so recreating another
                # identical PENDING invoice would only be noise. FAILED/
                # NEEDS_ATTENTION rows are never matched here (a real prior
                # attempt happened -- see spec: revalidate them regardless of
                # whether the content changed, since e.g. a required Tally
                # master may exist now even for identical data).
                duplicate_count += 1
                row["_preview_status"] = "ALREADY_IMPORTED"
                continue
            # B. RETRY_CANDIDATE -- once per voucher identity per upload
            # (multiple source rows can share one invoice-level identity).
            if voucher_hash not in prepared_retries:
                prepare_invoice_for_retry(registry)
                prepared_retries.add(voucher_hash)
        elif fingerprint in existing:
            # D. DUPLICATE (no registry, but identical content already on file).
            duplicate_count += 1
            row["_preview_status"] = "ALREADY_IMPORTED"
            continue
        # C. NEW, or B continuing through to a fresh revalidation attempt.
        row["_preview_status"] = "READY"
        row["dedup_fingerprint"] = fingerprint
        new_rows.append(row)
    return new_rows, duplicate_count, already_imported_count, len(prepared_retries)


@transaction.atomic
def import_file(file_obj, return_type, return_period, user=None, selected_tally_company="",
                stable_tally_company_id=""):
    cleanup_expired_processing_rows()
    if return_type not in RETURN_TYPES:
        raise ValueError("Unsupported return type")
    # Format (how the file is read) and return type (which alias/party
    # mapping applies) are independent -- a GSTR-1 file may be Excel just as
    # easily as a GSTR-2B file may be CSV. Sniff the format, then hand the
    # actual return_type through so the correct canonical mapping is used
    # regardless of which extension was uploaded.
    extension = validate_upload_type(file_obj, canonical_invoice.ALLOWED_EXTENSIONS)
    file_hash, file_size = file_content_fingerprint(file_obj)
    rows, metadata = canonical_invoice.parse_source(file_obj, return_type, extension=extension)
    if not rows:
        raise ValueError("No supported invoice records were found")
    period = return_period or metadata.get("return_period", "")
    company_gstin = metadata.get("company_gstin", "")
    product_license = _current_product_license(user, company_gstin)
    fingerprint = normalized_source_fingerprint(rows)
    company_source = metadata.get("company_source") or {}
    if selected_tally_company:
        company_source = {**company_source, "selected_tally_company": selected_tally_company}
    if stable_tally_company_id:
        company_source = {**company_source, "stable_tally_company_id": stable_tally_company_id}
    company_scope_id = company_scope_id_for(
        company_gstin=company_gstin,
        return_type=return_type,
        selected_tally_company=(company_source.get("selected_tally_company") or company_source.get("company_name") or ""),
        stable_tally_company_id=company_source.get("stable_tally_company_id", ""),
    )
    party_field = canonical_invoice.PARTY_FIELDS.get(return_type, (None,))[0]
    voucher_hashes = [
        row_voucher_identity(row, company_gstin=company_gstin, return_type=return_type,
                             party_field=party_field, company_scope_id=company_scope_id)
        for row in rows
    ]
    for row, voucher_hash in zip(rows, voucher_hashes):
        row["_voucher_identity_hash"] = voucher_hash
    active_batch = GSTImportBatch.objects.select_for_update().filter(
        company_gstin=company_gstin, gst_return_type=return_type,
        company_scope_id=company_scope_id, expires_at__gt=timezone.now(),
    ).order_by("-uploaded_at").first() if company_gstin else None
    registry_by_hash = (
        registry_rows_for_hashes(
            batch=active_batch,
            company_gstin=company_gstin,
            return_type=return_type,
            voucher_hashes=voucher_hashes,
            company_scope_id=company_scope_id,
        )
        if company_gstin and active_batch else {}
    )
    summary = active_batch.company_import_summary if active_batch else None
    file_type = file_type_label(extension)
    if company_gstin and not active_batch:
        summary = get_or_create_company_import_summary(
            company_gstin=company_gstin,
            company_name=(metadata.get("company_source") or {}).get("company_name", ""),
            return_type=return_type,
            source_file_name=file_obj.name,
            source_format=file_type,
            total_source_count=len(rows),
            voucher_hashes=voucher_hashes,
            company_scope_id=company_scope_id,
        )
    new_rows, duplicate_rows, already_imported_rows, retry_candidate_rows = _dedupe_rows(
        rows, company_gstin=company_gstin, return_type=return_type, user=user,
        registry_by_hash=registry_by_hash, company_scope_id=company_scope_id, active_batch=active_batch,
    )
    batch = active_batch or GSTImportBatch.objects.create(file_name=file_obj.name, file_type=file_type_label(extension),
        gst_return_type=return_type, total_rows=len(rows), source_parties=metadata.get("parties", {}),
        company_import_summary=summary,
        product_license=product_license,
        company_gstin=company_gstin, company_scope_id=company_scope_id,
        company_gstin_candidates=metadata.get("company_gstin_candidates", []),
        company_details=company_source,
        company_resolution_status=metadata.get("company_resolution_status", ""),
        company_resolution_error=metadata.get("company_resolution_error", ""),
        tax_period=period, file_hash=file_hash, file_size=file_size, source_fingerprint=fingerprint,
        uploaded_by=user if user and user.is_authenticated else None,
        expires_at=timezone.now() + timedelta(hours=24))
    if active_batch and len(rows) > batch.total_rows:
        batch.total_rows = len(rows)
        batch.file_name = file_obj.name
        batch.file_type = file_type
        batch.save(update_fields=["total_rows", "file_name", "file_type", "updated_at"])
    if not active_batch and summary:
        batch.company_import_summary = summary
        batch.save(update_fields=["company_import_summary", "updated_at"])
    invoices = []
    failed_rows = 0
    for row in new_rows:
        try:
            row.pop("_voucher_identity_hash", None)
            row.pop("_preview_status", None)
            row["filing_period"] = row.get("filing_period") or period
            row["filing_type"] = row.get("filing_type") or return_type
            # The source date remains immutable.  A prior-period row uploaded
            # into a later processing period posts on that period's first day.
            invoice_date = row.get("invoice_date")
            posting_period = period or row.get("filing_period") or ""
            original_period = row.get("filing_period") or ""
            if invoice_date and posting_period:
                from .carry_forward import posting_date_for
                voucher_date, carry = posting_date_for(invoice_date, original_period, posting_period)
                row.update(voucher_date=voucher_date, is_carry_forward=carry,
                           original_period=original_period, posting_period=posting_period)
            invoice = GSTInvoice.objects.create(import_batch=batch, **row)
            invoices.append(invoice)
            if summary:
                upsert_voucher_registry(batch, invoice)
        except (TypeError, ValueError):
            failed_rows += 1
    # Keep duplicate source rows in the persisted preview. They are never
    # actionable (processing_state=DUPLICATE), but hiding them made a re-upload
    # look like data had disappeared and prevented users from inspecting the
    # complete file. Voucher generation only selects PENDING/RETRY rows.
    for row in rows:
        if row.get("_preview_status") != "ALREADY_IMPORTED":
            continue
        try:
            duplicate_row = dict(row)
            duplicate_row.pop("_voucher_identity_hash", None)
            duplicate_row.pop("_preview_status", None)
            duplicate_row["filing_period"] = duplicate_row.get("filing_period") or period
            duplicate_row["filing_type"] = duplicate_row.get("filing_type") or return_type
            duplicate_row["processing_state"] = "DUPLICATE"
            duplicate_row["dedup_fingerprint"] = invoice_fingerprint(
                company_gstin=company_gstin, return_type=return_type,
                party_gstin=duplicate_row.get(party_field, "") if party_field else "",
                invoice_no=duplicate_row.get("invoice_no", ""), invoice_date=duplicate_row.get("invoice_date"),
                invoice_type=duplicate_row.get("invoice_type", ""), taxable_value=duplicate_row.get("taxable_value"),
                cgst=duplicate_row.get("cgst"), sgst=duplicate_row.get("sgst"), igst=duplicate_row.get("igst"),
                invoice_value=duplicate_row.get("invoice_value"),
            )
            GSTInvoice.objects.create(import_batch=batch, **duplicate_row)
        except (TypeError, ValueError):
            failed_rows += 1
    # This is the upload's actionable-row count, not the number of retained
    # rows accumulated in the active session.  The permanent summary holds
    # the aggregate session total.
    batch.imported_rows = len(invoices)
    batch.failed_rows = failed_rows
    batch.duplicate_rows = duplicate_rows
    batch.save(update_fields=["imported_rows", "failed_rows", "duplicate_rows", "updated_at"])
    if summary:
        increment_summary_skipped(summary, duplicate_rows)
        # A retry can replace FAILED/NEEDS_ATTENTION working rows with fresh
        # PENDING rows. Recalculate from the registry so the permanent summary
        # reflects the latest working state before Tally processing starts.
        from .import_summary import update_company_import_summary
        update_company_import_summary(summary)
    logger.info(
        "IMPORT UPLOAD PRECHECK batch_id=%s total_rows=%s new_rows=%s already_imported=%s "
        "retry_candidates=%s duplicate_rows=%s failed_rows=%s",
        batch.id, len(rows), len(invoices), already_imported_rows, retry_candidate_rows,
        duplicate_rows, failed_rows,
    )
    if settings.DEBUG:
        logger.debug(
            "gst import: user=%s batch_id=%s return_type=%s duplicate_scope=%s "
            "product_license_id=%s total_rows=%s new_rows=%s duplicate_rows=%s failed_rows=%s decision=%s",
            getattr(user, "id", None), batch.id, return_type, company_gstin or f"user:{getattr(user, 'id', None)}",
            product_license.id if product_license else None, len(rows), len(invoices), duplicate_rows, failed_rows,
            "ACCEPTED" if invoices else "ALL_DUPLICATE",
        )
    return batch
