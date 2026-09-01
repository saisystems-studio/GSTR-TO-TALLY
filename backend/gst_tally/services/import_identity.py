"""Duplicate-upload identity: a deterministic fingerprint of parsed source
content, independent of filename, and the confirmed-duplicate gate that uses
it. See spec section Q -- filename is never the identity; a renamed but
byte-identical file is still a duplicate, and changed content under the same
filename is still eligible."""
import hashlib
import json

from gst_tally.utils.file_utils import json_safe


def normalized_source_fingerprint(rows):
    """SHA-256 over the parsed, normalized invoice rows -- never the raw file
    bytes or filename. Decimal/date values are serialized deterministically
    (via ``json_safe``) so equal accounting content always hashes equal."""
    serialized = json.dumps(json_safe(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class DuplicateImportError(Exception):
    """Raised when this exact (company GSTIN, return type, tax period, source
    fingerprint) was already imported and confirmed in Tally. Carries the
    original batch so the caller can reference it without creating a new one."""
    def __init__(self, batch):
        self.batch = batch
        super().__init__(
            "This return data has already been imported for this company and period. "
            "Duplicate import is not allowed.")


def find_confirmed_duplicate_batch(company_gstin, return_type, tax_period, fingerprint):
    """The most recent earlier batch sharing this scope and fingerprint that
    reached a confirmed successful/verified Tally outcome, or ``None``.

    An earlier upload that merely exists (never imported, or failed) is not a
    block -- only a batch with at least one voucher Tally has actually
    confirmed stops a re-upload of the same content.
    """
    if not fingerprint:
        return None
    from gst_tally.models import GSTImportBatch, TallyVoucherMapping
    candidates = GSTImportBatch.objects.filter(
        company_gstin=company_gstin, gst_return_type=return_type,
        tax_period=tax_period, source_fingerprint=fingerprint,
    ).order_by("-created_at")
    for candidate in candidates:
        if TallyVoucherMapping.objects.filter(batch=candidate, import_status="Imported").exists():
            return candidate
    return None
