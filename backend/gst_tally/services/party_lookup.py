import re
import logging
from datetime import timedelta
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from gst_tally.models import GSTParty
from gst_tally.tally.validators import state_name as state_name_for_gstin_prefix
from .gst_lookup.base import (GSTLookupAuthenticationError, GSTLookupConfigurationError,
                              GSTLookupNotFoundError, GSTLookupProviderError,
                              GSTLookupRateLimitError, GSTLookupTimeoutError, has_usable_text)
from .gst_lookup.service import GSTLookupService
from .gst_lookup.providers.sandbox import ACCESS_CACHE_KEY, SESSION_CACHE_KEY, SandboxOTPRequired
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
def party_eligibility(party, source_name="", gstin=""):
    """Authoritative eligibility. Presentation statuses are deliberately not inputs."""
    gstin = normalize_gstin(getattr(party, "gstin", "") or gstin)
    name = resolve_party_ledger_name(party, {"party_name": source_name}, gstin)
    if name == gstin:
        name = ""
    if not valid_gstin(gstin): return {"tally_ready": False, "reason": "Invalid GSTIN", "party_name": ""}
    fallback = not bool(name)
    sandbox = str(settings.GST_LOOKUP_PROVIDER or "").strip().lower() == "sandbox"
    if fallback and sandbox:
        # Sandbox taxpayer lookup is optional party ENRICHMENT (name/address),
        # never a voucher eligibility gate: once the GSTIN itself is valid,
        # GSTIN is a valid fallback ledger name/identity for Tally (state,
        # country and registration type are all separately derivable without
        # the lookup). A lookup failure -- for any reason, including OTP/auth
        # -- surfaces as a non-blocking warning instead of blocking import.
        authentication_blocked = party and party.lookup_status in {"OTP Required", "Sandbox Session Required"}
        reason = "OTP required" if party and party.lookup_status == "OTP Required" else "GST Sandbox authentication required" if authentication_blocked else "Sandbox taxpayer details are unavailable"
        return {"tally_ready": True, "party_name": gstin, "name_source": "Sandbox",
                "warning": reason, "warning_code": "SANDBOX_PARTY_DETAILS_UNAVAILABLE"}
    # The party's state is always derivable from its own (already-validated)
    # GSTIN prefix -- the same derivation `transaction_type` already relies on
    # elsewhere. Requiring the sandbox provider to have separately reported a
    # state field blanket-blocked every voucher for a party it otherwise
    # resolved fine, whenever that one field happened to come back blank.
    if sandbox and not (has_usable_text(getattr(party, "state_name", "")) or has_usable_text(state_name_for_gstin_prefix(gstin[:2]))):
        return {"tally_ready": False, "reason": "Sandbox taxpayer state is unavailable", "party_name": name, "name_source": "Sandbox"}
    result = {"tally_ready": True, "party_name": name or gstin, "name_source": "GSTIN" if fallback else "Taxpayer"}
    if fallback: result["warning"] = "GSTIN fallback ledger name"
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
def _record_status(gstin, status, reason, existing=None, diagnostics=None):
    party = existing
    if party:
        party.lookup_source = settings.GST_LOOKUP_PROVIDER or party.lookup_source
        party.lookup_status = status
        party.lookup_error = reason
        if not party_is_fresh(party): party.party_data_status = "Incomplete"
        party.save(update_fields=["lookup_source", "lookup_status", "lookup_error", "party_data_status", "updated_at"])
    else:
        party = GSTParty.objects.create(gstin=gstin, lookup_source=settings.GST_LOOKUP_PROVIDER,
                                        lookup_status=status, lookup_error=reason, party_data_status="Incomplete")
    if str(settings.GST_LOOKUP_PROVIDER or "").strip().lower() == "sandbox":
        party._lookup_diagnostics = diagnostics or {"gstin": gstin, "configured_provider": "sandbox", "provider_requested": "sandbox",
            "actual_provider_used": "sandbox", "provider_used": "sandbox", "cache_hit": False,
            "sandbox_access_token_valid": bool(cache.get(ACCESS_CACHE_KEY)),
            "taxpayer_session_active": bool(cache.get(SESSION_CACHE_KEY)), "lookup_attempted": True,
            "http_status": None, "sandbox_success": False, "normalized": False, "status": status,
            "warning": None, "safe_reason": reason}
    return party


def _source_party(batch, gstin):
    return (batch.source_parties or {}).get(gstin, {}) if batch else {}


def _source_party_name(source):
    return (resolve_party_ledger_name(source=source, gstin="") or "").strip()


def _fallback_party_identity(gstin, batch, status, reason, existing=None, diagnostics=None):
    source = _source_party(batch, gstin)
    name = _source_party_name(source) or gstin
    address = str(source.get("principal_place_of_business") or source.get("address") or "").strip()
    party, _ = GSTParty.objects.update_or_create(gstin=gstin, defaults={
        "trade_name": name,
        "legal_name": str(source.get("legal_name") or "").strip(),
        "principal_place_of_business": address,
        "address": address,
        "state_name": str(source.get("state") or source.get("state_name") or "").strip(),
        "pincode": str(source.get("pincode") or "").strip(),
        "taxpayer_type": str(source.get("registration_type") or source.get("taxpayer_type") or "").strip(),
        "lookup_source": "IMPORT_SOURCE" if name != gstin else "GSTIN",
        "lookup_status": status,
        "lookup_error": reason,
        "party_data_status": "Complete",
        "last_fetched_at": timezone.now(),
        "retry_not_before": None,
    })
    party._lookup_diagnostics = diagnostics or getattr(existing, "_lookup_diagnostics", {}) or {
        "gstin": gstin,
        "configured_provider": "sandbox",
        "provider_requested": "sandbox",
        "actual_provider_used": "sandbox",
        "provider_used": "sandbox",
        "cache_hit": False,
        "lookup_attempted": True,
        "sandbox_success": False,
        "normalized": False,
        "status": status,
        "warning": None,
        "safe_reason": reason,
    }
    return party


def normalized_party_result(gstin, party=None, source=None, lookup_status=""):
    source = source or {}
    sandbox_legal = party.legal_name if party and party.lookup_source == "sandbox" and has_usable_text(party.legal_name) else ""
    sandbox_trade = (party.trade_name if party and party.lookup_source == "sandbox" and
                     has_usable_text(party.trade_name) and party.trade_name != sandbox_legal else "")
    source_name = _source_party_name(source)
    name = sandbox_trade or sandbox_legal or source_name or (party.trade_name if party and has_usable_text(party.trade_name) else "") or gstin
    if sandbox_trade:
        name_source = "SANDBOX_TRADE_NAME"
    elif sandbox_legal:
        name_source = "SANDBOX_LEGAL_NAME"
    elif source_name or (party and party.lookup_source == "IMPORT_SOURCE"):
        name_source = "SOURCE"
    else:
        name_source = "GSTIN"
    address = ((party.principal_place_of_business if party else "") or (party.address if party else "") or
               source.get("principal_place_of_business") or source.get("address") or "")
    return {
        "gstin": gstin,
        "trade_name": party.trade_name if party else "",
        "legal_name": party.legal_name if party else "",
        "name": name,
        "address": address,
        "state": (party.state_name if party else "") or source.get("state") or source.get("state_name") or state_name_for_gstin_prefix(gstin[:2]),
        "pincode": (party.pincode if party else "") or source.get("pincode") or "",
        "country": "India" if valid_gstin(gstin) else "",
        "registration_type": (party.taxpayer_type if party else "") or source.get("registration_type") or source.get("taxpayer_type") or "",
        "name_source": name_source,
        "lookup_status": lookup_status or (party.lookup_status if party else ""),
    }


def process_gstin(gstin, batch=None, force=False):
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
    if trade_name and address and str(settings.GST_LOOKUP_PROVIDER).lower() != "sandbox":
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
        sandbox = str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox"
        status = "Sandbox Not Configured" if sandbox else "Failed"
        if sandbox:
            return status, _fallback_party_identity(gstin, batch, status, "SANDBOX_NOT_CONFIGURED", existing)
        return status, _record_status(gstin, status, "SANDBOX_NOT_CONFIGURED" if sandbox else "GST lookup configuration unavailable", existing)
    try:
        source_kind, _details = GSTLookupService.lookup_cached(gstin, fallback_party_name=trade_name,
                                                                company_gstin=batch.company_gstin if batch else "")
        party = GSTParty.objects.get(gstin=gstin)
        party._lookup_diagnostics = _details.get("_diagnostics", {})
        normalized = normalized_party_result(gstin, party, _source_party(batch, gstin), lookup_status=party.lookup_status)
        print("=== PARTY LOOKUP ===")
        print("GSTIN:", gstin)
        print("RAW PROVIDER RESPONSE:")
        print(party._lookup_diagnostics.get("raw_provider_response", ""))
        print("NORMALIZED:")
        print("trade_name:", normalized["trade_name"])
        print("legal_name:", normalized["legal_name"])
        print("party_name:", normalized["name"])
        print("principal_address:", normalized["address"])
        print("state:", normalized["state"])
        print("pincode:", normalized["pincode"])
        print("country:", normalized["country"])
        print("registration_type:", normalized["registration_type"])
        print("gst_status:", party.registration_status)
        print("name_source:", normalized["name_source"])
        if party._lookup_diagnostics.get("unresolved_rate_limited"):
            party.retry_not_before = _retry_not_before(party._lookup_diagnostics.get("retry_after"))
            party.save(update_fields=["retry_not_before", "updated_at"])
            return "Rate Limited", party
        if source_kind == "existing": return "Existing", party
        if str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox" and source_kind not in {"sandbox", "existing"}:
            return "Sandbox Lookup Failed", _record_status(gstin, "Sandbox Lookup Failed", "UNEXPECTED_GST_PROVIDER", party)
        if party.lookup_status == "Fetched via Fallback" or "+" in source_kind: return "Fetched via Fallback", party
        return ("Incomplete" if party.party_data_status == "Incomplete" else "Fetched"), party
    except SandboxOTPRequired as exc:
        diagnostics = dict(getattr(exc, "lookup_diagnostics", None) or getattr(exc, "diagnostics", {}) or {})
        diagnostics.update(status="Sandbox Session Required", safe_reason="Sandbox Session Required",
                           lookup_attempted=False, api_calls=0, normalized=False, sandbox_success=False)
        reason = "Sandbox taxpayer session is inactive. Authenticate the GST session and retry."
        return "Sandbox Session Required", _fallback_party_identity(gstin, batch, "Sandbox Session Required", reason, existing, diagnostics)
    except GSTLookupNotFoundError as exc:
        status = "Sandbox Lookup Failed" if str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox" else "Not Found"
        if status.startswith("Sandbox"):
            return status, _fallback_party_identity(gstin, batch, status, "GSTIN not found", existing, getattr(exc, "lookup_diagnostics", None))
        return status, _record_status(gstin, status, "GSTIN not found", existing)
    except GSTLookupAuthenticationError as exc:
        logger.warning("GST taxpayer provider rejected credentials")
        status = "Sandbox Lookup Failed" if str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox" else "Failed"
        diagnostics = getattr(exc, "lookup_diagnostics", None)
        status = "Sandbox Session Failed" if status.startswith("Sandbox") and diagnostics and diagnostics.get("taxpayer_session_active") else status
        if status.startswith("Sandbox"):
            reason = "Sandbox Session Failed" if status == "Sandbox Session Failed" else "Sandbox authentication failed"
            return status, _fallback_party_identity(gstin, batch, status, reason, existing, diagnostics)
        return status, _record_status(gstin, status, "Sandbox Session Failed" if status == "Sandbox Session Failed" else "Sandbox authentication failed" if status.startswith("Sandbox") else "Provider authentication failed", existing, diagnostics)
    except GSTLookupRateLimitError as exc:
        logger.warning("GST taxpayer provider rate limit reached")
        party = _record_status(gstin, "Rate Limited", "Provider rate limit", existing)
        party.retry_not_before = _retry_not_before(exc.retry_after)
        party.save(update_fields=["retry_not_before", "updated_at"])
        provider = str(exc.provider or settings.GST_LOOKUP_PRIMARY_PROVIDER or settings.GST_LOOKUP_PROVIDER).lower()
        party._lookup_diagnostics = {
            f"{provider}_429_count": getattr(exc, "rate_count", 1),
            "retry_after_present": bool(exc.retry_after), "fallback_attempted_after_429": False,
            "retry_count": getattr(exc, "retry_count", 0), "unresolved_rate_limited": True,
            "api_calls": 1 + getattr(exc, "retry_count", 0),
        }
        return "Rate Limited", party
    except GSTLookupTimeoutError as exc:
        logger.warning("GST taxpayer provider timed out for GSTIN %s", gstin)
        status = "Sandbox Lookup Failed" if str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox" else "Failed"
        if status.startswith("Sandbox"):
            return status, _fallback_party_identity(gstin, batch, status, "Provider timeout", existing, getattr(exc, "lookup_diagnostics", None))
        return status, _record_status(gstin, status, "Provider timeout", existing, getattr(exc, "lookup_diagnostics", None))
    except GSTLookupProviderError as exc:
        logger.warning("GST taxpayer provider returned an unexpected response for GSTIN %s", gstin)
        status = "Sandbox Lookup Failed" if str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox" else "Failed"
        if status.startswith("Sandbox"):
            return status, _fallback_party_identity(gstin, batch, status, "Invalid Sandbox response", existing, getattr(exc, "lookup_diagnostics", None))
        return status, _record_status(gstin, status, "Invalid Sandbox response" if status.startswith("Sandbox") else "Malformed provider response", existing, getattr(exc, "lookup_diagnostics", None))
    except GSTLookupConfigurationError as exc:
        status = "Sandbox Lookup Failed" if str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox" else "Failed"
        if status.startswith("Sandbox"):
            return status, _fallback_party_identity(gstin, batch, status, str(exc), existing, getattr(exc, "lookup_diagnostics", None))
        return status, _record_status(gstin, status, str(exc) if status.startswith("Sandbox") else "GST lookup configuration unavailable", existing)
    except Exception as exc:
        logger.warning("GST taxpayer provider error for GSTIN %s error_code=%s", gstin, type(exc).__name__)
        if str(settings.GST_LOOKUP_PROVIDER).lower() == "sandbox":
            return "Sandbox Lookup Failed", _fallback_party_identity(gstin, batch, "Sandbox Lookup Failed", "Provider request failed", existing)
        return "Failed", _record_status(gstin, "Failed", "Provider request failed", existing)

def result_row(gstin, status, party=None, reason=""):
    # "Pending" means this GSTIN has never actually been looked up yet (no
    # GSTParty record, or one that was never queried) -- it is NOT a
    # configuration problem, and must never be reported as one merely
    # because no attempt has happened. A genuinely unconfigured provider is
    # reported through lookup_configured/configuration_error instead.
    reasons = {"Invalid": "Invalid GSTIN", "Not Found": "No taxpayer data returned",
               "Rate Limited": "Provider rate limit", "Pending": "Sandbox taxpayer lookup has not been attempted yet.",
               "Failed": "Processing failed", "Incomplete": "Required party details are incomplete"}
    eligibility = party_eligibility(party, gstin=gstin)
    sandbox = str(settings.GST_LOOKUP_PROVIDER or "").strip().lower() == "sandbox"
    controlled_failure = status in {"Invalid", "Incomplete", "Not Found", "Rate Limited", "Failed", "Sandbox Lookup Failed",
                                    "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}
    if not controlled_failure and not sandbox and eligibility["tally_ready"] and eligibility.get("name_source") == "GSTIN":
        status, reason = "GSTIN Fallback", "Lookup incomplete. GSTIN will be used as the Tally ledger name."
    elif not controlled_failure and not sandbox and eligibility["tally_ready"] and not party_has_address(party):
        status, reason = "Ready with Warning", "Principal Place of Business could not be fetched. Party can still be used for Tally import."
    elif not eligibility["tally_ready"] and status not in {"Invalid", "Not Applicable", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}:
        status, reason = ("Sandbox Lookup Failed" if sandbox else "Incomplete"), eligibility.get("reason", reason)
    fallback_status = sandbox and status in {"Sandbox Lookup Failed", "Sandbox Not Configured", "Sandbox Session Required", "Sandbox Session Failed"}
    warning_reason = ("Sandbox party enrichment unavailable; source/GSTIN fallback used." if fallback_status
                      else "Address unavailable" if sandbox and status in {"Fetched", "Existing"} and party and not party_has_address(party) else "")
    normalized = normalized_party_result(gstin, party, lookup_status=status)
    eligibility_name_source = eligibility.get("name_source", "")
    if fallback_status:
        eligibility.update(tally_ready=True, party_name=normalized["name"], name_source=normalized["name_source"],
                           warning=warning_reason, warning_code="SANDBOX_PARTY_ENRICHMENT_UNAVAILABLE")
    elif normalized["name_source"] in {"SANDBOX_TRADE_NAME", "SANDBOX_LEGAL_NAME", "SOURCE", "GSTIN"}:
        eligibility["party_name"] = normalized["name"]
        eligibility["name_source"] = normalized["name_source"]
    elif eligibility_name_source:
        normalized["name_source"] = eligibility_name_source
    return {**eligibility, **normalized,
            "trade_name": party.trade_name if party else "",
            "legal_name": party.legal_name if party else "", "state": normalized["state"],
            "pincode": normalized["pincode"],
            "principal_place_of_business": party.principal_place_of_business if party else "",
            "status": status, "reason": reason or (party.lookup_error if party and party.lookup_error else reasons.get(status, "")),
            "warning_reason": warning_reason, "diagnostics": getattr(party, "_lookup_diagnostics", {}) if party else {}}
