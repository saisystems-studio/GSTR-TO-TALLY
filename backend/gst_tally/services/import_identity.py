"""Duplicate-upload identity helpers."""
import hashlib
import json

from gst_tally.utils.file_utils import json_safe
from gst_tally.services.party_lookup import normalize_gstin


def _rewind(file_obj, position=0):
    if hasattr(file_obj, "seek"):
        file_obj.seek(position)


def file_content_fingerprint(file_obj):
    """SHA-256 over the exact uploaded bytes, plus byte size.

    The file pointer is restored to its original position so parsers can read
    the upload normally after the duplicate check.
    """
    position = file_obj.tell() if hasattr(file_obj, "tell") else 0
    _rewind(file_obj, 0)
    digest = hashlib.sha256()
    size = 0
    if hasattr(file_obj, "chunks"):
        iterator = file_obj.chunks()
    else:
        iterator = iter(lambda: file_obj.read(1024 * 1024), b"")
    for chunk in iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        size += len(chunk)
        digest.update(chunk)
    _rewind(file_obj, position)
    return digest.hexdigest(), size


def normalized_source_fingerprint(rows):
    """SHA-256 over the parsed, normalized invoice rows -- never the raw file
    bytes or filename. Decimal/date values are serialized deterministically
    (via ``json_safe``) so equal accounting content always hashes equal."""
    serialized = json.dumps(json_safe(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def invoice_fingerprint(*, company_gstin, return_type, party_gstin, invoice_no,
                         invoice_date, invoice_type, taxable_value, cgst, sgst,
                         igst, invoice_value):
    """Deterministic identity of one invoice's business data -- the fields a
    human would use to recognize "this is the same invoice I already
    imported", never the source filename or raw bytes. Two uploads (even in
    different files, different byte encodings) that carry the exact same
    values here are the same invoice; genuinely different data for the same
    company GSTIN always hashes differently and is always allowed.

    Deliberately excludes ProductLicense/user/device identity -- duplicate
    detection is about the DATA, scoped by company GSTIN + return type, never
    by who is licensed to import it (see find_duplicate_batch for the same
    principle applied at the whole-file level).
    """
    payload = {
        "company_gstin": normalize_gstin(company_gstin),
        "return_type": str(return_type or "").strip().upper(),
        "party_gstin": normalize_gstin(party_gstin),
        "invoice_no": str(invoice_no or "").strip().upper(),
        "invoice_type": str(invoice_type or "").strip().upper(),
    }
    serialized = json.dumps(json_safe({
        **payload,
        "invoice_date": invoice_date,
        "taxable_value": taxable_value,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "invoice_value": invoice_value,
    }), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class DuplicateImportError(Exception):
    """Raised when the exact same uploaded file (same bytes, same company
    GSTIN/customer scope) already exists as an import batch. Carries the
    original batch so the caller can reference it without creating a new one
    or re-inserting its invoice rows."""
    def __init__(self, batch):
        self.batch = batch
        super().__init__("This file has already been imported. Delete the existing imported data before uploading this file again.")


def find_duplicate_batch(*, file_hash, user=None, company_gstin="", return_type=""):
    """Legacy whole-file duplicate check retained for compatibility only.

    The modern workflow intentionally allows a previously processed file to be
    uploaded again. Duplicate protection is enforced at the voucher level via the
    permanent registry and the invoice dedup fingerprint, not by hard-blocking on
    filename or raw file hash.
    """
    return None
