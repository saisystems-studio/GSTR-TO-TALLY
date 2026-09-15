from .gst_lookup.service import active_provider_name
import re
import logging
from datetime import timedelta
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from gst_tally.models import GSTParty
from gst_tally.tally.validators import state_name as state_name_for_gstin_prefix
from .gst_lookup.base import (GSTLookupAuthenticationError, GSTLookupConfigurationError, GSTLookupNotFoundError, GSTLookupProviderError, GSTLookupRateLimitError, GSTLookupTimeoutError, has_usable_text)
from .gst_lookup.service import GSTLookupService
from .gst_lookup.providers.sandbox import ACCESS_CACHE_KEY, SESSION_CACHE_KEY, SandboxNetworkError, SandboxOTPRequired
from .party_ledger_name import resolve_party_ledger_name

logger = logging.getLogger(__name__)

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

def normalize_gstin(value): return str(value or "").strip().upper()
def valid_gstin(value): return bool(GSTIN_PATTERN.fullmatch(value))
def lookup_is_configured(): return GSTLookupService.configured()
def lookup_configuration_message():
    return "GSTIN lookup source is not configured." if GSTLookupService.configuration_issues() else ""
def party_is_complete(party):
    gstin = normalize_gstin(getattr(party, "gstin", ""))
    has_name = party and (
        (has_usable_text(party.trade_name) and normalize_gstin(party.trade_name) != gstin) or
        (has_usable_text(party.legal_name) and normalize_gstin(party.legal_name) != gstin)
    )
    return bool(party and party.party_data_status != "Incomplete" and
                valid_gstin(normalize_gstin(party.gstin)) and has_name)
def party_has_address(party): return bool(party and has_usable_text(party.principal_place_of_business))
def party_eligibility(party, source_name="", gstin="", source_state=""):
    """Authoritative eligibility. Presentation statuses are deliberately not inputs.

    Sandbox taxpayer lookup is OPTIONAL ENRICHMENT, never an eligibility
    gate: a party is ready for its Tally ledger whenever a valid GSTIN, a
    usable name (source or Sandbox), and a resolvable state (Sandbox,
    source, or the GSTIN's own state-code prefix) are already available --
    country is always India and registration type defaults to Regular for a
    valid GSTIN (see mappings.registration_type_for), neither requiring
    Sandbox. A Sandbox lookup that failed, hasn't run yet, or needs an OTP
    is surfaced only as a non-blocking `warning`/`warning_code`, never as
    `tally_ready=False`, as long as those minimum identity fields are
    already satisfied from the source file or a prior successful lookup.
    """
    gstin = normalize_gstin(getattr(party, "gstin", "") or gstin)
    if not valid_gstin(gstin): return {"tally_ready": False, "reason": "Invalid GSTIN", "party_name": ""}
    name = resolve_party_ledger_name(party, {"party_name": source_name}, gstin)
    if name == gstin:
        name = ""
    fallback = not bool(name)
    # The party's state is always derivable from its own (already-validated)
    # GSTIN prefix -- the same derivation `transaction_type` already relies on
    # elsewhere -- so a missing Sandbox/source state never blocks the party;
    # it only widens the identity signals actually available.
    state = (getattr(party, "state_name", "") if party else "") or source_state or state_name_for_gstin_prefix(gstin[:2])
    if not has_usable_text(state):
        return {"tally_ready": False, "reason": "Sandbox taxpayer state is unavailable", "party_name": name, "name_source": "Sandbox"}
    sandbox = str(active_provider_name() or "").strip().lower() == "sandbox"
    sandbox_real_name = party and str(getattr(party, "lookup_source", "") or "").strip().lower() == "sandbox" and (
        (has_usable_text(getattr(party, "trade_name", "")) and normalize_gstin(party.trade_name) != gstin) or
        (has_usable_text(getattr(party, "legal_name", "")) and normalize_gstin(party.legal_name) != gstin)
    )
    sandbox_failure_statuses = {"Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required",
                                "Sandbox Session Required", "Sandbox Session Failed", "Rate Limited"}
    sandbox_unavailable = sandbox and (not party or (party.lookup_status in sandbox_failure_statuses and not sandbox_real_name))
    result = {"tally_ready": True, "party_name": name or gstin,
              "name_source": ("Sandbox" if sandbox_unavailable else "GSTIN") if fallback else "Taxpayer"}
    if sandbox_unavailable:
        # Optional-enrichment failure only -- name/GSTIN/state above already
        # satisfy the minimum party-master requirements, so this never blocks
        # the voucher; it is surfaced purely as a non-blocking warning.
        authentication_blocked = party and party.lookup_status in {"OTP Required", "Sandbox Session Required"}
        reason = ("Sandbox taxpayer lookup has not been attempted yet." if not party else
                  "OTP required" if party.lookup_status == "OTP Required" else
                  "GST Sandbox authentication required" if authentication_blocked else
                  "Sandbox taxpayer details are unavailable")
        result.update(warning=reason, warning_code="SANDBOX_PARTY_DETAILS_UNAVAILABLE")
    elif fallback: result["warning"] = "GSTIN fallback ledger name"
    elif not party_has_address(party): result["warning"] = "Address unavailable"
    return result
def party_is_fresh(party):
    fresh_after = timezone.now() - timedelta(days=settings.GST_PARTY_FRESH_DAYS)
    return bool(party_is_complete(party) and party.last_fetched_at and party.last_fetched_at >= fresh_after)
def retry_allowed(party):
    return not (party and party.retry_not_before and party.retry_not_before > timezone.now())
def _retry_not_before(value):
    if value in (None, ""): return None
    try: return timezone.now() + timedelta(seconds=max(0, float(value)))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(str(value))
            return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
        except (TypeError, ValueError, OverflowError): return None
def _truncate(text, limit=100):
    """Fit within lookup_error's CharField(max_length=100) -- now that real
    provider exception messages are surfaced instead of a fixed generic
    string, an unusually long one must never crash the save with a raw DB
    truncation error."""
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _coded(diagnostics, code):
    """Attach a stable, distinguishable failure code to a diagnostics dict --
    `lookup_status`/`lookup_error` alone often collapse several genuinely
    different Sandbox failures (auth, not-found, malformed body, HTTP 5xx)
    into the same "Sandbox Lookup Failed" status string, hiding which one
    actually happened. This is purely additive to diagnostics; it never
    changes status/name_source/tally_ready."""
    merged = dict(diagnostics or {
        "configured_provider": "sandbox", "actual_provider_used": "sandbox",
        "provider_used": "sandbox", "cache_hit": False, "lookup_attempted": False,
        "http_status": None, "sandbox_success": False, "normalized": False,
        "api_calls": 0,
    })
    merged["lookup_error_code"] = code
    return merged


def _record_status(gstin, status, reason, existing=None, diagnostics=None):
    reason = _truncate(reason)
    sandbox = str(active_provider_name() or "").strip().lower() == "sandbox"
    # Persisted alongside the row so a later GET (a fresh GSTParty query with
    # no in-memory diagnostics attached) still shows the real last-attempt
    # detail instead of falling back to blank/false defaults.
    diag = diagnostics or ({"gstin": gstin, "configured_provider": "sandbox", "provider_requested": "sandbox",
        "actual_provider_used": "sandbox", "provider_used": "sandbox", "cache_hit": False,
        "sandbox_access_token_valid": bool(cache.get(ACCESS_CACHE_KEY)),
        "taxpayer_session_active": bool(cache.get(SESSION_CACHE_KEY)), "lookup_attempted": True,
        "http_status": None, "sandbox_success": False, "normalized": False, "status": status,
        "warning": None, "safe_reason": reason} if sandbox else {})
    party = existing
    if party:
        party.lookup_source = active_provider_name() or party.lookup_source
        party.lookup_status = status
        party.lookup_error = reason
        party.lookup_diagnostics = diag
        if not party_is_fresh(party): party.party_data_status = "Incomplete"
        party.save(update_fields=["lookup_source", "lookup_status", "lookup_error", "lookup_diagnostics", "party_data_status", "updated_at"])
    else:
        party = GSTParty.objects.create(gstin=gstin, lookup_source=active_provider_name(),
                                        lookup_status=status, lookup_error=reason, lookup_diagnostics=diag,
                                        party_data_status="Incomplete")
    party._lookup_diagnostics = diag
    return party


def _source_party(batch, gstin):
    return (batch.source_parties or {}).get(gstin, {}) if batch else {}


def _source_party_name(source, gstin=""):
    gstin = normalize_gstin(gstin)
    name = (resolve_party_ledger_name(source=source, gstin=gstin) or "").strip()
    # resolve_party_ledger_name's own last resort is the GSTIN itself -- undo
    # just that tail here so "no real source name" still reads as "", not a
    # GSTIN masquerading as a name, for callers that OR this into further
    # fallbacks (existing DB name, then GSTIN) of their own.
    return "" if gstin and name.upper() == gstin else name


def _keep(new_value, existing_value):
    """Prefer real new data; a blank/unusable new value must never overwrite
    an existing real value -- a later failed lookup must not erase what an
    earlier successful one already fetched."""
    new_value = str(new_value or "").strip()
    return new_value if has_usable_text(new_value) else str(existing_value or "").strip()


def _fallback_party_identity(gstin, batch, status, reason, existing=None, diagnostics=None):
    """Build/refresh a GSTParty when a real Sandbox lookup could not run or
    failed -- from the source's own party fields, falling back to whatever
    the existing record already has (never blanking real data with this
    attempt's absence of it). A readable name here (source-supplied or a
    previously kept one) is only a placeholder identity, never a completed
    taxpayer profile: this function only ever runs on a Sandbox
    failure/fallback path, so a fallback name alone must never mark
    party_data_status Complete. Marking it Complete just because *some*
    non-GSTIN name existed used to make party_is_complete/party_is_fresh
    treat the record as fully enriched forever, silently skipping every
    future Sandbox retry for that GSTIN even though legal_name/address/
    pincode were never actually fetched. The one case that legitimately
    stays Complete is a record that already holds a genuine prior Sandbox
    address -- this transient failure must not downgrade already-good data."""
    reason = _truncate(reason)
    source = _source_party(batch, gstin)
    existing_trade_name = getattr(existing, "trade_name", "") if existing else ""
    existing_name_usable = has_usable_text(existing_trade_name) and existing_trade_name.strip().upper() != gstin.upper()
    name = _source_party_name(source, gstin) or (existing_trade_name if existing_name_usable else "") or gstin
    real_name = name != gstin
    existing_address = ((getattr(existing, "principal_place_of_business", "") or getattr(existing, "address", ""))
                        if existing else "")
    address = _keep(source.get("principal_place_of_business") or source.get("address"), existing_address)
    existing_complete_with_address = bool(existing and existing.party_data_status == "Complete" and
                                          party_has_address(existing))
    # Persisted (not just kept as an in-memory attribute) so a later GET --
    # a fresh GSTParty query with no in-memory diagnostics attached -- still
    # shows the real last-attempt detail instead of blank/false defaults.
    diag = diagnostics or getattr(existing, "_lookup_diagnostics", None) or getattr(existing, "lookup_diagnostics", None) or {
        "gstin": gstin,
        "configured_provider": "sandbox",
        "provider_requested": "sandbox",
        "actual_provider_used": "sandbox",
        "provider_used": "sandbox",
        "cache_hit": False,
        "lookup_attempted": False,
        "sandbox_success": False,
        "normalized": False,
        "status": status,
        "warning": None,
        "safe_reason": reason,
    }
    party, _ = GSTParty.objects.update_or_create(gstin=gstin, defaults={
        "trade_name": name,
        "legal_name": _keep(source.get("legal_name"), getattr(existing, "legal_name", "") if existing else ""),
        "principal_place_of_business": address,
        "address": address,
        "state_name": _keep(source.get("state") or source.get("state_name"), getattr(existing, "state_name", "") if existing else ""),
        "pincode": _keep(source.get("pincode"), getattr(existing, "pincode", "") if existing else ""),
        "taxpayer_type": _keep(source.get("registration_type") or source.get("taxpayer_type"),
                               getattr(existing, "taxpayer_type", "") if existing else ""),
        "lookup_source": ("IMPORT_SOURCE" if _source_party_name(source)
                          else (getattr(existing, "lookup_source", "") or "GSTIN") if real_name else "GSTIN"),
        "lookup_status": status,
        "lookup_error": reason,
        "lookup_diagnostics": diag,
        "party_data_status": "Complete" if existing_complete_with_address else "Incomplete",
        "last_fetched_at": timezone.now(),
        "retry_not_before": None,
    })
    party._lookup_diagnostics = diag
    return party


def normalized_party_result(gstin, party=None, source=None, lookup_status=""):
    source = source or {}
    sandbox_legal = party.legal_name if party and party.lookup_source == "sandbox" and has_usable_text(party.legal_name) else ""
    sandbox_trade = (party.trade_name if party and party.lookup_source == "sandbox" and
                     has_usable_text(party.trade_name) and party.trade_name != sandbox_legal else "")
    source_name = _source_party_name(source, gstin)
    name = sandbox_trade or sandbox_legal or source_name or (party.trade_name if party and has_usable_text(party.trade_name) else "") or gstin
    if sandbox_trade:
        name_source = "SANDBOX_TRADE_NAME"
    elif sandbox_legal:
        name_source = "SANDBOX_LEGAL_NAME"
    elif source_name or (party and party.lookup_source == "IMPORT_SOURCE"):
        name_source = "SOURCE"
    else:
        name_source = "GSTIN"
    address_from_party = (party.principal_place_of_business if party else "") or (party.address if party else "")
    address_from_source = source.get("principal_place_of_business") or source.get("address") or ""
    address = address_from_party or address_from_source or ""
    pincode_from_party = party.pincode if party else ""
    pincode_from_source = source.get("pincode") or ""
    pincode = pincode_from_party or pincode_from_source or ""
    # Per-field provenance for the enrichment fields the backend party object
    # and Step 4 master preview must surface (task spec's BACKEND PARTY
    # OBJECT / UI sections). "SANDBOX" only when the value genuinely came
    # from a completed Sandbox lookup (party.lookup_source == "sandbox") --
    # never fabricated for a source-derived or fallback-persisted value, even
    # though trade_name/legal_name/address/pincode can independently hold
    # SOURCE-derived data (address/pincode's own priority chain above;
    # trade_name/legal_name via the fallback-identity path elsewhere in this
    # module, e.g. IMPORT_SOURCE). "" only when the field is genuinely
    # unavailable from either source -- never invented.
    party_is_sandbox = bool(party) and str(getattr(party, "lookup_source", "") or "").strip().lower() == "sandbox"
    data_source = {
        "trade_name": "SANDBOX" if party_is_sandbox and has_usable_text(party.trade_name if party else "") else
                      ("SOURCE" if has_usable_text(party.trade_name if party else "") else ""),
        "legal_name": "SANDBOX" if party_is_sandbox and has_usable_text(party.legal_name if party else "") else
                      ("SOURCE" if has_usable_text(party.legal_name if party else "") else ""),
        "address": "SANDBOX" if party_is_sandbox and has_usable_text(address_from_party) else
                   ("SOURCE" if has_usable_text(address_from_source) else ""),
        "pincode": "SANDBOX" if party_is_sandbox and has_usable_text(pincode_from_party) else
                   ("SOURCE" if has_usable_text(pincode_from_source) else ""),
    }
    # Genuine Sandbox taxpayer enrichment vs a placeholder name -- kept
    # separate from `tally_ready` so a failed/never-attempted lookup can
    # never present itself as a completed party master (see
    # party_eligibility's GSTIN-fallback comment for why `tally_ready` itself
    # must stay true for voucher import). A source/mapping-derived name
    # ("SOURCE") is only a display fallback, exactly like "GSTIN" -- it still
    # means Sandbox enrichment (legal name, address, pincode, registration
    # details) never actually happened for this party.
    party_details_complete = name_source in {"SANDBOX_TRADE_NAME", "SANDBOX_LEGAL_NAME"}
    return {
        "gstin": gstin,
        "trade_name": party.trade_name if party else "",
        "legal_name": party.legal_name if party else "",
        "name": name,
        "address": address,
        "state": (party.state_name if party else "") or source.get("state") or source.get("state_name") or state_name_for_gstin_prefix(gstin[:2]),
        "pincode": pincode,
        "country": "India" if valid_gstin(gstin) else "",
        "registration_type": (party.taxpayer_type if party else "") or source.get("registration_type") or source.get("taxpayer_type") or "",
        # The taxpayer's GSTIN registration status (e.g. "Active") -- distinct
        # from `registration_type` (Regular/Composition/etc) and from
        # `lookup_status` (this record's own fetch outcome). Only populated
        # once genuinely resolved (Sandbox today; IMPORT_SOURCE if a source
        # file ever carries it) -- never guessed.
        "gstin_status": (party.registration_status if party else "") or "",
        "name_source": name_source,
        "lookup_status": lookup_status or (party.lookup_status if party else ""),
        "party_details_complete": party_details_complete,
        "data_source": data_source,
    }


def _log_party_lookup(gstin, status, party):
    diag = (getattr(party, "_lookup_diagnostics", None) or getattr(party, "lookup_diagnostics", None) or {}) if party else {}
    resolved_name = resolve_party_ledger_name(party, gstin=gstin) if party else gstin
    address = ((getattr(party, "principal_place_of_business", "") or getattr(party, "address", "")) if party else "")
    print("=== PARTY LOOKUP ===")
    print("GSTIN:", gstin)
    print("Sandbox attempted:", bool(diag.get("lookup_attempted")))
    print("HTTP status:", diag.get("http_status"))
    print("Trade name:", getattr(party, "trade_name", "") if party else "")
    print("Legal name:", getattr(party, "legal_name", "") if party else "")
    print("Resolved party name:", resolved_name)
    print("Address:", address)
    print("State:", getattr(party, "state_name", "") if party else "")
    print("Pincode:", getattr(party, "pincode", "") if party else "")
    print("Registration type:", getattr(party, "taxpayer_type", "") if party else "")
    print("Lookup status:", status)
    # Explicit per-field debug trace (task spec's DEBUG LOG section) -- named
    # exactly as requested so it's obvious, per GSTIN, where Sandbox data is
    # being lost between the raw API response and the final party master
    # fields. sandbox_* fields stay blank whenever this GSTParty record's
    # identity didn't actually come from a completed Sandbox lookup (see
    # lookup_source == "sandbox"), never guessed from source/fallback data.
    from_sandbox = bool(party) and str(getattr(party, "lookup_source", "") or "").strip().lower() == "sandbox"
    sandbox_trade = getattr(party, "trade_name", "") if from_sandbox else ""
    sandbox_legal = getattr(party, "legal_name", "") if from_sandbox else ""
    print("=== SANDBOX LOOKUP DEBUG ===")
    print("gstin:", gstin)
    print("sandbox_lookup_attempted:", bool(diag.get("lookup_attempted")))
    print("sandbox_status_code:", diag.get("http_status"))
    print("sandbox_success:", bool(from_sandbox and (has_usable_text(sandbox_trade) or has_usable_text(sandbox_legal))))
    print("sandbox_lgnm:", sandbox_legal)
    print("sandbox_tradeNam:", sandbox_trade)
    print("sandbox_pradr_present:", bool(from_sandbox and address))
    print("sandbox_pincode:", getattr(party, "pincode", "") if from_sandbox else "")
    print("mapped_ledger_name:", resolved_name)
    print("mapped_address:", address)
    print("mapped_state:", getattr(party, "state_name", "") if party else "")
    print("mapped_pincode:", getattr(party, "pincode", "") if party else "")
    # Additional explicit fields (task spec's BACKEND DEBUGGING section):
    # sandbox_response_present distinguishes "no attempt/HTTP failure" from a
    # response that was actually received and parsed; mapped_trade_name/
    # mapped_legal_name are the final party-master fields those two Sandbox
    # raw values (sandbox_tradeNam/sandbox_lgnm above) are supposed to become,
    # so a mismatch between the two pairs pinpoints a mapping bug rather than
    # an API/parsing one.
    print("sandbox_response_present:", bool(diag.get("lookup_attempted")) and diag.get("http_status") is not None)
    print("mapped_trade_name:", getattr(party, "trade_name", "") if party else "")
    print("mapped_legal_name:", getattr(party, "legal_name", "") if party else "")


def process_gstin(gstin, batch=None, force=False):
    gstin = normalize_gstin(gstin)
    status, party = _process_gstin(gstin, batch=batch, force=force)
    _log_party_lookup(gstin, status, party)
    return status, party


def _process_gstin(gstin, batch=None, force=False):
    gstin = normalize_gstin(gstin)
    if not valid_gstin(gstin): return "Invalid", None
    existing = GSTParty.objects.filter(gstin=gstin).first()
    # force refreshes the batch response, not a still-fresh external record.
    if party_is_fresh(existing):
        if existing.lookup_status == "Completed Manually": return "Completed Manually", existing
        if existing.lookup_status != "Existing" or existing.lookup_error:
            existing.lookup_status = "Existing"
            existing.lookup_error = ""
            existing.party_data_status = "Complete"
            existing.retry_not_before = None
            existing.save(update_fields=["lookup_status", "lookup_error", "party_data_status", "retry_not_before", "updated_at"])
        return "Existing", existing
    source = _source_party(batch, gstin)
    source_trade_name = str(source.get("trade_name") or "").strip()
    source_address = str(source.get("principal_place_of_business") or "").strip()
    trade_name = existing.trade_name if existing and has_usable_text(existing.trade_name) else ""
    trade_name = trade_name or (existing.legal_name if existing and has_usable_text(existing.legal_name) else "")
    address = existing.principal_place_of_business if existing and has_usable_text(existing.principal_place_of_business) else ""
    source_trade_name = source_trade_name if has_usable_text(source_trade_name) else ""
    source_address = source_address if has_usable_text(source_address) else ""
    trade_name = trade_name or source_trade_name
    address = address or source_address
    if trade_name and address and str(active_provider_name()).lower() != "sandbox":
        party, _ = GSTParty.objects.update_or_create(gstin=gstin, defaults={
            "trade_name": trade_name,
            "principal_place_of_business": address,
            "lookup_source": "IMPORT_SOURCE",
            "lookup_status": "Fetched",
            "lookup_error": "",
            "party_data_status": "Complete",
            "last_fetched_at": timezone.now(),
        })
        return "Fetched", party
    if not lookup_is_configured():
        sandbox = str(active_provider_name()).lower() == "sandbox"
        status = "Sandbox Not Configured" if sandbox else "Failed"
        if sandbox:
            return status, _fallback_party_identity(gstin, batch, status, "SANDBOX_NOT_CONFIGURED", existing)
        return status, _record_status(gstin, status, "SANDBOX_NOT_CONFIGURED" if sandbox else "GST lookup configuration unavailable", existing)
    try:
        source_kind, _details = GSTLookupService.lookup_cached(gstin, fallback_party_name=trade_name,
                                                                force_refresh=force,
                                                                company_gstin=batch.company_gstin if batch else "")
        party = GSTParty.objects.get(gstin=gstin)
        diag = _details.get("_diagnostics", {})
        party._lookup_diagnostics = diag
        party.lookup_diagnostics = diag
        party.save(update_fields=["lookup_diagnostics", "updated_at"])
        if party._lookup_diagnostics.get("unresolved_rate_limited"):
            party.retry_not_before = _retry_not_before(party._lookup_diagnostics.get("retry_after"))
            party.save(update_fields=["retry_not_before", "updated_at"])
            return "Rate Limited", party
        if source_kind == "existing": return "Existing", party
        if str(active_provider_name()).lower() == "sandbox" and source_kind not in {"sandbox", "existing"}:
            return "Sandbox Lookup Failed", _record_status(gstin, "Sandbox Lookup Failed", "UNEXPECTED_GST_PROVIDER", party)
        if party.lookup_status == "Fetched via Fallback" or "+" in source_kind: return "Fetched via Fallback", party
        return ("Incomplete" if party.party_data_status == "Incomplete" else "Fetched"), party
    except SandboxOTPRequired as exc:
        diagnostics = dict(getattr(exc, "lookup_diagnostics", None) or getattr(exc, "diagnostics", {}) or {})
        diagnostics.update(status="Sandbox Session Required", safe_reason="Sandbox Session Required",lookup_attempted=False, api_calls=0, normalized=False, sandbox_success=False)
        diagnostics = _coded(diagnostics, "SANDBOX_SESSION_REQUIRED")
        reason = "Sandbox taxpayer session is inactive. Authenticate the GST session and retry."
        return "Sandbox Session Required", _fallback_party_identity(gstin, batch, "Sandbox Session Required", reason, existing, diagnostics)
    except GSTLookupNotFoundError as exc:
        status = "Sandbox Lookup Failed" if str(active_provider_name()).lower() == "sandbox" else "Not Found"
        if status.startswith("Sandbox"):
            diagnostics = _coded(getattr(exc, "lookup_diagnostics", None), "SANDBOX_TAXPAYER_NOT_FOUND")
            return status, _fallback_party_identity(gstin, batch, status, "GSTIN not found", existing, diagnostics)
        return status, _record_status(gstin, status, "GSTIN not found", existing)
    except GSTLookupAuthenticationError as exc:
        logger.warning("GST taxpayer provider rejected credentials")
        status = "Sandbox Lookup Failed" if str(active_provider_name()).lower() == "sandbox" else "Failed"
        diagnostics = getattr(exc, "lookup_diagnostics", None)
        http_status = (diagnostics or {}).get("http_status")
        provider_message = str((diagnostics or {}).get("provider_message") or "").strip()
        session_expired = status.startswith("Sandbox") and diagnostics and diagnostics.get("taxpayer_session_active")
        # A bare 403 with no taxpayer session in play is Sandbox rejecting the
        # *account* (quota/subscription/permission) -- distinct from an
        # expired taxpayer session (above, a more specific match that still
        # wins) and from a genuine 401 credentials failure (below). Collapsing
        # it into "Sandbox authentication failed" hid the real reason (e.g.
        # Sandbox's own "Usage quota exhausted") behind a misleading claim.
        forbidden = not session_expired and status.startswith("Sandbox") and http_status == 403
        # SandboxGSTProvider.authenticate() already tells the difference between
        # a quota-exhausted account and any other 403 (its own exc.code, set at
        # the source where the provider's error message is read) -- prefer
        # that over re-deriving it here, so the two can never disagree.
        provider_code = str(getattr(exc, "code", "") or "")
        quota_exhausted = forbidden and provider_code == "SANDBOX_QUOTA_EXHAUSTED"
        status = "Sandbox Session Failed" if session_expired else status
        code = ("SANDBOX_QUOTA_EXHAUSTED" if quota_exhausted else
                "SANDBOX_FORBIDDEN" if forbidden else
                "SANDBOX_SESSION_EXPIRED" if session_expired else "SANDBOX_AUTH_FAILED")
        diagnostics = _coded(diagnostics, code)
        if status.startswith("Sandbox"):
            reason = (provider_message if forbidden and provider_message else
                      "Sandbox Session Failed" if status == "Sandbox Session Failed" else
                      "GST party lookup credentials require an update. Contact the administrator.")
            return status, _fallback_party_identity(gstin, batch, status, reason, existing, diagnostics)
        return status, _record_status(gstin, status, "Sandbox Session Failed" if status == "Sandbox Session Failed" else "Sandbox authentication failed" if status.startswith("Sandbox") else "Provider authentication failed", existing, diagnostics)
    except GSTLookupRateLimitError as exc:
        logger.warning("GST taxpayer provider rate limit reached")
        party = _record_status(gstin, "Rate Limited", "Provider rate limit", existing)
        provider = str(exc.provider or settings.GST_LOOKUP_PRIMARY_PROVIDER or settings.GST_LOOKUP_PROVIDER).lower()
        party._lookup_diagnostics = party.lookup_diagnostics = {
            f"{provider}_429_count": getattr(exc, "rate_count", 1),
            "retry_after_present": bool(exc.retry_after), "fallback_attempted_after_429": False,
            "retry_count": getattr(exc, "retry_count", 0), "unresolved_rate_limited": True,
            "api_calls": 1 + getattr(exc, "retry_count", 0), "lookup_error_code": "SANDBOX_RATE_LIMITED",
        }
        party.retry_not_before = _retry_not_before(exc.retry_after)
        party.save(update_fields=["retry_not_before", "lookup_diagnostics", "updated_at"])
        return "Rate Limited", party
    except GSTLookupTimeoutError as exc:
        logger.warning("GST taxpayer provider timed out for GSTIN %s", gstin)
        status = "Sandbox Lookup Failed" if str(active_provider_name()).lower() == "sandbox" else "Failed"
        reason = str(exc) or "Provider timeout"
        diagnostics = _coded(getattr(exc, "lookup_diagnostics", None), "SANDBOX_TIMEOUT")
        if status.startswith("Sandbox"):
            return status, _fallback_party_identity(gstin, batch, status, reason, existing, diagnostics)
        return status, _record_status(gstin, status, reason, existing, diagnostics)
    except GSTLookupProviderError as exc:
        # The exception message already carries the real, specific reason
        # (e.g. "Sandbox request failed (HTTP 500)", "Sandbox public GSTIN
        # payload is empty") -- surface it as-is instead of collapsing every
        # distinct provider failure into one generic, undiagnosable string.
        logger.warning("GST taxpayer provider returned an unexpected response for GSTIN %s: %s", gstin, exc)
        status = "Sandbox Lookup Failed" if str(active_provider_name()).lower() == "sandbox" else "Failed"
        raw_diagnostics = getattr(exc, "lookup_diagnostics", None) or {}
        http_status = raw_diagnostics.get("http_status")
        # A connection-level failure (DNS/refused/reset -- no HTTP response at
        # all) is a distinct, actionable diagnosis from a response that came
        # back malformed or unmapped; SandboxNetworkError is the only case
        # that reaches here with no real http_status, so check it explicitly
        # rather than lumping it into SANDBOX_INVALID_RESPONSE by default.
        code = ("SANDBOX_NETWORK_ERROR" if isinstance(exc, SandboxNetworkError) else
                "SANDBOX_PROVIDER_ERROR" if isinstance(http_status, int) and http_status >= 500 else
                f"SANDBOX_HTTP_{http_status}" if isinstance(http_status, int) and http_status >= 400 else
                "SANDBOX_INVALID_RESPONSE")
        diagnostics = _coded(raw_diagnostics, code)
        if status.startswith("Sandbox"):
            reason = str(exc) or "Invalid Sandbox response"
            return status, _fallback_party_identity(gstin, batch, status, reason, existing, diagnostics)
        # Non-Sandbox providers keep the original static reason -- views.py's
        # BatchPartiesView retry-eligibility check matches this exact string.
        return status, _record_status(gstin, status, "Malformed provider response", existing, diagnostics)
    except GSTLookupConfigurationError as exc:
        status = "Sandbox Lookup Failed" if str(active_provider_name()).lower() == "sandbox" else "Failed"
        diagnostics = _coded(getattr(exc, "lookup_diagnostics", None), "SANDBOX_NOT_CONFIGURED")
        if status.startswith("Sandbox"):
            return status, _fallback_party_identity(gstin, batch, status, str(exc), existing, diagnostics)
        return status, _record_status(gstin, status, str(exc) if status.startswith("Sandbox") else "GST lookup configuration unavailable", existing)
    except Exception as exc:
        logger.warning("GST taxpayer provider error for GSTIN %s error_code=%s: %s", gstin, type(exc).__name__, exc)
        if str(active_provider_name()).lower() == "sandbox":
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else f"Provider request failed ({type(exc).__name__})"
            diagnostics = _coded(None, "SANDBOX_UNEXPECTED_ERROR")
            return "Sandbox Lookup Failed", _fallback_party_identity(gstin, batch, "Sandbox Lookup Failed", reason, existing, diagnostics)
        # Non-Sandbox providers keep the original static reason -- views.py's
        # BatchPartiesView retry-eligibility check matches this exact string.
        return "Failed", _record_status(gstin, "Failed", "Provider request failed", existing)

def result_row(gstin, status, party=None, reason=""):
    # "Pending" means this GSTIN has never actually been looked up yet (no
    # GSTParty record, or one that was never queried) -- it is NOT a
    # configuration problem, and must never be reported as one merely
    # because no attempt has happened. A genuinely unconfigured provider is
    # reported through lookup_configured/configuration_error instead.
    reasons = {"Invalid": "Invalid GSTIN", "Not Found": "No taxpayer data returned","Rate Limited": "Provider rate limit", "Pending": "Sandbox taxpayer lookup has not been attempted yet.","Failed": "Processing failed", "Incomplete": "Required party details are incomplete"}
    eligibility = party_eligibility(party, gstin=gstin)
    sandbox = str(active_provider_name() or "").strip().lower() == "sandbox"
    controlled_failure = status in {"Invalid", "Incomplete", "Not Found", "Rate Limited", "Failed", "Sandbox Lookup Failed",
                                    "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}
    if not controlled_failure and not sandbox and eligibility["tally_ready"] and eligibility.get("name_source") == "GSTIN":
        status, reason = "GSTIN Fallback", "Lookup incomplete. GSTIN will be used as the Tally ledger name."
    elif not controlled_failure and not sandbox and eligibility["tally_ready"] and not party_has_address(party):
        status, reason = "Ready with Warning", "Principal Place of Business could not be fetched. Party can still be used for Tally import."
    elif not eligibility["tally_ready"] and status not in {"Invalid", "Not Applicable", "Pending", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}:
        status, reason = ("Sandbox Lookup Failed" if sandbox else "Incomplete"), eligibility.get("reason", reason)
    fallback_status = sandbox and status in {"Sandbox Lookup Failed", "Sandbox Not Configured", "Sandbox Session Required", "Sandbox Session Failed"}
    # A quota-exhausted account is not the same problem as an unreachable
    # provider or a bad GSTIN, and must never read like one -- the user needs
    # to know the fallback is a Sandbox billing/usage limit, not a defect in
    # their data, and that it will resolve itself (or via Retry) once the
    # quota is available again.
    early_diag = (getattr(party, "_lookup_diagnostics", None) or getattr(party, "lookup_diagnostics", None) or {}) if party else {}
    quota_exhausted_warning = fallback_status and early_diag.get("lookup_error_code") == "SANDBOX_QUOTA_EXHAUSTED"
    warning_reason = ("Sandbox API quota exhausted. Taxpayer enrichment is temporarily unavailable." if quota_exhausted_warning
else "Sandbox taxpayer details are unavailable." if fallback_status
else "Address unavailable" if sandbox and status in {"Fetched", "Existing"} and party and not party_has_address(party) else "")
    normalized = normalized_party_result(gstin, party, lookup_status=status)
    eligibility_name_source = eligibility.get("name_source", "")
    if fallback_status:
        normalized["name"] = ""
        normalized["name_source"] = "SANDBOX_LOOKUP_FAILED"
        eligibility.update(tally_ready=False, party_name="", name_source="SANDBOX_LOOKUP_FAILED",reason=eligibility.get("reason") or warning_reason,warning=warning_reason, warning_code="SANDBOX_PARTY_DETAILS_UNAVAILABLE",attention_required=True)
    elif normalized["name_source"] in {"SANDBOX_TRADE_NAME", "SANDBOX_LEGAL_NAME", "SOURCE", "GSTIN"}:
        eligibility["party_name"] = normalized["name"]
        eligibility["name_source"] = normalized["name_source"]
    elif eligibility_name_source:
        normalized["name_source"] = eligibility_name_source
    # Prefer the in-memory diagnostics from *this* request's own lookup attempt;
    # fall back to what was persisted on the row so a plain GET (a fresh
    # GSTParty query, no lookup run in this request) still reflects the real
    # last Sandbox attempt instead of blank/false defaults.
    party_diagnostics = (getattr(party, "_lookup_diagnostics", None) or getattr(party, "lookup_diagnostics", None) or {}) if party else {}
    # A field only genuinely came from Sandbox if this GSTParty's identity was
    # actually resolved by a completed Sandbox lookup (lookup_source ==
    # "sandbox") -- never guessed from a source/fallback-derived record, even
    # though that record can otherwise look "complete" (mirrors data_source's
    # own rule in normalized_party_result above).
    party_is_sandbox = bool(party) and str(getattr(party, "lookup_source", "") or "").strip().lower() == "sandbox"
    sandbox_trade_name = (party.trade_name or "") if party_is_sandbox and party and has_usable_text(party.trade_name) else ""
    sandbox_legal_name = (party.legal_name or "") if party_is_sandbox and party and has_usable_text(party.legal_name) else ""
    sandbox_address = ((party.principal_place_of_business or party.address or "") if party_is_sandbox and party else "")
    request_metadata = party_diagnostics.get("request_metadata") or {}
    # Compact, frontend-safe summary of the real Sandbox call for this row --
    # never the generic SANDBOX_PARTY_DETAILS_UNAVAILABLE warning alone, and
    # never any secret/token. None when this provider isn't Sandbox at all.
    sandbox_lookup = ({
        "attempted": bool(party_diagnostics.get("lookup_attempted")),
        "success": bool(normalized.get("party_details_complete")),
        "http_status": party_diagnostics.get("http_status"),
        "error_code": party_diagnostics.get("lookup_error_code") or "",
        "error_message": party_diagnostics.get("provider_message") or "",
        "response_body": party_diagnostics.get("raw_provider_response") or "",
        "endpoint": request_metadata.get("endpoint", ""),
        "auth_token_attached": bool(request_metadata.get("auth_token_attached")),
        "api_key_attached": bool(request_metadata.get("api_key_attached")),
        "taxpayer_session_attached": bool(request_metadata.get("taxpayer_session_attached")),
        # Explicit, individually-named diagnostic fields (never a secret/
        # token -- only booleans, codes, and already-public taxpayer fields).
        "sandbox_lookup_attempted": bool(party_diagnostics.get("lookup_attempted")),
        "sandbox_provider_configured": bool(party_diagnostics.get("provider_configured", party_diagnostics.get("sandbox_configured"))),
        "sandbox_endpoint": request_metadata.get("endpoint", ""),
        "sandbox_http_status": party_diagnostics.get("http_status"),
        "sandbox_success": bool(normalized.get("party_details_complete")),
        "sandbox_error_code": party_diagnostics.get("lookup_error_code") or "",
        "sandbox_error_message": party_diagnostics.get("provider_message") or "",
        "sandbox_response_received": bool(party_diagnostics.get("lookup_attempted")) and party_diagnostics.get("http_status") is not None,
        "sandbox_response_shape": party_diagnostics.get("response_shape") or "",
        "sandbox_auth_present": bool(request_metadata.get("auth_token_attached")),
        "sandbox_token_present": bool(request_metadata.get("auth_token_attached") or request_metadata.get("api_key_attached")),
        "sandbox_trade_name": sandbox_trade_name,
        "sandbox_legal_name": sandbox_legal_name,
        "sandbox_gstin_status": (party.registration_status if party_is_sandbox and party else "") or "",
        "sandbox_taxpayer_type": (party.taxpayer_type if party_is_sandbox and party else "") or "",
        "sandbox_address_present": bool(has_usable_text(sandbox_address)),
        "sandbox_state": (party.state_name if party_is_sandbox and party else "") or "",
        "sandbox_pincode": (party.pincode if party_is_sandbox and party else "") or "",
    } if sandbox else None)
    return {**eligibility, **normalized,
            "trade_name": party.trade_name if party else "",
            "legal_name": party.legal_name if party else "", "state": normalized["state"],
            "pincode": normalized["pincode"],
            "principal_place_of_business": party.principal_place_of_business if party else "",
            "status": status, "reason": reason or (party.lookup_error if party and party.lookup_error else reasons.get(status, "")),
            "warning_reason": warning_reason, "diagnostics": party_diagnostics,
            "sandbox_lookup": sandbox_lookup}
