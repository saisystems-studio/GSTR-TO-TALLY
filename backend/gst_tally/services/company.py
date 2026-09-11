import re
from datetime import date
from gst_tally.services.gst_lookup.service import GSTLookupService
from gst_tally.services.import_summary import ensure_summary_for_batch
from gst_tally.services.party_lookup import normalize_gstin, valid_gstin
from gst_tally.tally.mappings import clean_name
from gst_tally.tally.validators import state_name

PINCODE = re.compile(r"(?<!\d)([1-9][0-9]{5})(?!\d)")


def resolve_verified_company_gstin(batch):
    """The single source of truth for "what GSTIN does this batch's verified
    Tally company actually have" -- used everywhere a stage after Company
    Verification (license check, master preparation) needs that GSTIN, so
    none of them fall back to reading batch.company_gstin directly and
    re-deriving a stale/blank value into a false GSTIN_REQUIRED or
    GSTIN_MISMATCH. Checked in order, first real value wins:

    1. CompanyDetails -- the record Company Verification itself persisted
       (services/company_verification.py) for the exact Tally company name
       this batch was verified against.
    2. batch.company_details["gstin"] -- already-resolved company details on
       the batch (e.g. from resolve_batch_company / the source GST return).
    3. batch.company_gstin -- the import batch's own company GSTIN field.
    4. TallyCompanyMapping -- a GSTIN this same Tally company name was bound
       to on an earlier successful verification.
    """
    from gst_tally.models import CompanyDetails, TallyCompanyMapping
    from gst_tally.tally.odbc import normalize_company_name

    details = batch.company_details or {}
    selected_name = str(details.get("selected_tally_company") or "").strip()

    if selected_name:
        record = (CompanyDetails.objects.filter(company_name=normalize_company_name(selected_name))
                  .order_by("-verified_at").first())
        if record and record.gstin:
            return normalize_gstin(record.gstin)

    if details.get("gstin"):
        return normalize_gstin(details["gstin"])

    if batch.company_gstin:
        return normalize_gstin(batch.company_gstin)

    if selected_name:
        mapping = TallyCompanyMapping.objects.filter(tally_company_name=selected_name).order_by("-updated_at").first()
        if mapping and mapping.gstin:
            return normalize_gstin(mapping.gstin)

    return ""


CURRENT_TALLY_FALLBACK = "CURRENT_TALLY_FALLBACK"


def resolve_source_company_gstin(batch, *, current_tally_company_gstin="", current_tally_company_name=""):
    """Resolve and persist this batch's return-owner GSTIN exactly once.

    1. If the batch already carries a company_gstin -- extracted from the
       source file itself at upload time (canonical_invoice.py, which never
       reads a supplier/party GSTIN for this), or bound by an earlier call
       to this same function -- that value is authoritative and is returned
       unchanged. A batch already bound to one company must never be
       silently rebound to a different one.
    2. Only when the file's own GSTIN is genuinely unresolved (blank, and
       not merely ambiguous -- see the candidates guard below) does this
       fall back to the GSTIN of whichever single company is currently open
       in Tally, persisting it onto the batch (company_source =
       CURRENT_TALLY_FALLBACK) so every later step (Invoice Preview, Party
       Details, License Verification) reads the exact same saved value
       instead of re-deriving it.
    3. Ambiguous files (more than one company_gstin_candidates entry --
       "Selection Required") are left alone: that state has its own
       explicit user-driven GSTIN-picker flow and must never be silently
       resolved by a Tally fallback instead.

    Returns (source_company_gstin, source_company_name, company_source).
    Never invents a value from party/supplier/invoice-row data -- the only
    two sources are the file's own extraction (already done before this is
    called) and the live Tally connection.
    """
    existing = normalize_gstin(batch.company_gstin)
    if existing:
        details = batch.company_details or {}
        return existing, str(details.get("company_name") or ""), str(details.get("company_source") or "")

    if batch.company_gstin_candidates:
        # Ambiguous, not missing -- the GSTIN picker flow owns this case.
        return "", "", ""

    fallback_gstin = normalize_gstin(current_tally_company_gstin)
    current_tally_company_name = str(current_tally_company_name or "").strip()
    if not (fallback_gstin and valid_gstin(fallback_gstin) and current_tally_company_name):
        return "", "", ""

    batch.company_gstin = fallback_gstin
    batch.company_details = {**(batch.company_details or {}), "gstin": fallback_gstin,
                              "company_name": current_tally_company_name, "company_source": CURRENT_TALLY_FALLBACK}
    batch.company_resolution_status = "Detected"
    batch.company_resolution_error = ""
    batch.save(update_fields=["company_gstin", "company_details", "company_resolution_status",
                               "company_resolution_error", "updated_at"])
    ensure_summary_for_batch(batch)
    return fallback_gstin, current_tally_company_name, CURRENT_TALLY_FALLBACK


def get_financial_year_start(value):
    if not value: return None
    year = value.year if value.month >= 4 else value.year - 1
    return date(year, 4, 1)

def financial_year_details(value):
    start = get_financial_year_start(value)
    if not start: return None
    return {"label": f"{start.year}-{str(start.year + 1)[-2:]}", "start": start, "end": date(start.year + 1, 3, 31)}

def source_company_metadata(candidates, source=None):
    values = sorted({normalize_gstin(value) for value in candidates if valid_gstin(normalize_gstin(value))})
    if len(values) > 1:
        return {"company_gstin": "", "company_gstin_candidates": values, "company_source": source or {},
                "company_resolution_status": "Selection Required", "company_resolution_error": "MULTIPLE_COMPANY_GSTINS"}
    return {"company_gstin": values[0] if values else "", "company_gstin_candidates": values, "company_source": source or {},
            "company_resolution_status": "Detected" if values else "Not Found",
            "company_resolution_error": "" if values else "COMPANY_GSTIN_NOT_FOUND"}

def _first(mapping, *keys):
    indexed = {str(key).casefold(): value for key, value in (mapping or {}).items()}
    return next((str(indexed[key.casefold()]).strip() for key in keys if indexed.get(key.casefold()) not in (None, "")), "")

def normalize_company(gstin, source=None, lookup=None, invoice_date=None):
    source, lookup = source or {}, lookup or {}
    address = _first(lookup, "principal_address", "principal_place_of_business", "address") or _first(source, "address", "principal_place_of_business", "Addr1")
    pincode = _first(lookup, "pincode") or _first(source, "pincode", "pin", "Pin")
    if not pincode:
        match = PINCODE.search(address); pincode = match.group(1) if match else ""
    if pincode: address = re.sub(rf"(?:\s*[-,]\s*)?{re.escape(pincode)}\s*$", "", address).strip(" ,- ")
    explicit_state = _first(lookup, "state", "state_name") or _first(source, "state", "state_name", "Stcd")
    state = state_name(explicit_state) or state_name(gstin[:2])
    legal = clean_name(_first(lookup, "legal_name")) or clean_name(_first(source, "legal_name", "LglNm"))
    trade = clean_name(_first(lookup, "trade_name")) or clean_name(_first(source, "trade_name", "TrdNm"))
    fy = get_financial_year_start(invoice_date)
    return {"gstin": gstin, "company_name": trade or legal, "legal_name": legal, "trade_name": trade,
            "address": address, "state": state, "country": _first(source, "country") or "India", "pincode": pincode,
            "mobile": _first(source, "mobile", "phone", "Ph"), "email": _first(source, "email", "Em"),
            "pan": gstin[2:12] if valid_gstin(gstin) else "", "registration_date": _first(lookup, "registration_date"),
            "gst_status": _first(lookup, "gst_status", "status"), "taxpayer_type": _first(lookup, "taxpayer_type"),
            "financial_year_beginning": fy.isoformat() if fy else "", "books_beginning": fy.isoformat() if fy else "", "status": "Resolved"}

def resolve_batch_company(batch, selected_gstin="", force=False):
    candidates = batch.company_gstin_candidates or ([batch.company_gstin] if batch.company_gstin else [])
    gstin = normalize_gstin(selected_gstin or batch.company_gstin)
    if len(candidates) > 1 and not selected_gstin: raise ValueError("MULTIPLE_COMPANY_GSTINS")
    if gstin not in candidates and candidates: raise ValueError("Selected GSTIN does not belong to this batch")
    if not valid_gstin(gstin): raise ValueError("COMPANY_GSTIN_NOT_FOUND")
    source = batch.company_details or {}; lookup = {}
    try:
        _, lookup = GSTLookupService.lookup_cached(gstin, force_refresh=force)
    except Exception:
        # Source identity remains usable; lookup failure is visible in the normalized status.
        lookup = {}
    first_date = batch.invoices.exclude(invoice_date=None).order_by("invoice_date").values_list("invoice_date", flat=True).first()
    details = normalize_company(gstin, source, lookup, first_date)
    details["status"] = "Resolved" if details["company_name"] else "Incomplete"
    batch.company_gstin = gstin; batch.company_details = details
    batch.company_resolution_status = details["status"]; batch.company_resolution_error = "" if details["company_name"] else "COMPANY_NAME_UNAVAILABLE"
    batch.save(update_fields=["company_gstin", "company_details", "company_resolution_status", "company_resolution_error", "updated_at"])
    ensure_summary_for_batch(batch)
    return details
