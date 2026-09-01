from django.db import transaction

from gst_tally.models import GSTImportBatch, GSTInvoice
from . import gstr1_parser, gstr2a_parser, gstr2b_parser
from .import_identity import DuplicateImportError, find_confirmed_duplicate_batch, normalized_source_fingerprint
from gst_tally.utils.uploaded_files import validate_upload_type

RETURN_TYPES = {"GSTR1", "GSTR2A", "GSTR2B"}
FORMAT_PARSERS = {"json": gstr1_parser.parse, "csv": gstr2a_parser.parse,
                  "xlsx": gstr2b_parser.parse, "xls": gstr2b_parser.parse}
FILE_TYPES = {"json": "JSON", "csv": "CSV", "xlsx": "EXCEL", "xls": "EXCEL"}


@transaction.atomic
def import_file(file_obj, return_type, return_period, user=None):
    if return_type not in RETURN_TYPES:
        raise ValueError("Unsupported return type")
    extension = validate_upload_type(file_obj, FORMAT_PARSERS)
    rows, metadata = FORMAT_PARSERS[extension](file_obj)
    if not rows:
        raise ValueError("No supported invoice records were found")
    period = return_period or metadata.get("return_period", "")
    company_gstin = metadata.get("company_gstin", "")
    fingerprint = normalized_source_fingerprint(rows)
    # Filename is never the identity: a renamed but byte-identical file is still
    # a duplicate; changed content under an unchanged filename is still eligible.
    duplicate = find_confirmed_duplicate_batch(company_gstin, return_type, period, fingerprint)
    if duplicate is not None:
        raise DuplicateImportError(duplicate)
    batch = GSTImportBatch.objects.create(file_name=file_obj.name, file_type=FILE_TYPES[extension],
        gst_return_type=return_type, total_rows=len(rows), source_parties=metadata.get("parties", {}),
        company_gstin=company_gstin, company_gstin_candidates=metadata.get("company_gstin_candidates", []),
        company_details=metadata.get("company_source", {}),
        company_resolution_status=metadata.get("company_resolution_status", ""),
        company_resolution_error=metadata.get("company_resolution_error", ""),
        tax_period=period, source_fingerprint=fingerprint)
    invoices = []
    failed_rows = 0
    for row in rows:
        try:
            row["filing_period"] = row.get("filing_period") or period
            row["filing_type"] = row.get("filing_type") or return_type
            invoices.append(GSTInvoice(import_batch=batch, **row))
        except (TypeError, ValueError):
            failed_rows += 1
    GSTInvoice.objects.bulk_create(invoices)
    batch.imported_rows = len(invoices)
    batch.failed_rows = failed_rows
    batch.save(update_fields=["imported_rows", "failed_rows", "updated_at"])
    return batch
