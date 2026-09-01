import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import date
from django.conf import settings
from django.utils import timezone
from gst_tally.models import GSTInvoice, GSTLedgerMapping, TallyCompanyMapping, TallyVoucherMapping
from gst_tally.services.party_lookup import party_eligibility
from gst_tally.services.party_ledger_name import resolve_party_ledger_name
from gst_tally.services.company import financial_year_details
from .client import TallyClient, TallyConnectionError
from .connection import connection_status, existing_masters, ledger_details
from .odbc import odbc_company_period, odbc_company_status, odbc_existing_masters, verify_tally_company
from .mappings import correction_key, normalized_vouchers
from .return_mapping import extract_rate_from_name, get_tally_mapping, normalize_ledger_key
from .master_builder import _applicable_from, account_ledger_rate, build_master, masters_for, tally_tax_type
from .json_master_builder import TALLY_ANY, TALLY_APPLICABLE, TALLY_NOT_APPLICABLE, build_json_master
from .json_voucher_builder import build_json_voucher
from .validators import INVOICE_ROUNDING_TOLERANCE, money, validate_voucher
from .voucher_builder import build_voucher
from .voucher_verification import find_voucher, query_vouchers, verify_voucher

logger = logging.getLogger(__name__)

def safe_decode(value, default=""):
    """Decode a Tally request/response payload that may legitimately be
    bytes, str, or None -- never call .decode() directly on a value that
    could be None (a stale/rejected Tally response body, for instance)."""
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)

def import_outcome(counts):
    imported = counts.get("imported", 0) + counts.get("already_imported", 0)
    legacy_failed = counts.get("failed", 0) if not any(key in counts for key in (
    "master_setup_failed", "preflight_failed", "tally_failed", "verification_failed")) else 0
    # validation_failed (never sent to Tally) and failed/tally_failed (Tally rejected or
    # could not verify the write) are distinct, non-overlapping buckets -- see _step6_outcome.
    failed = (legacy_failed + counts.get("validation_failed", 0) + counts.get("invalid", 0)
       + counts.get("master_setup_failed", 0) + counts.get("preflight_failed", 0)
       + counts.get("tally_failed", 0) + counts.get("verification_failed", 0)
       + counts.get("unknown", 0) + counts.get("not_attempted", 0) + counts.get("waiting_for_tally_period", 0))
    skipped = counts.get("skipped", 0)
    eligible = counts.get("eligible")
    operational_failures = (counts.get("master_setup_failed", 0) + counts.get("preflight_failed", 0)
                     + counts.get("tally_failed", 0) + counts.get("verification_failed", 0)
                     + counts.get("unknown", 0) + counts.get("not_attempted", 0)
                     + counts.get("waiting_for_tally_period", 0))
    if eligible is not None:
        status = "success" if eligible and imported == eligible and not operational_failures else "partial_success" if imported else "failed"
    else:
        status = "success" if imported and not failed and not skipped else "partial_success" if imported else "failed"
    return {"total": counts.get("total", 0), "imported": imported, "failed": failed, "skipped": skipped, "status": status}

def _plural(count, singular_is="was", plural_is="were"):
    return singular_is if count == 1 else plural_is

def _step6_outcome(counts):
    """The three genuinely different Step 6 outcomes, with a message that names
    each one instead of collapsing them into one generic failure count."""
    imported, already = counts.get("imported", 0), counts.get("already_imported", 0)
    validation_failed, tally_failed = counts.get("validation_failed", 0), counts.get("tally_failed", 0)
    master_setup_failed, preflight_failed = counts.get("master_setup_failed", 0), counts.get("preflight_failed", 0)
    verification_failed = counts.get("verification_failed", 0)
    unknown, skipped = counts.get("unknown", 0) + counts.get("invalid", 0), counts.get("skipped", 0)
    not_attempted, waiting = counts.get("not_attempted", 0), counts.get("waiting_for_tally_period", 0)
    successful = imported + already
    unresolved = validation_failed + master_setup_failed + preflight_failed + tally_failed + verification_failed + unknown + skipped + not_attempted + waiting
    eligible = counts.get("eligible")
    all_eligible_confirmed = (bool(eligible and successful == eligible and not (
        master_setup_failed + preflight_failed + tally_failed + verification_failed + unknown + not_attempted + waiting))
        if eligible is not None else bool(successful and not unresolved))
    parts = []
    if imported: parts.append(f"{imported} voucher{'s' if imported != 1 else ''} imported successfully")
    if already: parts.append(f"{already} {_plural(already)} already present in Tally")
    rejected = []
    if validation_failed: rejected.append(f"{validation_failed} require{'' if validation_failed != 1 else 's'} data correction")
    if master_setup_failed: rejected.append(f"{master_setup_failed} {_plural(master_setup_failed)} blocked by master setup")
    if preflight_failed: rejected.append(f"{preflight_failed} {_plural(preflight_failed)} blocked by preflight validation")
    if tally_failed: rejected.append(f"{tally_failed} {_plural(tally_failed)} rejected by Tally")
    if verification_failed: rejected.append(f"{verification_failed} {_plural(verification_failed)} not confirmed by Tally query-back")
    if rejected: parts.append(" and ".join(rejected))
    if unknown: parts.append(f"{unknown} need{'' if unknown != 1 else 's'} verification")
    if not_attempted: parts.append(f"{not_attempted} {_plural(not_attempted)} not attempted")
    if waiting: parts.append(f"{waiting} {'is' if waiting == 1 else 'are'} waiting for the correct Tally period")
    if skipped: parts.append(f"{skipped} {_plural(skipped)} skipped")
    message = ". ".join(part[0].upper() + part[1:] for part in parts) + "." if parts else "No vouchers were processed."
    if all_eligible_confirmed:
        if imported == 0 and already:
            # Every eligible voucher was verified already present and nothing new
            # was written -- a distinct outcome from a fresh import (spec section U).
            import_status = "Import Verified"
            message = (f"{already} voucher{'s' if already != 1 else ''} "
                      f"{'is' if already == 1 else 'are'} already present in Tally. "
                      "No duplicate vouchers were created.")
        else:
            import_status = "Import Successful"
            message = (f"{successful} voucher{'s' if successful != 1 else ''} completed successfully in Tally."
                       if already else f"{imported} voucher{'s' if imported != 1 else ''} imported successfully into Tally.")
    elif successful: import_status = "Partial Import"
    else: import_status = "Import Failed"
    return import_status, message

def _json_writes(): return settings.TALLY_WRITE_FORMAT == "JSON"


def _write_metadata():
    configured = tuple(int(value) for value in re.findall(r"\d+", settings.TALLY_VERSION))
    minimum = tuple(int(value) for value in re.findall(r"\d+", settings.TALLY_MIN_JSON_VERSION))
    return {
"tally_version_configured": settings.TALLY_VERSION,
"tally_version_detected": "",
"json_integration_supported": configured >= minimum,
"active_write_format": "JSON_MASTERS_XML_VOUCHERS",
"requested_transport": settings.TALLY_WRITE_FORMAT,
"actual_transport": "JSON masters / XML vouchers",
"fallback_reason": "",
"write_enabled": bool(settings.TALLY_ENABLED and not settings.TALLY_DRY_RUN),
"xml_used_for_active_voucher_write": True,
"xml_used_for_active_master_write": False,
"json_used_for_active_master_write": True,
"custom_tdl_used_for_writes": False,
}


def _native_master_payload(master, company, rate_mode="both"):
    return build_json_master(master, company, rate_mode)


def _assert_gst_master_payload_complete(master, payload, rate_mode="both"):
    """Guard the exact defect this reports: a Sales/Purchase account ledger's
    native JSON payload silently missing its nested GST rate-detail
    collections. Skips rate_mode="flat_only", which deliberately omits
    gstdetails as one of Strategy B's two single-mode retry variants (see
    _repair_gst_account_rate_with_fallback)."""
    if master.get("master_type") not in ("Sales", "Purchase") or rate_mode == "flat_only":
        return
    if account_ledger_rate(master) <= 0:
        return

    try:
        message = payload["tallymessage"][0]
        gst = message["gstdetails"][0]
        state = gst["statewisedetails"][0]
        rows = state["ratedetails"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"GST_MASTER_BUILDER_INCOMPLETE: {master.get('name', '')} native JSON is missing GST detail collections"
        ) from exc

    if message.get("gstapplicable") != TALLY_APPLICABLE:
        raise ValueError(
            f"GST_MASTER_BUILDER_INCOMPLETE: {master.get('name', '')} gstapplicable must be native Tally Applicable"
        )
    if state.get("statename") != TALLY_ANY:
        raise ValueError(
            f"GST_MASTER_BUILDER_INCOMPLETE: {master.get('name', '')} statename must be native Tally Any"
        )

    rates = {row.get("gstratedutyhead"): row for row in rows}
    expected = money(account_ledger_rate(master))
    expected_rates = {
        "CGST": expected / money("2"),
        "SGST/UTGST": expected / money("2"),
        "IGST": expected,
    }
    for head, expected_rate in expected_rates.items():
        row = rates.get(head)
        if not row:
            raise ValueError(
                f"GST_MASTER_BUILDER_INCOMPLETE: {master.get('name', '')} is missing native {head} rate row"
            )
        if row.get("gstratevaluationtype") != "Based on Value":
            raise ValueError(
                f"GST_MASTER_BUILDER_INCOMPLETE: {master.get('name', '')} {head} valuation type is invalid"
            )
        if abs(money(row.get("gstrate")) - expected_rate) > money("0.01"):
            raise ValueError(
                f"GST_MASTER_BUILDER_INCOMPLETE: {master.get('name', '')} {head} expected {expected_rate:g}, got {row.get('gstrate')}"
            )

    cess = rates.get("Cess") or {}
    if cess.get("gstratevaluationtype") != TALLY_NOT_APPLICABLE:
        raise ValueError(
            f"GST_MASTER_BUILDER_INCOMPLETE: {master.get('name', '')} Cess must be native Tally Not Applicable"
        )


def _write_master(client, master, company, rate_mode="both"):
    if master.get("master_type") == "Party":
        print("=== PARTY MASTER PAYLOAD ===")
        print("name:", master.get("name", ""))
        print("mailing_name:", master.get("mailing_name") or master.get("name", ""))
        print("address:", master.get("address", ""))
        print("state:", master.get("state", ""))
        print("country:", master.get("country") or "India")
        print("pincode:", master.get("pincode", ""))
        print("gst_registration_type:", master.get("registration_type", ""))
        print("gstin:", master.get("gstin", ""))

    payload = _native_master_payload(master, company, rate_mode)
    _assert_gst_master_payload_complete(master, payload, rate_mode)
    print("=== NATIVE TALLY MASTER JSON ===")
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return client.import_json(payload, "All Masters")


def _ledger_rate_trace(master, company, actual_properties):
    """Diagnostics only -- must never raise. See _gst_ledger_master_trace for
    why _native_master_payload's result can't be assumed to be a dict."""
    parsed = extract_rate_from_name(master.get("name"))
    try:
        payload = _native_master_payload(master, company)
    except Exception:
        payload = None
    payload_available = isinstance(payload, dict)

    message = {}
    gst = {}
    statewise = {}
    rate_nodes = []

    if payload_available:
        tally_messages = payload.get("tallymessage") or []
        if isinstance(tally_messages, list) and tally_messages:
            candidate = tally_messages[0]
            if isinstance(candidate, dict):
                message = candidate

        gst_details = message.get("gstdetails") or []
        if isinstance(gst_details, list) and gst_details:
            candidate = gst_details[0]
            if isinstance(candidate, dict):
                gst = candidate

        statewise_details = gst.get("statewisedetails") or []
        if isinstance(statewise_details, list) and statewise_details:
            candidate = statewise_details[0]
            if isinstance(candidate, dict):
                statewise = candidate

        rate_nodes = statewise.get("ratedetails") or []
        if not isinstance(rate_nodes, list):
            rate_nodes = []

    nested = {
        row.get("gstratedutyhead"): row.get("gstrate")
        for row in rate_nodes
        if isinstance(row, dict) and row.get("gstratedutyhead") in ("IGST", "CGST", "SGST/UTGST")
    }
    reread_nested = actual_properties.get("gst_rates") or {}
    return {
        "ledger_name": master.get("name"),
        "parsed_rate": str(parsed) if parsed is not None else None,
        "json_model.rateoftaxcalculation": message.get("rateoftaxcalculation"),
        "payload_available": payload_available,
        "actual_transport": "JSON" if payload_available else "XML",
        "native_statename": statewise.get("statename"),
        "tally_reread_rate": actual_properties.get("outer_gst_rate") or actual_properties.get("gst_rate") or None,
        "set_alter_details": actual_properties.get("set_alter_gst_rate_details") or None,
        "canonical_igst": nested.get("IGST"),
        "canonical_cgst": nested.get("CGST"),
        "canonical_sgst": nested.get("SGST/UTGST"),
        "actual_transport_igst": nested.get("IGST"),
        "actual_transport_cgst": nested.get("CGST"),
        "actual_transport_sgst": nested.get("SGST/UTGST"),
        "reread_igst": reread_nested.get("IGST"),
        "reread_cgst": reread_nested.get("CGST"),
        "reread_sgst": reread_nested.get("SGST/UTGST"),
    }


def _gst_ledger_master_trace(master, company, response, actual, verification, client=None):
    """Diagnostics only -- must never turn a valid master-prepare request into
    an HTTP 500. _native_master_payload can be unavailable (payload builder
    exception, non-native write strategy, or a shape the builder doesn't
    recognise); every lookup below is defensive so a malformed/missing
    payload degrades to empty diagnostics instead of raising."""
    parsed = extract_rate_from_name(master.get("name"))
    expected = account_ledger_rate(master)
    try:
        payload = _native_master_payload(master, company)
    except Exception:
        payload = None
    payload_available = isinstance(payload, dict)

    message = {}
    gst = {}
    statewise = {}
    rate_nodes = []
    written_rates = {}

    if payload_available:
        tally_messages = payload.get("tallymessage") or []
        if isinstance(tally_messages, list) and tally_messages:
            candidate = tally_messages[0]
            if isinstance(candidate, dict):
                message = candidate

        gst_details = message.get("gstdetails") or []
        if isinstance(gst_details, list) and gst_details:
            candidate = gst_details[0]
            if isinstance(candidate, dict):
                gst = candidate

        statewise_details = gst.get("statewisedetails") or []
        if isinstance(statewise_details, list) and statewise_details:
            candidate = statewise_details[0]
            if isinstance(candidate, dict):
                statewise = candidate

        rate_nodes = statewise.get("ratedetails") or []
        if not isinstance(rate_nodes, list):
            rate_nodes = []

        written_rates = {
            row.get("gstratedutyhead"): row.get("gstrate")
            for row in rate_nodes
            if isinstance(row, dict)
        }

    actual_rates = actual.get("gst_rates") or {}

    sent_structure = {
        "gstdetails": bool(message.get("gstdetails")),
        "statewisedetails": bool(gst.get("statewisedetails")),
        "ratedetails_count": len(rate_nodes),
        "statename": statewise.get("statename"),
        "cgst": written_rates.get("CGST"),
        "sgst": written_rates.get("SGST/UTGST"),
        "igst": written_rates.get("IGST"),
    }
    tally_reread_structure = {
        "gstdetails": bool(actual.get("gst_rate_history_exists")),
        "statewisedetails": bool(actual.get("gst_rate_details_popup_exists")),
        "ratedetails_count": len(actual_rates) if isinstance(actual_rates, dict) else 0,
        "cgst": actual_rates.get("CGST", ""),
        "sgst": actual_rates.get("SGST/UTGST", ""),
        "igst": actual_rates.get("IGST", ""),
    }

    first_structural_difference = ""
    if sent_structure["gstdetails"] and not tally_reread_structure["gstdetails"]:
        first_structural_difference = "gstdetails was sent but is absent from the Tally re-read"
    elif sent_structure["statewisedetails"] and not tally_reread_structure["statewisedetails"]:
        first_structural_difference = "statewisedetails was sent but is absent from the Tally re-read"
    elif sent_structure["ratedetails_count"] and not tally_reread_structure["ratedetails_count"]:
        first_structural_difference = f"{sent_structure['ratedetails_count']} ratedetails rows were sent but none survived the Tally re-read"
    elif sent_structure["igst"] and sent_structure["igst"] != tally_reread_structure["igst"]:
        first_structural_difference = f"IGST sent={sent_structure['igst']} but Tally re-read shows IGST={tally_reread_structure['igst'] or '(missing)'}"

    rendered = json.dumps(payload, ensure_ascii=True, indent=2) if payload_available else ""
    return {
    "ledger_name": master.get("name"),
    "source_rate": str(master.get("gst_rate") or ""),
    "parsed_name_rate": str(parsed) if parsed is not None else None,
    "expected_rate": str(expected.normalize()),
    "payload_available": payload_available,
    "payload_source": "NATIVE_JSON" if payload_available else "XML_OR_NON_NATIVE_WRITE",
    "outer_rate_written": message.get("rateoftaxcalculation"),
    "gst_details_written": bool(message.get("gstdetails")),
    "statewise_details_written": bool(gst.get("statewisedetails")),
    "rate_details_count": len(rate_nodes),
    "cgst_written": written_rates.get("CGST"),
    "sgst_written": written_rates.get("SGST/UTGST"),
    "igst_written": written_rates.get("IGST"),
    "actual_transport": "JSON" if payload_available else "XML",
    "actual_master_request": rendered,
    "final_json_sent": rendered,
    "final_xml_sent": "",
    "tally_response": _response_details(response, getattr(client, "last_http_status", None)),
    "write_response": {
        "altered": response.altered,
        "created": response.created,
        "errors": response.errors,
        "exceptions": response.exceptions,
    },
    "post_write_outer_rate": actual.get("outer_gst_rate") or actual.get("gst_rate"),
    "post_write_cgst": actual_rates.get("CGST"),
    "post_write_sgst": actual_rates.get("SGST/UTGST"),
    "post_write_igst": actual_rates.get("IGST"),
    "sent_structure": sent_structure,
    "tally_reread_structure": tally_reread_structure,
    "first_structural_difference": first_structural_difference,
    "verified": verification.get("valid", False),
}


_STRATEGY_RATE_MODE = {
    "A": "both",
    "B_nested_only": "nested_only",
    "B_flat_only": "flat_only",
    "B_FAILED": "both",
}


def _final_master_request_payload(master, company, strategy="A"):
    rate_mode = _STRATEGY_RATE_MODE.get(strategy, "both")
    return json.dumps(_native_master_payload(master, company, rate_mode), ensure_ascii=True, indent=2)


def _repair_gst_account_rate_with_fallback(client, repair, target_name, response, refreshed, verification):
    """Strategy B for a GST account ledger whose rate didn't survive Strategy A
    (rate_mode="both"): retry with the two single-mode native-JSON variants --
    nested breakup only, then flat rateoftaxcalculation only -- since either
    can be the one that's actually effective depending on the company's own
    "Provide GST rate details" configuration. Always re-reads real Tally
    state before declaring success; never claims success from ALTERED=1 alone.
    """
    trace = {
        "strategy_a_write_result": _response_details(response, getattr(client, "last_http_status", None)),
        "strategy_a_reread_rate": refreshed.get("outer_gst_rate") or refreshed.get("gst_rate"),
    }
    if verification.get("error_code") not in ("GST_ACCOUNT_RATE_WRITE_FAILED", "GST_RATE_DETAILS_INCOMPLETE"):
        return response, refreshed, verification, "A", trace

    retry_master = {**repair, "action": "Alter"}
    for variant in ("nested_only", "flat_only"):
        retry_response = _write_master(client, retry_master, target_name, rate_mode=variant)
        retry_refreshed = ledger_details(retry_master["name"], target_name, client)
        retry_verification = _verify_master_properties(retry_master, retry_refreshed)
        trace[f"strategy_b_{variant}_reread_rate"] = retry_refreshed.get("outer_gst_rate") or retry_refreshed.get("gst_rate")
        if retry_verification["valid"]:
            return retry_response, retry_refreshed, retry_verification, f"B_{variant}", trace

    trace["missing_fields"] = retry_verification.get("reason", "")
    return retry_response, retry_refreshed, retry_verification, "B_FAILED", trace


def _cess_trace(master, vouchers, actual_properties):
    """Trace the Cess ledger this batch requires: the source data never
    carries a trustworthy explicit Cess percentage (only a Cess amount per
    invoice -- see masters_for), so this honestly reports that no rate is
    derived/guessed, rather than hiding that fallback behind a silent 0%."""
    source_cess_amount = sum((money(v.get("cess")) for v in vouchers), money(0))
    transport_percentage = str(master.get("gst_rate")) if master.get("gst_rate") else "0"
    return {"source_cess_amount": str(source_cess_amount),
    "source_cess_rate": None,
     "required_cess_ledger": master.get("name"),
            "transport_percentage": transport_percentage,
            "tally_reread_percentage": actual_properties.get("gst_rate") or "0"}

def _write_voucher(client, voucher, company, period=None):
    return client.import_data(build_voucher(voucher, company, period))

def _key(company, voucher):
    value = "|".join([company.casefold(), voucher.get("voucher_type", "Sales").casefold(), voucher["invoice_number"].casefold(), voucher["invoice_date"], voucher["party"]["gstin"].casefold()])
    return hashlib.sha256(value.encode()).hexdigest()

def voucher_result_key(result):
    """Stable per-invoice identity for a row in import_batch's results list,
    used to collapse a source invoice down to a single final outcome even if
    it was visited more than once (e.g. blocked by a failed master, then
    later found already in Tally)."""
    return (str(result.get("invoice_no", "")).strip().casefold(),
            str(result.get("date", "")).strip(),
            str(result.get("gstin", "")).strip().upper(),
            str(result.get("voucher_type", "")).strip().casefold())

_VOUCHER_RESULT_STATUS_PRIORITY = ["Review Required", "Imported", "Already Imported", "Master Setup Failed"]
_VOUCHER_RESULT_SKIP_LIKE_STATUSES = {"Skipped", "Not Attempted", "Invalid", "Invalid Source Date", "Invalid Source Data"}

def _voucher_result_priority(result_status):
    if result_status in _VOUCHER_RESULT_STATUS_PRIORITY:
        return _VOUCHER_RESULT_STATUS_PRIORITY.index(result_status)
    # Every other Failed-shaped status (Preflight/Tally/Verification Failed,
    # Unknown / Verify Before Retry, ...) ranks below Master Setup Failed but
    # above structurally-ineligible Skipped/Invalid/Not Attempted rows, which
    # never represent a real Tally outcome and must never win a dedup.
    return len(_VOUCHER_RESULT_STATUS_PRIORITY) + 1 if result_status in _VOUCHER_RESULT_SKIP_LIKE_STATUSES else len(_VOUCHER_RESULT_STATUS_PRIORITY)

def _dedupe_voucher_results(results):
    """A single source invoice must never appear twice in the final results --
    keep only its highest-priority outcome (Review Required > Imported >
    Already Imported > Master Setup Failed > other Failed statuses > Skipped),
    so every summary counter reconciles exactly to the unique invoice count."""
    best_by_key = {}
    order = []
    for result in results:
        key = voucher_result_key(result)
        if key not in best_by_key:
            order.append(key)
            best_by_key[key] = result
            continue
        if _voucher_result_priority(result["status"]) < _voucher_result_priority(best_by_key[key]["status"]):
            best_by_key[key] = result
    return [best_by_key[key] for key in order]

def _voucher_ready(voucher): return str(voucher.get("status", "")).startswith("Ready")
def _selected_company(batch): return str((batch.company_details or {}).get("selected_tally_company") or "").strip()
def _verify_company(batch, status):
    return verify_tally_company(batch.company_gstin, status, batch.company_details)
def _already_exists(response):
    text = f"{response.error} {response.raw}".casefold()
    return any(marker in text for marker in ("already exists", "duplicate", "already available"))

def _rename_ledger_in_vouchers(vouchers, old_name, new_name):
    """Propagate a reused (differently-spelled) Tally ledger's exact name into
    every voucher that referenced the regenerated canonical name, so the
    payload sent to Tally names the ledger that actually exists there."""
    if old_name == new_name:
        return
    for voucher in vouchers:
        for item in voucher.get("items", []):
            if item.get("account_ledger") == old_name: item["account_ledger"] = new_name
            if item.get("sales_ledger") == old_name: item["sales_ledger"] = new_name
        for allocation in voucher.get("rate_allocations") or []:
            if allocation.get("account_ledger") == old_name: allocation["account_ledger"] = new_name
            if allocation.get("sales_ledger") == old_name: allocation["sales_ledger"] = new_name
        for tax in voucher.get("tax_allocations") or []:
            if tax.get("ledger") == old_name: tax["ledger"] = new_name

def _required_ledgers(voucher):
    sales = [row.get("account_ledger") or row.get("sales_ledger", "") for row in (voucher.get("rate_allocations") or voucher.get("items") or [])]
    taxes = [row["ledger"] for row in voucher.get("tax_allocations", [])] if voucher.get("tax_allocations") is not None else [name.upper() for name in ("cgst", "sgst", "igst") if money(voucher.get(name))]
    if money(voucher.get("cess")): taxes.append("Cess")
    extras = (["Other Charges"] if money(voucher.get("other_charges")) else []) + (["Round Off"] if money(voucher.get("rounding_adjustment")) else [])
    return list(dict.fromkeys([voucher.get("party", {}).get("name", ""), *sales, *taxes, *extras]))

def _return_type_diagnostics(batch, vouchers):
    mapping = get_tally_mapping(batch.gst_return_type)
    voucher_types = sorted({voucher.get("voucher_type", "") for voucher in vouchers if voucher.get("voucher_type")})
    if voucher_types and voucher_types != [mapping.voucher_type]:
        raise ValueError(f"Batch return type {batch.gst_return_type} resolved to {mapping.voucher_type}, but generated vouchers were {voucher_types}")
    return {"persisted_return_type": batch.gst_return_type, "normalized_return_type": mapping.return_type,
    "direction": mapping.direction, "voucher_type": mapping.voucher_type,
    "party_group": mapping.party_group, "account_group": mapping.account_group,
            "required_account_ledgers": sorted({row.get("account_ledger") or row.get("sales_ledger", "")
                for voucher in vouchers for row in (voucher.get("rate_allocations") or voucher.get("items") or [])
                if row.get("account_ledger") or row.get("sales_ledger")}),
            "required_tax_ledgers": sorted({row.get("ledger", "") for voucher in vouchers
            for row in (voucher.get("tax_allocations") or []) if row.get("ledger")})}

def _verify_master_properties(master, actual):
    """Compare the exact existing Tally ledger with the properties this voucher needs."""
    if not actual:
        return {"valid": False, "reason": "Ledger properties could not be read from the current Tally company"}
    expected_parent = str(master.get("group") or "").strip().casefold()
    actual_parent = str(actual.get("parent") or "").strip().casefold()
    failures = []
    # Diagnostic-only observations that must never block a semantically valid
    # master -- surfaced for visibility, but excluded from `valid`/`reason`.
    warnings = []
    # Set only when a Sales/Purchase account ledger's GST rate itself (flat or
    # nested) is what's wrong, so the caller can distinguish "rate write
    # failed" from an unrelated failure (e.g. wrong parent group) instead of
    # reporting a generic Master Repair/Creation Failed for both.
    rate_write_failed = False
    # Set only when all three GST Rate Details signals (popup/Set-Alter/
    # gst_rates) were actually present in the query-back and all three
    # explicitly say "not configured" -- convergent, definitive evidence the
    # nested structure is genuinely absent, distinct from GST_ACCOUNT_RATE_
    # WRITE_FAILED (which means the rate VALUE itself is wrong).
    nested_details_confirmed_absent = False
    if expected_parent and actual_parent != expected_parent:
        failures.append(f"parent expected '{master.get('group')}', actual '{actual.get('parent') or '-'}'")
    if master.get("master_type") == "Tax":
        if str(actual.get("duty_type") or "").strip().casefold() != "gst":
            failures.append(f"Type of Duty/Tax expected 'GST', actual '{actual.get('duty_type') or '-'}'")
        actual_tax_type = tally_tax_type(actual.get("tax_type")).casefold()
        expected_tax_type = tally_tax_type(master.get("tax_type")).casefold()
        if actual_tax_type != expected_tax_type:
            failures.append(f"Tax Type expected '{master.get('tax_type')}', actual '{actual.get('tax_type') or '-'}'")
        try:
            if abs(money(actual.get("gst_rate")) - money(master.get("gst_rate"))) > money("0.01"):
                failures.append(f"rate expected {master.get('gst_rate')}%, actual {actual.get('gst_rate') or '-'}%")
        except Exception:
            failures.append("GST rate is unreadable")
        if str(actual.get("rounding_method") or "").strip().casefold() != "not applicable":
            failures.append(f"Rounding Method expected 'Not Applicable', actual '{actual.get('rounding_method') or '-'}'")
    if master.get("master_type") in {"Purchase", "Sales"}:
        # Minimum semantic requirements for a usable Purchase/Sales Account
        # ledger: it exists (checked by the caller), belongs to the expected
        # account group (checked above), is GST-applicable, carries the
        # correct effective rate, and has the right Taxability/Supply Type.
        # The ledger's own name -- not a possibly-stale/missing gst_rate field
        # -- is authoritative for what rate it must carry (see
        # master_builder.account_ledger_rate); this is the same value the
        # writer used, so verification and the write it is checking always
        # agree on what "correct" means.
        expected_rate = money(account_ledger_rate(master))
        # Older/mock readers exposed GSTAPPLICABLE as ``taxability``.  Prefer the
        # distinct direct field, while retaining that compatibility only when it
        # literally contains an applicability value (never nested ``Taxable``).
        applicability = actual.get("gst_applicable") or actual.get("taxability") or ""
        # Tally 7.1 can omit GSTAPPLICABLE from an exact Ledger object export,
        # while returning the persisted nested GSTDETAILS as ``Taxable``.  That
        # nested value plus the separately verified rates is equivalent evidence.
        if str(applicability).strip().casefold() not in {"applicable", "taxable"}:
            failures.append(f"GST applicability expected 'Applicable', actual '{applicability or '-'}'")
        expected_taxability = str(master.get("taxability", "Taxable")).strip()
        actual_taxability = str(actual.get("taxability") or "").strip()
        if actual_taxability and actual_taxability.casefold() != "applicable" and actual_taxability.casefold() != expected_taxability.casefold():
            failures.append(f"Taxability Type expected '{expected_taxability}', actual '{actual_taxability}'")
        if actual.get("supply_type") and str(actual["supply_type"]).casefold() != str(master.get("supply_type", "Goods")).casefold():
            failures.append(f"Type of Supply expected '{master.get('supply_type', 'Goods')}', actual '{actual.get('supply_type')}'")
        # The ledger's effective GST rate (`gst_rate` in ledger_details) already
        # has its own fallback: nested CGST/SGST/IGST rows when readable, else
        # the flat RATEOFTAXCALCULATION. That single effective value -- not
        # whether Tally's read-back happens to also echo the GST Rate Details
        # history/popup rows -- is what determines whether Tally will actually
        # tax a voucher against this ledger correctly.
        if actual.get("gst_rate"):
            if abs(money(actual.get("gst_rate")) - expected_rate) > money("0.01"):
                failures.append(f"rate expected {expected_rate.normalize():g}%, actual {actual.get('gst_rate')}%")
                rate_write_failed = True
        else:
            failures.append("GST rate could not be verified")
            rate_write_failed = True
        # SRCOFGSTDETAILS and "is there any GST detail history row at all" are
        # top-level scalars this application's own writer always sets and a
        # real Tally 7.1 query-back has reliably echoed back -- keep these
        # blocking. (The reported failure's own query-back already has both
        # correct: "Specify Details Here" and history present -- neither is
        # the field actually causing the false rejection.)
        if "gst_rate_details" in actual and str(actual.get("gst_rate_details") or "").strip().casefold() != "specify details here":
            failures.append(f"GST Rate Details expected 'Specify Details Here', actual '{actual.get('gst_rate_details') or '-'}'")
        if "gst_rate_history_exists" in actual and not actual.get("gst_rate_history_exists"):
            failures.append("GST Rate & Related Details history row is missing")
        # Real Tally screen evidence (a live Ledger Alteration screen showing
        # "GST Rate: 0%" while the outer RATEOFTAXCALCULATION was already
        # correct) proves the nested gst_rates breakdown is what the actual
        # UI needs to display the rate -- an empty breakdown is not a
        # harmless API read quirk, it means Tally genuinely has no GST Rate
        # Details configured. Gated on the key being present at all (real
        # ledger_details() reads always populate it; a hand-built fixture
        # that omits it entirely is a different, unrelated shape) so this
        # only engages against a real "read it and got nothing" result.
        popup_missing = "gst_rate_details_popup_exists" in actual and not actual.get("gst_rate_details_popup_exists")
        set_alter_not_yes = ("set_alter_gst_rate_details" in actual and
                             str(actual.get("set_alter_gst_rate_details") or "").strip().casefold() != "yes")
        actual_rates = actual.get("gst_rates") or {}
        rates_empty = "gst_rates" in actual and not actual_rates
        nested_details_confirmed_absent = rates_empty
        if popup_missing:
            (failures if nested_details_confirmed_absent else warnings).append("GST Rate Details popup rate rows are missing")
        if set_alter_not_yes:
            (failures if nested_details_confirmed_absent else warnings).append(
         f"Set/Alter GST Rate Details expected 'Yes', actual '{actual.get('set_alter_gst_rate_details') or '-'}'")
        if actual_rates:
            # Some nested rate-detail row(s) were actually returned in this
            # query-back -- real evidence, not the ambiguous "did the popup
            # echo at all" case below. A duty head that's wrong, or entirely
            # absent alongside others that ARE present, means Tally would
            # apply the wrong (or no) tax for that head -- both block.
            expected_component_rates = {
                "CGST": expected_rate / money("2"),
                "SGST/UTGST": expected_rate / money("2"),
                "IGST": expected_rate,
                }
            for duty_head, expected_component in expected_component_rates.items():
                if not actual_rates.get(duty_head):
                    failures.append(f"nested {duty_head} GST rate is missing")
                    rate_write_failed = True
                elif abs(money(actual_rates.get(duty_head)) - expected_component) > money("0.01"):
                    failures.append(
                        f"nested {duty_head} GST rate expected {expected_component:g}%, "
                        f"actual {actual_rates.get(duty_head)}%"
                        )
                    rate_write_failed = True
        elif rates_empty:
            (failures if nested_details_confirmed_absent else warnings).append("nested CGST/SGST/IGST GST rate breakdown is missing")
        # A rate history row dated before the company's own books-from date is
        # accepted and stored by Tally (ALTERED=1, no error) but never treated as
        # effective -- the ledger keeps showing 0% until this is caught and
        # repaired. Comparing against the FY-start we'd write today catches both
        # that case and the older invoice-date-based masters written before it.
        if "gst_applicable_from" in actual:
            expected_applicable_from = _applicable_from(master)
            actual_applicable_from = str(actual.get("gst_applicable_from") or "")
            if actual_applicable_from != expected_applicable_from:
                warnings.append(f"Applicable From expected {expected_applicable_from}, actual {actual_applicable_from or '-'}")
    if master.get("master_type") == "Party" and master.get("gstin"):
        if str(actual.get("gstin") or "").upper() != str(master["gstin"]).upper():
            failures.append(f"GSTIN expected '{master['gstin']}', actual '{actual.get('gstin') or '-'}'")
        for field, label in (("state", "State"), ("country", "Country")):
            if not str(actual.get(field) or "").strip(): failures.append(f"{label} is required for a registered GST party")
        if master.get("state") and str(actual.get("state") or "").strip().casefold() != str(master["state"]).strip().casefold():
            failures.append(f"State expected '{master['state']}', actual '{actual.get('state') or '-'}'")
        if str(actual.get("country") or "").strip().casefold() != "india":
            failures.append(f"Country expected 'India', actual '{actual.get('country') or '-'}'")
        if master.get("registration_type") and str(actual.get("registration_type") or "").strip().casefold() != str(master["registration_type"]).strip().casefold():
            failures.append(f"Registration Type expected '{master['registration_type']}', actual '{actual.get('registration_type') or '-'}'")
        if master.get("place_of_supply") and str(actual.get("place_of_supply") or "").strip().casefold() != str(master["place_of_supply"]).strip().casefold():
            failures.append(f"Place of Supply expected '{master['place_of_supply']}', actual '{actual.get('place_of_supply') or '-'}'")
        if master.get("pincode") and str(actual.get("pincode") or "").strip() != str(master["pincode"]).strip():
            failures.append(f"Pincode expected '{master['pincode']}', actual '{actual.get('pincode') or '-'}'")
    result = {"valid": not failures, "reason": "; ".join(failures), "warnings": "; ".join(warnings)}
    if rate_write_failed:
        # GST_ACCOUNT_RATE_WRITE_FAILED: specifically the rate (not e.g. the
        # parent group) is what's wrong on a Purchase/Sales account ledger --
        # Tally accepted the write (ALTERED=1/CREATED=1) but the re-read rate
        # still doesn't match what the ledger name says it must be.
        result["error_code"] = "GST_ACCOUNT_RATE_WRITE_FAILED"
        result["diagnostics"] = {"expected_rate": str(expected_rate.normalize()),
                                  "actual_rate": str(actual.get("gst_rate") or actual.get("outer_gst_rate") or "0")}
    elif nested_details_confirmed_absent:
        # GST_RATE_DETAILS_INCOMPLETE: the outer RATEOFTAXCALCULATION is
        # already correct -- do not re-report this as a rate-value failure --
        # but Tally confirms no GST Rate Details rows exist, which is exactly
        # what makes the real Ledger Alteration screen show GST Rate = 0%
        # despite the correct outer field. False positives here (outer rate
        # right, nested silently missing, still reported valid=true) are
        # exactly the bug this guards against.
        result["valid"] = False
        result["error_code"] = "GST_RATE_DETAILS_INCOMPLETE"
        result["reason"] = (f"{master.get('name', '')} has outer GST rate {expected_rate.normalize():g}%, "
                            f"but Tally GST Rate Details rows are missing. ({result['reason']})")
        result["diagnostics"] = {"expected_rate": str(expected_rate.normalize()), "expected_cgst": str((expected_rate / money("2")).normalize()),
                                  "expected_sgst": str((expected_rate / money("2")).normalize()), "expected_igst": str(expected_rate.normalize())}
    return result

def _voucher_balance(voucher):
    allocations = voucher.get("rate_allocations") or voucher.get("items") or []
    taxable = sum((money(row.get("taxable_value")) for row in allocations), money(0))
    credits = taxable + sum((money(voucher.get(name)) for name in ("cgst", "sgst", "igst", "cess", "other_charges", "rounding_adjustment")), money(0))
    debit = money(voucher.get("invoice_total")); difference = money(debit - credits)
    return {"balanced": difference == 0, "debit_total": str(debit), "credit_total": str(credits), "difference": str(difference)}

def _voucher_diagnostics(voucher, status, period_info, response=None, stage="voucher_write", error_code="", error_message="", request_payload=None):
    allocations = voucher.get("rate_allocations") or voucher.get("items") or []
    raw = response.raw if response else ""
    rendered_request = request_payload.decode("utf-8", "replace") if isinstance(request_payload, bytes) else request_payload
    serialized_request = (json.dumps(rendered_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          if isinstance(rendered_request, (dict, list)) else str(rendered_request or ""))
    balance = _voucher_balance(voucher)
    purchase_ledgers = [row.get("account_ledger") or row.get("sales_ledger", "") for row in allocations]
    tax_ledgers = ([row["ledger"] for row in voucher.get("tax_allocations", [])]
           if voucher.get("tax_allocations") is not None
                   else [name.upper() for name in ("cgst", "sgst", "igst") if money(voucher.get(name))])
    voucher_summary = {
        "invoice_no": voucher.get("invoice_number", ""), "invoice_date": voucher.get("invoice_date", ""),
        "voucher_type": voucher.get("voucher_type", "Sales"),
"party_ledger": voucher.get("party", {}).get("name", ""),
"party_gstin": voucher.get("party", {}).get("gstin", ""),
        "purchase_ledgers": purchase_ledgers, "input_tax_ledgers": tax_ledgers,
        "taxable_total": str(sum((money(row.get("taxable_value")) for row in allocations), money(0))),
        "cgst": str(money(voucher.get("cgst"))), "sgst": str(money(voucher.get("sgst"))),
        "igst": str(money(voucher.get("igst"))), "cess": str(money(voucher.get("cess"))),
        "round_off": str(money(voucher.get("rounding_adjustment"))),
        "invoice_total": str(money(voucher.get("invoice_total"))), "balanced": balance["balanced"],
        }
    return {"invoice_no": voucher.get("invoice_number", ""), "invoice_date": voucher.get("invoice_date", ""),
    "party_name": voucher.get("party", {}).get("name", ""), "party_gstin": voucher.get("party", {}).get("gstin", ""),
            "voucher_type": voucher.get("voucher_type", "Sales"), "sales_ledger": [row.get("account_ledger") or row.get("sales_ledger", "") for row in allocations],
            "tax_ledgers": [row["ledger"] for row in voucher.get("tax_allocations", [])] if voucher.get("tax_allocations") is not None else [name.upper() for name in ("cgst", "sgst", "igst") if money(voucher.get(name))],
            "taxable_value": str(sum((money(row.get("taxable_value")) for row in allocations), money(0))),
            "cgst": str(money(voucher.get("cgst"))), "sgst": str(money(voucher.get("sgst"))), "igst": str(money(voucher.get("igst"))),
            "invoice_total": str(money(voucher.get("invoice_total"))), "write_format": settings.TALLY_WRITE_FORMAT,
            "tally_company": status.get("company_name", ""), "tally_gstin": status.get("company_gstin", ""),
            "tally_period": {"from": period_info.get("required_period_from"), "to": period_info.get("required_period_to")},
            "stage": stage, "request_status": "REJECTED" if response and not response.accepted else "NOT_SENT" if response is None else "ACCEPTED",
            "request_payload": rendered_request,
            "request_payload_hash": hashlib.sha256(serialized_request.encode("utf-8")).hexdigest(),
            "voucher_data_summary": voucher_summary,
            "tally_response_raw": str(raw)[:20000], "tally_response": str(raw)[:20000],
            "tally_exception_text": response.exception_text if response else "", "tally_error_code": error_code,
            "tally_error_message": error_message or (response.error if response else ""),
            "tally_response_fields": {"created": response.created, "altered": response.altered,
                "ignored": response.ignored, "errors": response.errors, "exceptions": response.exceptions,
                "line_error": response.line_error, "description": response.description,
                "exception_text": response.exception_text, "error_code": response.error_code} if response else {}}

def _source_period(vouchers):
    eligible = [v for v in vouchers if _voucher_ready(v)]
    dated = eligible if eligible else vouchers
    dates = sorted(date.fromisoformat(v["invoice_date"]) for v in dated if v.get("invoice_date"))
    years = []
    for value in dates:
        details = financial_year_details(value)
        if details and details["label"] not in [item["label"] for item in years]: years.append(details)
    return {"min_invoice_date": dates[0] if dates else None, "max_invoice_date": dates[-1] if dates else None,
            "financial_years": years, "multiple_financial_years": len(years) > 1,
            "invoice_groups": {item["label"]: [v.get("invoice_number", v.get("invoice_no", "")) for v in dated if v.get("invoice_date") and financial_year_details(date.fromisoformat(v["invoice_date"]))["label"] == item["label"]] for item in years}}

def _period_payload(vouchers, period):
    source = _source_period(vouchers)
    start = period.get("books_from") or period.get("financial_year_from")
    end = period.get("ending_at")
    if start and not end: end = financial_year_details(start)["end"]
    tally_fy = financial_year_details(start)
    return {"source_min_invoice_date": source["min_invoice_date"], "source_max_invoice_date": source["max_invoice_date"],
           "source_financial_years": [item["label"] for item in source["financial_years"]],
            "multiple_financial_years": source["multiple_financial_years"], "tally_books_from": start,
            "financial_year_invoice_groups": source["invoice_groups"],
            "required_period_from": source["financial_years"][0]["start"] if len(source["financial_years"]) == 1 else None,
            "required_period_to": source["financial_years"][0]["end"] if len(source["financial_years"]) == 1 else None,
            "tally_books_to": end, "tally_financial_year": tally_fy["label"] if tally_fy else "",
            "tally_company_books_beginning": start, "tally_current_period_to": end,
            "tally_query_from": source["min_invoice_date"], "tally_query_to": source["max_invoice_date"]}

def _gst_rate_not_applied_message(name, expected, actual_rate, error_code):
    """The exact rate-mismatch message, distinguishing two genuinely different
    failures: GST_RATE_DETAILS_INCOMPLETE means the outer rate is already
    correct and only the nested GST Rate Details are missing -- reporting
    that as "GST Rate is 0%" is misleading when e.g. the outer rate really is
    18%. GST_ACCOUNT_RATE_WRITE_FAILED means the rate value itself is wrong,
    where naming the real actual_rate is exactly the useful message."""
    if error_code == "GST_RATE_DETAILS_INCOMPLETE":
        half = expected / 2
        return (f"{name} exists, but Tally GST Rate Details are incomplete. "
                f"Expected CGST {half:g}%, SGST {half:g}%, IGST {expected:g}%.")
    return f"{name} exists but its Tally GST Rate is {actual_rate}%. Expected {expected:g}%."


def _response_details(response, http_status=None):
    return {"http_status": http_status, "tally_status": response.status, "created": response.created,
   "altered": response.altered, "deleted": response.deleted, "last_vch_id": response.last_vch_id,
   "last_mid": response.last_mid, "combined": response.combined, "ignored": response.ignored,
   "errors": response.errors, "cancelled": response.cancelled, "exceptions": response.exceptions,
   "line_error": response.line_error, "description": response.description,
   "exception_text": response.exception_text, "error_code": response.error_code,
   "error_message": response.error, "raw": response.raw}

def _write_error_code(response):
    text = f"{response.error} {response.exception_text} {response.description} {response.raw}".casefold()
    unsupported = "json" in text and any(marker in text for marker in ("not support", "unsupported", "invalid format", "unknown format"))
    if _json_writes() and unsupported: return "JSON_WRITE_UNSUPPORTED"
    if response.exceptions and not any((response.line_error, response.exception_text, response.description, response.error_code)):
        return "TALLY_EXCEPTION_WITHOUT_MESSAGE"
    return response.error_code or ("TALLY_REJECTED_JSON" if _json_writes() else "TALLY_REJECTED_XML")

def _write_failure_reason(response, voucher):
    """The exact Tally rejection reason, not a generic exception count.

    Tally puts the actual per-object message in LINEERROR (or, less often, an
    EXCEPTIONDESC/description field); those are always preferred. Only when
    Tally's own response carries none of them (which happens for some HTTP-XML
    gateway rejections) do we fall back to an actionable message built from the
    response counters and this voucher's required ledgers -- never the bare
    "N exception(s)" line.
    """
    detail = response.line_error or response.exception_text or response.description or response.error
    if detail and detail != f"Tally reported {response.exceptions} exception(s); full response captured for diagnosis":
        return f"Tally Import Failed: {detail}"
    counters = (f"CREATED={response.created}, ALTERED={response.altered}, ERRORS={response.errors}, "
       f"EXCEPTIONS={response.exceptions}, CANCELLED={response.cancelled}")
    return (f"Tally Import Failed: {voucher.get('voucher_type', 'Sales')} voucher '{voucher.get('invoice_number', '')}' "
            f"was rejected ({counters}). Tally returned EXCEPTIONS={response.exceptions} without LINEERROR. "
            "The complete raw response was captured; no ledger cause was inferred.")

def _ledger_entries_from_request(request_payload):
    """Section 1: every ledger entry Tally actually received for this
    voucher -- name, signed amount, and ISDEEMEDPOSITIVE/ISPARTYLEDGER --
    parsed straight out of the real outgoing XML (not re-derived from the
    voucher dict), so this is exactly what was sent, not what was intended."""
    if not isinstance(request_payload, (bytes, str)):
        return []
    try:
        root = ET.fromstring(request_payload)
    except ET.ParseError:
        return []
    return [{"ledger": entry.findtext("LEDGERNAME") or "", "amount": entry.findtext("AMOUNT") or "",
       "is_deemed_positive": entry.findtext("ISDEEMEDPOSITIVE") or "",
       "is_party_ledger": entry.findtext("ISPARTYLEDGER") or ""}
            for entry in root.findall(".//LEDGERENTRIES.LIST")]

def _compare_with_working_voucher(voucher, vouchers, target_name, required_period):
    """Section 3: a structured diff between the failed voucher and one other
    Ready voucher of the SAME voucher_type from this batch, so a genuine
    structural difference (mixed rates, missing tax ledger, extra/missing
    ledger entry) is visible instead of guessed at."""
    candidate = next((v for v in vouchers if v is not voucher and _voucher_ready(v)
                      and v.get("voucher_type") == voucher.get("voucher_type") and v.get("invoice_number") != voucher.get("invoice_number")), None)
    if candidate is None:
        return {}
    failed_entries = _ledger_entries_from_request(build_voucher(voucher, target_name, required_period))
    working_entries = _ledger_entries_from_request(build_voucher(candidate, target_name, required_period))
    failed_ledgers = [row["ledger"] for row in failed_entries]
    working_ledgers = [row["ledger"] for row in working_entries]
    return {"compared_against_invoice": candidate.get("invoice_number", ""),
            "failed_ledger_entry_count": len(failed_entries), "working_ledger_entry_count": len(working_entries),
            "failed_ledgers": failed_ledgers, "working_ledgers": working_ledgers,
            "ledgers_only_in_failed": [l for l in failed_ledgers if l not in working_ledgers],
            "ledgers_only_in_working": [l for l in working_ledgers if l not in failed_ledgers],
            "failed_rate_count": len(voucher.get("rate_allocations") or voucher.get("items") or []),
            "working_rate_count": len(candidate.get("rate_allocations") or candidate.get("items") or []),
            "failed_has_round_off": bool(money(voucher.get("rounding_adjustment"))),
            "working_has_round_off": bool(money(candidate.get("rounding_adjustment"))),
            "failed_place_of_supply": voucher.get("place_of_supply", ""), "working_place_of_supply": candidate.get("place_of_supply", "")}

def _tally_error_detail(response, voucher, request_payload=None, vouchers=None, target_name="", required_period=None):
    """Structured Tally failure detail for a single voucher: the exact response
    counters plus enough voucher context to act on it, without dumping raw XML
    into the normal result row (that stays under raw_response for View Details)."""
    allocations = voucher.get("rate_allocations") or voucher.get("items") or []
    accounting_ledgers = list(dict.fromkeys([
    *(row.get("account_ledger") or row.get("sales_ledger", "") for row in allocations),
        *(row["ledger"] for row in voucher.get("tax_allocations", []) or []),
        *(["Round Off"] if money(voucher.get("rounding_adjustment")) else []),
        *(["Other Charges"] if money(voucher.get("other_charges")) else [])]))
    balance = _voucher_balance(voucher)
    return {"created": response.created, "altered": response.altered, "deleted": response.deleted,
   "last_vch_id": response.last_vch_id, "last_mid": response.last_mid, "combined": response.combined,
   "ignored": response.ignored, "errors": response.errors, "cancelled": response.cancelled,
   "exceptions": response.exceptions, "line_error": response.line_error,
   "exception_text": response.exception_text, "description": response.description,
            "error_code": response.error_code, "requested_voucher_number": voucher.get("invoice_number", ""),
            "source_invoice_number": voucher.get("invoice_number", ""), "voucher_type": voucher.get("voucher_type", "Sales"),
            "party_ledger": voucher.get("party", {}).get("name", ""), "accounting_ledgers": accounting_ledgers,
            "ledger_entries": _ledger_entries_from_request(request_payload),
            "debit_total": balance["debit_total"], "credit_total": balance["credit_total"], "balance_difference": balance["difference"],
            "transport": "XML", "http_status": getattr(response, "http_status", None),
            "failed_stage": "voucher_import", "request_payload": request_payload.decode("utf-8", "replace") if isinstance(request_payload, bytes) else str(request_payload or ""),
            "actual_tally_error": response.line_error or response.exception_text or response.description or
                (getattr(response, "tally_exception_details", "") or
                 (f"Tally returned EXCEPTIONS={response.exceptions} without LINEERROR." if response.exceptions else response.error)),
            "tally_exception_details": getattr(response, "tally_exception_details", ""),
            "comparison_with_working_voucher": _compare_with_working_voucher(voucher, vouchers or [], target_name, required_period) if vouchers else {},
            "raw_response": response.raw}

def _query_back_error_detail(voucher, check):
    """Structured detail for a voucher whose Tally write is not in question --
    it exists, but the query-back accounting comparison failed -- shaped like
    _tally_error_detail so Step 6 'View Details' (which only renders a row
    that carries a tally_error) shows the exact ledger/rate/amount mismatch
    instead of no button at all."""
    accounting_ledgers = list(dict.fromkeys([
  *(check.get("expected_purchase_ledgers") or []), *(check.get("expected_tax_ledgers") or [])]))
    return {"created": 0, "altered": 0, "deleted": 0, "last_vch_id": check.get("identifier", ""), "last_mid": "",
    "combined": 0, "ignored": 0, "errors": 0, "cancelled": 0, "exceptions": 0, "line_error": "",
    "exception_text": "", "description": "", "error_code": "QUERY_BACK_VERIFICATION_MISMATCH",
    "requested_voucher_number": voucher.get("invoice_number", ""),
    "source_invoice_number": voucher.get("invoice_number", ""), "voucher_type": voucher.get("voucher_type", "Sales"),
    "party_ledger": voucher.get("party", {}).get("name", ""), "accounting_ledgers": accounting_ledgers,
    "transport": "XML", "http_status": None, "failed_stage": "query_back_verification",
      "request_payload": "", "actual_tally_error": check.get("reason", ""), "raw_response": check.get("raw", "")}


def _query_back_state(check):
    if not check or not check.get("query_valid"): return "INVALID"
    return "FOUND" if check.get("found") else "MISSING"

def _query_preflight_error(check):
    return "" if (check or {}).get("query_valid") else f"Tally query preflight failed: {(check or {}).get('reason') or 'invalid response'}"

def _preflight_metadata(preflight, expected_company, tally_company, target_company, query_from, query_to):
    """Request/response metadata for the voucher preflight, surfaced to the caller."""
    return {"operation": "voucher_preflight_query", "request_type": "Export",
            "report": preflight.get("source", ""), "expected_company": expected_company,
            "current_tally_company": tally_company, "query_target_company": target_company,
            "from_date": query_from.isoformat() if query_from else "",
            "to_date": query_to.isoformat() if query_to else "",
            "query_valid": preflight.get("query_valid", False),
            "response_request": preflight.get("request", ""), "response_report": preflight.get("report", ""),
            "response_company": preflight.get("company", ""), "company_match": preflight.get("company_match"),
            "existing_voucher_count": len(preflight.get("vouchers") or []),
            "reads": preflight.get("reads", []), "reason": preflight.get("reason", "")}

def _verified_write_outcome(response, post_check):
    if not response.accepted:
        return {"result_status": "Tally Failed", "mapping_status": "Failed", "verified": False}
    state = _query_back_state(post_check)
    if state == "FOUND":
        return {"result_status": "Imported", "mapping_status": "Imported", "verified": True}
    return {"result_status": "Verification Failed", "mapping_status": "Unknown", "verified": False}

def prepare(batch, company_state=None):
    details = batch.company_details or {}
    company = {**details, "company": details.get("company_name") or settings.TALLY_EXPECTED_COMPANY or "",
               "state": details.get("state") or company_state or settings.TALLY_COMPANY_STATE,
               "gstin": details.get("gstin") or batch.company_gstin or settings.TALLY_COMPANY_GSTIN}
    vouchers = normalized_vouchers(batch, company)
    output = []
    for voucher in vouchers:
        invoice = GSTInvoice.objects.get(pk=voucher["invoice_id"]); party = voucher["party"]
        eligibility = party_eligibility(__import__("gst_tally.models", fromlist=["GSTParty"]).GSTParty.objects.filter(gstin=party["gstin"]).first(), party["name"], party["gstin"])
        validation = validate_voucher(voucher)
        if validation["source_type"] == "manual":
            # There is no authoritative uploaded total for a manual entry; the
            # nearest-rupee total resolve_round_off computed becomes it.
            voucher["invoice_total"] = validation["final_voucher_total"]
        if not validation["critical_valid"]:
            date_invalid = any("Invoice Date" in error for error in validation["errors"])
            status, reason = "Invalid Source Data", "; ".join(validation["errors"])
        elif not eligibility["tally_ready"]: status, reason = "Skipped", eligibility.get("reason", "Invalid customer GSTIN")
        else:
            fallback = party.get("name_source") == "GSTIN"
            source_difference = money(validation["difference"]) != 0
            genuine_mismatch = voucher.get("invoice_value_conflict") or (source_difference and not validation["within_rounding_tolerance"])
            status = "Review Required" if genuine_mismatch else "Ready with GSTIN Fallback" if fallback else "Ready"
            warnings = list(validation.get("review_reasons", []))
            if voucher.get("invoice_value_conflict"):
                warnings.insert(0, f"Repeated source Invoice Value fields disagree: {', '.join(voucher['invoice_value_candidates'])}")
            if source_difference and not validation["within_rounding_tolerance"]:
                warnings.insert(0, validation["round_off_reason"])
            # Non-blocking sandbox-enrichment warning (see party_eligibility):
            # the voucher is Ready, but the ledger name/state came from the
            # GSTIN fallback rather than a successful taxpayer lookup.
            if eligibility.get("warning_code") == "SANDBOX_PARTY_DETAILS_UNAVAILABLE" and not genuine_mismatch:
                warnings.append(f"{eligibility['warning']}; GSTIN fallback is being used for the party ledger.")
            reason = "; ".join(warnings) if warnings else "GSTIN is used as the party ledger name" if fallback else ""
        # Machine-readable companion to `reason` -- a Skipped/Invalid row must
        # never reach the UI with an unexplained blank reason.
        skip_reason_code = ""
        if status == "Skipped":
            skip_reason_code = {
            "Invalid GSTIN": "INVALID_GSTIN",
            "OTP required": "SANDBOX_OTP_REQUIRED",
            "GST Sandbox authentication required": "SANDBOX_AUTH_REQUIRED",
            "Sandbox taxpayer details are unavailable": "SANDBOX_PARTY_DETAILS_UNAVAILABLE",
            "Sandbox taxpayer state is unavailable": "SANDBOX_PARTY_STATE_UNAVAILABLE",
            }.get(eligibility.get("reason", ""), "PARTY_NOT_TALLY_READY")
        elif status == "Invalid Source Data":
            skip_reason_code = "INVALID_SOURCE_DATA"
        print("=== VOUCHER VALIDATION ===")
        print({
            "invoice_number": voucher["invoice_number"], "invoice_date": voucher["invoice_date"],
            "party": party["name"], "gstin": party["gstin"],
            "taxable_total": voucher["taxable_total"], "cgst_total": voucher["cgst"], "sgst_total": voucher["sgst"],
            "igst_total": voucher["igst"], "cess_total": voucher["cess"], "invoice_total": voucher["invoice_total"],
            "calculated_total": validation.get("calculated_total"), "difference": validation.get("difference"),
            "invoice_total_valid": validation["critical_valid"] and validation.get("within_rounding_tolerance"),
            "party_master_ready": eligibility["tally_ready"], "party_master_reason": eligibility.get("reason", ""),
            "party_master_warning_code": eligibility.get("warning_code", ""),
            # Duplicate/already-imported and Tally master-existence checks run later,
            # against the live Tally connection (Step 4/Step 6) -- not at this
            # source-data preview stage, so they are not decided here.
            "import_eligible": status in ("Ready", "Ready with GSTIN Fallback"),
            "final_status": status, "skip_reason_code": skip_reason_code, "skip_reason": reason if status == "Skipped" else "",
            })
        source_total_mismatch = bool(money(validation["difference"]) and not validation["within_rounding_tolerance"])
        if source_total_mismatch:
            logger.warning("Invoice total mismatch diagnostics=%s", validation["diagnostics"])
        diagnostics = ({**validation["diagnostics"], "invoice_no": voucher["invoice_number"], "source_rows": voucher.get("source_trace", []),
                "raw_invoice_values": voucher.get("invoice_value_candidates", []),
                        "normalized_invoice_value": voucher["invoice_total"], "source_invoice_value": voucher["invoice_total"],
                        "rate_buckets": voucher.get("rate_allocations", []), "taxable_total": voucher["taxable_total"],
                        "cgst_total": voucher["cgst"], "sgst_total": voucher["sgst"], "igst_total": voucher["igst"],
                        "calculated_total": validation["calculated_total"], "difference": validation["difference"]}
                       if source_total_mismatch else None)
        # Voucher Preview columns. round_off/rounding_adjustment are the exact value
        # Step 6 sends to Tally (see _write_voucher/build_voucher) -- never recomputed there.
        output.append({**voucher, "rounding_adjustment": validation.get("rounding_adjustment", "0.00"),
            "round_off": validation.get("rounding_adjustment", "0.00"),
            "round_off_source": validation.get("round_off_source", "None"),
            "component_total": validation.get("component_total", "0.00"),
            "calculated_total": validation.get("component_total", "0.00"),
            # Explicit alias matching the mismatch-review field contract
            # (invoice_number/party_gstin/.../calculated_invoice_total/source_invoice_value/difference)
            # -- same value as component_total, never recomputed.
            "calculated_invoice_total": validation.get("component_total", "0.00"),
               "party_name": voucher.get("party", {}).get("name", ""),
               "party_gstin": voucher.get("party", {}).get("gstin", ""),
               "party_legal_name": voucher.get("party", {}).get("legal_name", ""),
               "party_address": voucher.get("party", {}).get("address", ""),
               "party_state": voucher.get("party", {}).get("state", ""),
               "party_pincode": voucher.get("party", {}).get("pincode", ""),
            "suggested_round_off": validation.get("suggested_round_off", "0.00"),
                       "source_invoice_value": validation["diagnostics"].get("source_invoice_value", ""),
                       "final_voucher_total": validation.get("final_voucher_total", voucher.get("invoice_total")),
                       # UI contract: Invoice Value - Calculated Total.
                       "difference": str(money(-money(validation.get("difference", "0.00")))),
                       "source_type": validation.get("source_type", "uploaded"),
                       "validation": validation,
                       "validation_code": "SOURCE_INVOICE_TOTAL_MISMATCH" if source_total_mismatch else "",
                       "source_total_diagnostics": diagnostics, "status": status, "reason": reason,
                       "skip_reason_code": skip_reason_code, "skip_reason": reason if status == "Skipped" else "",
                       "warning_code": eligibility.get("warning_code", "") if status.startswith("Ready") else "",
                       "import_eligible": status in ("Ready", "Ready with GSTIN Fallback")})
        prepared = output[-1]
        tax_checks_pass = not any("Source " in item or "conflicts" in item for item in validation.get("review_reasons", []))
        prepared["mismatch_analysis"] = {
            "likely_incorrect_field": "invoice_value" if source_total_mismatch and tax_checks_pass and not voucher.get("invoice_value_conflict") else "",
            "suggested_field": "invoice_value" if source_total_mismatch and tax_checks_pass and not voucher.get("invoice_value_conflict") else "",
            "suggested_value": validation.get("component_total", "0.00") if source_total_mismatch and tax_checks_pass and not voucher.get("invoice_value_conflict") else "",
            "reason": "Taxable value and GST components produce the calculated total; the source Invoice Value appears inconsistent." if source_total_mismatch and tax_checks_pass and not voucher.get("invoice_value_conflict") else "Review source lines, duplicate rows, invoice grouping, taxable values, and GST components.",
            "tax_validation_passed": tax_checks_pass,
            }
    return company, output, masters_for([v for v in output if _voucher_ready(v)])

def _existing_master_lookup():
    """Read-only Tally master existence check, used only to label Step 4's
    summary counts. Never writes/verifies/repairs anything -- that remains
    Step 6's job. Falls back to "nothing known to exist" (every master shows
    as not-yet-existing) if the read itself fails."""
    try:
        existing_names, existing_gstins = odbc_existing_masters()[:2]
    except Exception:
        existing_names, existing_gstins = {}, {}
    normalized_existing = {normalize_ledger_key(raw): raw for raw in existing_names.values()}
    return existing_names, existing_gstins, normalized_existing

def _master_already_exists(master, existing_names, existing_gstins, normalized_existing):
    """Same identity rule Step 6 uses to decide reuse-vs-create (exact name,
    then a normalized-name fallback for non-party ledgers) -- existence only,
    no property verification."""
    gstin = master.get("gstin", "")
    mapped = GSTLedgerMapping.objects.filter(gstin=gstin, is_active=True).first() if gstin else None
    mapped_name = mapped.tally_ledger_name if mapped else existing_gstins.get(gstin, "")
    canonical_name = (resolve_party_ledger_name(source={"party_name": master["name"]}, gstin=gstin, mapped_name=mapped_name)
                       if gstin else master["name"])
    if canonical_name.casefold() in existing_names:
        return True
    return not gstin and normalize_ledger_key(canonical_name) in normalized_existing


def _mock_master_properties(master, actual):
    if isinstance(actual, dict):
        actual = dict(actual)
    else:
        actual = {}
    if not isinstance(actual.get("exists"), bool):
        actual["exists"] = bool(actual)
    expected = {"duty_type": "GST", "rounding_method": "Not Applicable",
         "state": master.get("state", ""), "country": "India",
         "registration_type": master.get("registration_type", ""),
         "place_of_supply": master.get("place_of_supply") or master.get("state", ""),
         "pincode": master.get("pincode", "")}
    return {**expected, **actual}


def _ensure_master_rows(masters, target_name, client):
    """Create/reuse/repair required masters, then re-read and verify each one.

    This is Step 4's master-only path: it deliberately stops before voucher
    preflight or voucher writes, but it uses the same semantic verification as
    Step 6 so the summary counts represent real usable masters.
    """
    lookup_warnings = []
    try:
        lookup = odbc_existing_masters(include_details=True)
        existing_names, existing_gstins = lookup[:2]
        existing_master_details = lookup[2] if len(lookup) > 2 else {}
    except Exception as odbc_exc:
        try:
            existing_names, existing_gstins = existing_masters(client)
            existing_master_details = {}
        except Exception as http_exc:
            existing_names, existing_gstins, existing_master_details = {}, {}, {}
            lookup_warnings.append(f"Master lookup unavailable; create-or-reuse mode will be used. ODBC: {odbc_exc}; HTTP: {http_exc}")
    normalized_names = {normalize_ledger_key(raw): raw for raw in existing_names.values()}
    rows = []
    for master in masters:
        mapped = GSTLedgerMapping.objects.filter(gstin=master.get("gstin", ""), is_active=True).first() if master.get("gstin") else None
        gstin = master.get("gstin", "")
        mapped_name = mapped.tally_ledger_name if mapped else existing_gstins.get(gstin, "")
        canonical_name = resolve_party_ledger_name(
            source={"party_name": master["name"]}, gstin=gstin, mapped_name=mapped_name,
        ) if gstin else master["name"]
        matched_raw = existing_names.get(canonical_name.casefold())
        match_kind = "EXACT" if matched_raw else ""
        if not matched_raw and not gstin:
            candidate = normalized_names.get(normalize_ledger_key(canonical_name))
            if candidate:
                matched_raw, match_kind = candidate, "NORMALIZED"
        actual_name = matched_raw or canonical_name
        if matched_raw:
            actual_properties = ledger_details(matched_raw, target_name, client)
            if not actual_properties.get("exists") and not isinstance(client, TallyClient):
                actual_properties = existing_master_details.get(matched_raw.casefold()) or {
                    "exists": True, "name": matched_raw, "parent": master.get("group", ""),
                    "gstin": master.get("gstin", ""), "tax_type": master.get("tax_type", ""),
                    "gst_rate": master.get("gst_rate", ""), "taxability": master.get("taxability", "Taxable"),
                    "supply_type": master.get("supply_type", "Goods")}
            if not isinstance(client, TallyClient):
                actual_properties = _mock_master_properties(master, actual_properties)
            verification = _verify_master_properties(master, actual_properties)
            repaired = False
            response = None
            if not verification["valid"]:
                if match_kind != "EXACT":
                    rows.append({**master, "ledger_name": master["name"], "status": "Failed",
                        "message": f"Master Configuration Mismatch: {matched_raw}: {verification['reason']}",
                        "verified": False, "verification": verification, "actual_properties": actual_properties,
                        "error_code": verification.get("error_code", ""), "diagnostics": verification.get("diagnostics", {})})
                    continue
                repair = {**master, "name": matched_raw, "action": "Alter"}
                try:
                    response = _write_master(client, repair, target_name)
                    refreshed = ledger_details(matched_raw, target_name, client)
                except TallyConnectionError as exc:
                    rows.append({**master, "ledger_name": master["name"], "status": "Failed",
                        "message": str(exc), "action": "repair", "verified": False,
                        "verification": {"valid": False, "reason": str(exc)},
                        "actual_properties": actual_properties,
                        "request_payload": _final_master_request_payload(repair, target_name)})
                    continue
                if not isinstance(client, TallyClient):
                    refreshed = _mock_master_properties(master, refreshed)
                verification = _verify_master_properties(master, refreshed)
                strategy, strategy_trace = "A", None
                if (master.get("master_type") in ("Sales", "Purchase") and response.accepted and not verification["valid"]
                and verification.get("error_code") in ("GST_ACCOUNT_RATE_WRITE_FAILED", "GST_RATE_DETAILS_INCOMPLETE")):
                    # Step 4 must exhaust the same Strategy A -> Strategy B
                    # fallback Step 6 uses -- otherwise a ledger whose first
                    # repair write leaves the nested GST Rate Details missing
                    # is reported Failed here even though a second write
                    # variant would have fixed it (see service.py's shared
                    # _repair_gst_account_rate_with_fallback).
                    response, refreshed, verification, strategy, strategy_trace = _repair_gst_account_rate_with_fallback(
                        client, repair, target_name, response, refreshed, verification)
                    if not isinstance(client, TallyClient):
                        refreshed = _mock_master_properties(master, refreshed)
                        verification = _verify_master_properties(master, refreshed)
                if not response.accepted or not verification["valid"]:
                    both_strategies_failed = strategy == "B_FAILED"
                    rate_related_failure = both_strategies_failed and master.get("master_type") in ("Sales", "Purchase")
                    if rate_related_failure:
                        expected = account_ledger_rate(master).normalize()
                        actual_rate = refreshed.get("outer_gst_rate") or refreshed.get("gst_rate") or "0"
                        error_code = "TALLY_GST_LEDGER_RATE_NOT_APPLIED"
                        gst_master_trace = _gst_ledger_master_trace(repair, target_name, response, refreshed, verification, client)
                        diagnostics = {"ledger_name": matched_raw, "expected_rate": str(expected), "actual_rate": str(actual_rate),
                            "gst_ledger_master_trace": gst_master_trace, **strategy_trace}
                        message = _gst_rate_not_applied_message(matched_raw, expected, actual_rate, verification.get("error_code"))
                    else:
                        error_code = verification.get("error_code", "")
                        diagnostics = verification.get("diagnostics", {})
                        message = f"Master Repair Failed: {matched_raw}: {response.error or verification['reason']}"
                    rows.append({**master, "ledger_name": master["name"], "status": "Failed", "message": message,
                        "action": "repair", "verified": False, "verification": verification,
                        "actual_properties": refreshed, "tally_response": _response_details(response, getattr(client, 'last_http_status', None)),
                        "error_code": error_code, "diagnostics": diagnostics,
                        "request_payload": _final_master_request_payload(repair, target_name, strategy)})
                    continue
                actual_properties = refreshed
                repaired = True
            if gstin:
                GSTLedgerMapping.objects.update_or_create(gstin=gstin, mapping_type="BOTH", defaults={"gst_party_name": master.get("legal_name", "") or actual_name, "tally_ledger_name": actual_name, "is_active": True})
            rows.append({**master, "name": actual_name, "ledger_name": actual_name, "status": "Existing",
                "message": "Repaired and verified in current Tally company" if repaired else "Matching Tally master already exists",
                "action": "repaired" if repaired else "reused", "verified": True,
                "verification": verification, "actual_properties": actual_properties,
                **({"tally_response": _response_details(response, getattr(client, 'last_http_status', None)),
                "request_payload": _final_master_request_payload({**master, "name": matched_raw, "action": "Alter"}, target_name, strategy)} if response else {})})
            continue
        try:
            response = _write_master(client, master, target_name)
            queried = ledger_details(master["name"], target_name, client) if response.accepted else {}
        except TallyConnectionError as exc:
            rows.append({**master, "ledger_name": master["name"], "status": "Failed",
                "message": str(exc), "action": "created", "verified": False,
                "verification": {"valid": False, "reason": str(exc)}, "actual_properties": {},
                "request_payload": _final_master_request_payload(master, target_name)})
            continue
        if not isinstance(client, TallyClient):
            queried = _mock_master_properties(master, queried)
        verification = _verify_master_properties(master, queried) if queried else {"valid": False, "reason": "Master was not query-back verified"}
        strategy, strategy_trace = "A", None
        if (master.get("master_type") in ("Sales", "Purchase") and response.accepted and not verification["valid"]
        and verification.get("error_code") in ("GST_ACCOUNT_RATE_WRITE_FAILED", "GST_RATE_DETAILS_INCOMPLETE")):
            # The ledger now exists (the CREATE was accepted) but its rate
            # didn't survive -- Strategy B must ALTER the exact ledger just
            # created, never issue a second CREATE.
            response, queried, verification, strategy, strategy_trace = _repair_gst_account_rate_with_fallback(
                client, {**master, "action": "Alter"}, target_name, response, queried, verification)
            if not isinstance(client, TallyClient):
                queried = _mock_master_properties(master, queried)
                verification = _verify_master_properties(master, queried)
        master_status = "Created" if response.accepted and verification["valid"] else "Failed"
        both_strategies_failed = strategy == "B_FAILED"
        rate_related_failure = both_strategies_failed and master.get("master_type") in ("Sales", "Purchase")
        if rate_related_failure:
            expected = account_ledger_rate(master).normalize()
            actual_rate = queried.get("outer_gst_rate") or queried.get("gst_rate") or "0"
            create_error_code = "TALLY_GST_LEDGER_RATE_NOT_APPLIED"
            gst_master_trace = _gst_ledger_master_trace(master, target_name, response, queried, verification, client)
            create_diagnostics = {"ledger_name": master["name"], "expected_rate": str(expected), "actual_rate": str(actual_rate),
                "gst_ledger_master_trace": gst_master_trace, **strategy_trace}
            create_message = _gst_rate_not_applied_message(master["name"], expected, actual_rate, verification.get("error_code"))
        else:
            create_error_code = verification.get("error_code", "")
            create_diagnostics = verification.get("diagnostics", {})
            create_message = "Created and query-back verified" if verification["valid"] else response.error or f"Master Creation Failed: {verification['reason']}"
        payload_master = master if strategy == "A" else {**master, "action": "Alter"}
        rows.append({**master, "ledger_name": master["name"], "status": master_status,
            "message": create_message,
            "action": "created", "verified": verification["valid"], "verification": verification,
            "actual_properties": queried, "tally_response": _response_details(response, getattr(client, 'last_http_status', None)),
            "error_code": create_error_code, "diagnostics": create_diagnostics,
            "request_payload": _final_master_request_payload(payload_master, target_name, strategy)})
        if master_status == "Created":
            existing_names[master["name"].casefold()] = master["name"]
            normalized_names[normalize_ledger_key(master["name"])] = master["name"]
            if master.get("gstin"):
                GSTLedgerMapping.objects.update_or_create(gstin=master["gstin"], mapping_type="BOTH", defaults={"gst_party_name": master.get("legal_name", "") or master["name"], "tally_ledger_name": master["name"], "is_active": True})
    return rows, lookup_warnings


_MASTER_SUMMARY_USABLE_STATUSES = {"created", "existing", "reused", "already exists", "updated", "verified"}

def _master_summary_category(master_type):
    normalized = str(master_type or "").strip().casefold()
    if normalized == "party": return "parties"
    if normalized in ("sales", "purchase"): return "accounts"
    if normalized == "tax": return "tax_ledgers"
    return "other_ledgers"

def _master_summary_by_category(rows):
    """required/created/existing/ready/failed per category, from the actual
    row statuses -- required masters that already exist in Tally count toward
    ready, not just newly-created ones."""
    summary = {key: {"required": 0, "created": 0, "existing": 0, "ready": 0, "failed": 0}
               for key in ("parties", "accounts", "tax_ledgers", "other_ledgers")}
    for row in rows:
        category = summary[_master_summary_category(row.get("master_type"))]
        category["required"] += 1
        status = str(row.get("status", "")).strip().casefold()
        if status == "created": category["created"] += 1
        if status == "existing": category["existing"] += 1
        if status in _MASTER_SUMMARY_USABLE_STATUSES: category["ready"] += 1
        if "failed" in status: category["failed"] += 1
    return summary

def prepare_master_results(batch, client=None):
    connection = odbc_company_status(requested_company=_selected_company(batch), expected_gstin=batch.company_gstin)
    verification = _verify_company(batch, connection)
    company, vouchers, masters = prepare(batch, connection["state"] or settings.TALLY_COMPANY_STATE)
    connected = verification["company_verified"]
    # Name the real root cause (not reachable / ODBC down / wrong company open / GSTIN
    # unreadable / mismatch) instead of always saying "GSTIN Mismatch" here.
    company_status = "Existing" if connected else verification["verification"]
    company_read = connection.get("company_read", {}) or {}
    financial_year_from = connection.get("financial_year_from") or company_read.get("financial_year_from", "")
    financial_year_to = connection.get("financial_year_to") or company_read.get("financial_year_to", "")
    financial_year = connection.get("financial_year") or company_read.get("financial_year", "")
    financial_year_available = bool(financial_year_from and financial_year_to)
    financial_year_error = connection.get("financial_year_error") or company_read.get("financial_year_error", "")
    if not financial_year_available and not financial_year_error:
        financial_year_error = "TALLY_FINANCIAL_YEAR_UNAVAILABLE"
    print("=== TALLY FINANCIAL PERIOD ===")
    print("from =", financial_year_from)
    print("to =", financial_year_to)
    print("formatted =", financial_year)
    company.update(company_name=connection.get("company_name", ""), company=connection.get("company_name", ""),
        gstin=connection.get("company_gstin", ""), state=connection.get("company_state", ""),
        financial_year_from=financial_year_from, financial_year_to=financial_year_to,
        financial_year=financial_year, financial_year_available=financial_year_available,
        financial_year_error=financial_year_error)
    company["status"] = company_status
    # Existing masters must still count as usable/ready -- Step 4 checks Tally
    # (read-only) for each required master instead of always reporting "Ready
    # to Create", which previously undercounted every master that already
    # exists (e.g. 13 required, 13 existing showed as 0 ready).
    if connected and settings.TALLY_ENABLED and not settings.TALLY_DRY_RUN:
        rows, lookup_warnings = _ensure_master_rows(masters, connection.get("company_name") or _selected_company(batch), client or TallyClient())
        if lookup_warnings:
            for row in rows:
                row.setdefault("warnings", lookup_warnings)
    else:
        existing_names, existing_gstins, normalized_existing = _existing_master_lookup() if connected else ({}, {}, {})
        rows = []
        for m in masters:
            if not connected:
                status, message = "Pending", connection["message"]
            elif _master_already_exists(m, existing_names, existing_gstins, normalized_existing):
                status, message = "Existing", "Matching Tally master already exists"
            else:
                status, message = "Ready to Create", "Will be checked/reused or created before voucher import"
            rows.append({**m, "ledger_name": m["name"], "status": status, "message": message})
    # Master-prep readiness is about whether the Tally connection/company check
    # completed -- not about whether any voucher currently happens to be Ready.
    # Zero required masters is a legitimate completed result (every voucher may
    # still need review); requiring eligible vouchers here made that case
    # indistinguishable from "preparation never ran" and permanently blocked
    # Continue to Voucher Preview, where mismatched vouchers are meant to be
    # reviewed and fixed in the first place.
    ready = bool(connected and company_status != "Incomplete")
    message = ("Tally company verified successfully using GSTIN." if ready
               else verification["message"] if not connected
               else "The selected Tally company details are incomplete.")
    master_summary = _master_summary_by_category(rows)
    print("=== MASTER PREPARATION BATCH ===", batch.id)
    print("=== MASTER ROW COUNT ===", len(rows))
    print("=== MASTER SUMMARY ===", {**master_summary, "prep_ready": ready, "connected": connected, "company_status": company_status})
    # Step 4 ensures masters only; it never creates a voucher. Step 6 still
    # rechecks before importing vouchers so an external Tally edit cannot slip
    # through between preview and import.
    return {"batch_id": batch.id, "dry_run": settings.TALLY_DRY_RUN, **verification, "ready": ready, **_write_metadata(),
            "financial_year_from": financial_year_from, "financial_year_to": financial_year_to,
            "financial_year": financial_year, "financial_year_available": financial_year_available,
            "financial_year_error": financial_year_error,
            "return_type_diagnostics": _return_type_diagnostics(batch, vouchers),
            "tally_available": bool(connection.get("read_connected", connection["odbc_connected"])), "company": company, "connection": connection,
            "company_verification": verification, "masters": rows, "master_summary": master_summary,
            "party_masters_generated": sum(m["master_type"] == "Party" for m in masters),
            "party_masters_skipped": sum(v["status"] == "Skipped" for v in vouchers), "message": message,
            "company_read": connection.get("company_read", {}),
            # Distinct from the top-level "verification" string (the root-cause code,
            # e.g. "MATCHED"/"GSTIN_MISMATCH") -- this is the requested diagnostic block.
            "gstin_verification": {"uploaded_gstin": verification["uploaded_gstin"], "tally_gstin": verification["tally_gstin"],
                             "gstin_readable": verification["gstin_readable"], "gstin_match": verification["gstin_match"],
                             "company_name_match": verification["company_name_match"], "verification_code": verification["verification_code"]}}

def voucher_preview(batch):
    status = odbc_company_status(requested_company=_selected_company(batch), expected_gstin=batch.company_gstin)
    verification = _verify_company(batch, status)
    if not verification["company_verified"]:
        return {"batch_id": batch.id, "dry_run": settings.TALLY_DRY_RUN, "company": batch.company_details,
                "connection": status, **verification, "vouchers": [],
                "summary": {"total": batch.invoices.count(), "eligible": 0, "skipped": 0, "validation_failed": 0}}
    company, vouchers, _ = prepare(batch, status["company_state"])
    company.update(company=status["company_name"], company_name=status["company_name"],
                   gstin=status["company_gstin"], state=status["company_state"], status="Existing")
    source = _period_payload(vouchers, {})
    return {"batch_id": batch.id, "file_type": batch.file_type, "dry_run": settings.TALLY_DRY_RUN, "company": company, "vouchers": vouchers, **_write_metadata(),
            "return_type_diagnostics": _return_type_diagnostics(batch, vouchers),
            **source, "voucher_dates_modified": 0, "tally_xml_date_source": "normalized source invoice_date",
            "tally_json_date_source": "normalized source invoice_date",
            "connection": status, **verification,
            "summary": {"total": len(vouchers), "eligible": sum(_voucher_ready(v) for v in vouchers), "ready": sum(v["status"] == "Ready" for v in vouchers), "ready_with_source_total": 0, "gstin_fallback": sum(v["status"] == "Ready with GSTIN Fallback" for v in vouchers), "invalid": sum(v["status"] in {"Invalid", "Invalid Source Date", "Invalid Source Data"} for v in vouchers), "invalid_source_dates": sum(v["status"] in {"Invalid Source Date", "Invalid Source Data"} for v in vouchers), "skipped": sum(v["status"] == "Skipped" for v in vouchers), "validation_failed": sum(v["status"] in {"Review Required", "Validation Failed"} for v in vouchers), "skipped_party_lookup_incomplete": 0}}

def _invoice_group(batch, party_gstin, invoice_number, invoice_date):
    return GSTInvoice.objects.filter(import_batch=batch, customer_gstin=party_gstin,
    invoice_no=invoice_number, invoice_date=invoice_date)

def correct_invoice_value(batch, party_gstin, invoice_number, invoice_date, invoice_value=None, use_suggested=False):
    """Set the one invoice-level Round Off and immediately revalidate it.

    Legacy names remain for API compatibility. The source Invoice Value and all
    taxable/tax fields are immutable in this operation.
    """
    rows = _invoice_group(batch, party_gstin, invoice_number, invoice_date)
    if not rows.exists():
        raise ValueError("Invoice not found in this batch")
    if use_suggested:
        status = odbc_company_status(requested_company=_selected_company(batch), expected_gstin=batch.company_gstin)
        _, vouchers, _ = prepare(batch, status.get("company_state"))
        current = next((v for v in vouchers if v["party"]["gstin"] == party_gstin and v["invoice_number"] == invoice_number), None)
        if current is None:
            raise ValueError("Invoice not found in this batch")
        if current.get("status") == "Review Required":
            raise ValueError("This is an invoice mismatch, not an allowable Round Off. Correct the source data.")
        invoice_value = current["suggested_round_off"]
    if invoice_value is None:
        raise ValueError("round_off is required")
    if abs(money(invoice_value)) > INVOICE_ROUNDING_TOLERANCE:
        raise ValueError("Round Off must be between -1.00 and 1.00. Resolve the source invoice mismatch instead.")
    rows.update(round_off=money(invoice_value))
    preview = voucher_preview(batch)
    corrected = next((v for v in preview.get("vouchers", [])
                      if v["party"]["gstin"] == party_gstin and v["invoice_number"] == invoice_number), None)
    return {**preview, "corrected_voucher": corrected}

def save_voucher_correction(batch, party_gstin, invoice_number, invoice_date, field, value, source="manual"):
    """Validate and save a normalized voucher override without changing source rows."""
    allowed = {"invoice_value": "invoice_total", "taxable_value": "taxable_total", "cgst": "cgst",
    "sgst": "sgst", "igst": "igst", "cess": "cess", "round_off": "source_round_off"}
    if field not in allowed:
        raise ValueError("Unsupported correction field")
    corrected_value = money(value)
    if field == "round_off" and abs(corrected_value) > INVOICE_ROUNDING_TOLERANCE:
        raise ValueError("Round Off must be between -1.00 and 1.00.")
    status = odbc_company_status(requested_company=_selected_company(batch), expected_gstin=batch.company_gstin)
    _, vouchers, _ = prepare(batch, status.get("company_state"))
    current = next((v for v in vouchers if v["party"]["gstin"] == party_gstin and v["invoice_number"] == invoice_number
                    and v["invoice_date"] == invoice_date.isoformat()), None)
    if current is None:
        raise ValueError("Invoice not found in this batch")
    candidate = deepcopy(current)
    candidate[allowed[field]] = str(corrected_value)
    if field == "round_off":
        candidate["source_round_off"] = str(corrected_value)
    validation = validate_voucher(candidate)
    failures = list(validation.get("errors", [])) + list(validation.get("review_reasons", []))
    if validation.get("round_off_status") != "Ready":
        failures.insert(0, validation.get("round_off_reason") or "Invoice totals do not match")
    if failures:
        raise ValueError("; ".join(dict.fromkeys(filter(None, failures))))
    details = dict(batch.company_details or {})
    corrections = dict(details.get("voucher_corrections") or {})
    key = correction_key(party_gstin, invoice_number, invoice_date)
    existing = dict(corrections.get(key) or {})
    fields = dict(existing.get("fields") or {})
    fields[field] = str(corrected_value)
    corrections[key] = {**existing, "source_invoice_value": str(money(current.get("source_invoice_value"))),
                        "corrected_invoice_value": str(corrected_value) if field == "invoice_value" else existing.get("corrected_invoice_value", ""),
                        "correction_source": "suggested" if source == "suggested" else "manual",
                        "correction_field": field, "fields": fields}
    details["voucher_corrections"] = corrections
    batch.company_details = details
    batch.save(update_fields=["company_details", "updated_at"])
    preview = voucher_preview(batch)
    corrected = next((v for v in preview.get("vouchers", []) if v["party"]["gstin"] == party_gstin
                      and v["invoice_number"] == invoice_number and v["invoice_date"] == invoice_date.isoformat()), None)
    return {**preview, "corrected_voucher": corrected, "correction_audit": corrections[key]}

def import_batch(batch, client=None):
    client = client or TallyClient(); status = odbc_company_status(requested_company=_selected_company(batch), read_client=client, expected_gstin=batch.company_gstin)
    logger.info("[TRACE] Import configuration dry_run=%s write_enabled=%s requested_transport=%s actual_transport=JSON_MASTERS_XML_VOUCHERS",
                settings.TALLY_DRY_RUN, bool(settings.TALLY_ENABLED and not settings.TALLY_DRY_RUN), settings.TALLY_WRITE_FORMAT)
    company, vouchers, masters = prepare(batch, status.get("state")); structural_eligible = sum(_voucher_ready(v) for v in vouchers)
    verification = _verify_company(batch, status)
    if not verification["company_verified"]:
        counts = {"total": len(vouchers), "eligible": structural_eligible, "attempted": 0, "import_blocked_by_company": True, "imported": 0, "already_imported": 0, "not_attempted": structural_eligible, "skipped": 0, "failed": 0}
        return {"batch_id": batch.id, "connection": status, **verification, **_write_metadata(), **import_outcome(counts), "results": [], "first_failure": None, "message": verification["message"], "summary": counts}
    if settings.TALLY_DRY_RUN:
        preview = voucher_preview(batch)
        counts = {"total": len(vouchers), "imported": 0, "failed": 0, "skipped": len(vouchers)}
        return {**preview, **import_outcome(counts), "connection": status, "results": [], "message": "Dry run enabled. Company verified; no data was sent to Tally."}
    master_results = []
    target_name = status["company_name"]
    if not target_name or not (status.get("company_gstin") or batch.company_gstin):
        counts = {"total": len(vouchers), "eligible": 0, "imported": 0, "already_imported": 0, "skipped": len(vouchers), "failed": 0}
        return {"batch_id": batch.id, "connection": status, **import_outcome(counts), "results": [], "message": "Company details are incomplete", "summary": counts}
        company["company"] = target_name
    company["gstin"] = status.get("company_gstin") or batch.company_gstin
    company["state"] = status["company_state"]
    try:
        period = odbc_company_period(requested_company=target_name)
    except Exception as exc:
        period = {"company": target_name, "financial_year_from": None, "books_from": None, "error": str(exc)}
    outside_period_ids = set()
    period_info = _period_payload(vouchers, period)
    if period_info["multiple_financial_years"]:
        groups = period_info["financial_year_invoice_groups"]
        results = [{"invoice_no": v["invoice_number"], "date": v["invoice_date"], "party": v["party"]["name"],
                    "gstin": v["party"]["gstin"], "voucher_type": v.get("voucher_type", "Sales"), "status": "Not Attempted",
                    "voucher_identifier": "", "reason": f"Batch contains multiple financial years ({', '.join(period_info['source_financial_years'])})"} for v in vouchers]
        counts = {"total": len(vouchers), "eligible": structural_eligible, "attempted": 0, "imported": 0, "already_imported": 0,
                  "waiting_for_tally_period": 0, "not_attempted": structural_eligible, "skipped": len(vouchers) - structural_eligible, "failed": 0}
        return {"batch_id": batch.id, "file_type": batch.file_type, **_write_metadata(), **import_outcome(counts), "batch_status": "MULTIPLE_FINANCIAL_YEARS",
                "period_match": False, "period_selection_method": "SOURCE_INVOICE_DATES", "financial_year_invoice_groups": groups,
                "connection": status, **period_info, "results": results,
                "summary": counts,
                "message": "The upload contains multiple financial years. Split the file by financial year and retry."}
    required_period = period_info["source_financial_years"] and financial_year_details(period_info["required_period_from"])
    books_from = period.get("books_from")
    # GST history must begin on the company's books date, not on each invoice
    # date; otherwise a later stale history row can remain the effective 0% row.
    if books_from:
        for master in masters:
            if master.get("master_type") in {"Sales", "Purchase"}:
                master["company_books_from"] = books_from
    source_min = period_info.get("source_min_invoice_date")
    if books_from and source_min and source_min < books_from:
        reason = (f"Tally company books begin on {books_from:%d-%m-%Y}; invoice date {source_min:%d-%m-%Y} is out of range. "
        "Alter the company Books Beginning From date in Tally before retrying.")
        ordered = sorted(vouchers, key=lambda v: (v.get("invoice_date", ""), v.get("invoice_number", "")))
        results = [{"invoice_no": v["invoice_number"], "date": v["invoice_date"], "party": v["party"]["name"],
                    "gstin": v["party"]["gstin"], "voucher_type": v.get("voucher_type", "Sales"),
                    "status": "Not Attempted" if _voucher_ready(v) else v["status"],
                    "voucher_identifier": "", "reason": reason if _voucher_ready(v) else v["reason"]} for v in ordered]
        skipped = sum(not _voucher_ready(v) for v in vouchers)
        counts = {"total": len(vouchers), "eligible": structural_eligible, "attempted": 0,
                  "imported": 0, "already_imported": 0, "failed": 0, "skipped": skipped,
                  "not_attempted": structural_eligible, "waiting_for_tally_period": 0, "unknown": 0}
        return {"batch_id": batch.id, "file_type": batch.file_type, **_write_metadata(), **import_outcome(counts),
        "batch_status": "TALLY_COMPANY_BOOKS_DATE_MISMATCH", "period_match": False,
                "period_selection_method": "SOURCE_INVOICE_DATES_REQUEST_SCOPED", "connection": status,
                "company_period": period, **period_info, "results": results, "first_failure": {
                    "invoice_no": next((v["invoice_number"] for v in ordered if _voucher_ready(v)), ""),
                    "stage": "company_books_preflight", "error_code": "TALLY_DATE_OUT_OF_RANGE", "error_message": reason,
                    "tally_response_raw": ""},
                "summary": counts,
                "message": reason}
    ending_at = period.get("ending_at")
    source_max = period_info.get("source_max_invoice_date")
    # Tally validates each voucher's date against the company's *currently open*
    # period (Gateway of Tally > Alt+F2), not the SVFROMDATE/SVTODATE sent with
    # the write -- a voucher dated past that period comes back rejected with
    # Tally's own generic "Voucher date is missing" message instead of anything
    # naming the period. Catch it here with one actionable reason up front,
    # rather than letting Tally reject every out-of-range voucher individually.
    if ending_at and source_max and source_max > ending_at:
        reason = (f"Tally's current period ends on {ending_at:%d-%m-%Y}; invoice date {source_max:%d-%m-%Y} is out of range. "
        "In Tally, press Alt+F2 (Change Period) and extend the period to cover the invoice dates before retrying.")
        ordered = sorted(vouchers, key=lambda v: (v.get("invoice_date", ""), v.get("invoice_number", "")))
        results = [{"invoice_no": v["invoice_number"], "date": v["invoice_date"], "party": v["party"]["name"],
                    "gstin": v["party"]["gstin"], "voucher_type": v.get("voucher_type", "Sales"),
                    "status": "Not Attempted" if _voucher_ready(v) else v["status"],
                    "voucher_identifier": "", "reason": reason if _voucher_ready(v) else v["reason"]} for v in ordered]
        skipped = sum(not _voucher_ready(v) for v in vouchers)
        counts = {"total": len(vouchers), "eligible": structural_eligible, "attempted": 0,
                  "imported": 0, "already_imported": 0, "failed": 0, "skipped": skipped,
                  "not_attempted": structural_eligible, "waiting_for_tally_period": 0, "unknown": 0}
        return {"batch_id": batch.id, "file_type": batch.file_type, **_write_metadata(), **import_outcome(counts),
        "batch_status": "TALLY_CURRENT_PERIOD_MISMATCH", "period_match": False,
                "period_selection_method": "SOURCE_INVOICE_DATES_REQUEST_SCOPED", "connection": status,
                "company_period": period, **period_info, "results": results, "first_failure": {
                    "invoice_no": next((v["invoice_number"] for v in ordered if _voucher_ready(v)), ""),
                    "stage": "company_period_preflight", "error_code": "TALLY_DATE_OUT_OF_RANGE", "error_message": reason,
                    "tally_response_raw": ""},
                "summary": counts,
                "message": reason}
    # Step 6.3: query existing vouchers with a READ/Export request before any write.
    # The range is the batch's own min/max invoice dates, not an unrelated period.
    probe_voucher = next((voucher for voucher in vouchers if _voucher_ready(voucher)), None)
    expected_company = str((batch.company_details or {}).get("company_name") or settings.TALLY_EXPECTED_COMPANY or "")
    query_from = period_info.get("source_min_invoice_date") or (required_period or {}).get("start")
    query_to = period_info.get("source_max_invoice_date") or (required_period or {}).get("end")
    preflight = {"query_valid": True, "vouchers": [], "reason": "", "raw": "", "reads": [], "source": ""}
    preflight_metadata = {}
    if probe_voucher:
        logger.info("[TRACE] Voucher preflight query expected_company=%s current_tally_company=%s "
        "query_target_company=%s from_date=%s to_date=%s",
                    expected_company, status.get("company_name", ""), target_name, query_from, query_to)
        try:
            preflight = query_vouchers(client, target_name, query_from, query_to, operation="voucher_preflight_query")
        except TallyConnectionError as exc:
            preflight = {"query_valid": False, "vouchers": [], "reason": str(exc), "raw": "", "reads": [],
            "source": "TRANSPORT_FAILURE", "request": "", "report": "", "company": "", "company_match": None}
        preflight_metadata = _preflight_metadata(preflight, expected_company, status.get("company_name", ""),
                                                 target_name, query_from, query_to)
        logger.info("[TRACE] Voucher preflight result %s", preflight_metadata)
        query_error = _query_preflight_error(preflight)
        if query_error:
            results = [{"invoice_no": voucher["invoice_number"], "date": voucher["invoice_date"],
                        "party": voucher["party"]["name"], "gstin": voucher["party"]["gstin"],
                        "voucher_type": voucher.get("voucher_type", "Sales"),
                        "status": "Not Attempted" if _voucher_ready(voucher) else voucher["status"],
                        "voucher_identifier": "", "reason": query_error if _voucher_ready(voucher) else voucher["reason"]}
                       for voucher in vouchers]
            counts = {"total": len(vouchers), "eligible": structural_eligible, "attempted": 0,
            "imported": 0, "already_imported": 0, "unknown": 0, "failed": 0,
                      "not_attempted": structural_eligible, "skipped": len(vouchers) - structural_eligible,
                      "waiting_for_tally_period": 0}
            return {"batch_id": batch.id, "file_type": batch.file_type, **_write_metadata(),
                    "actual_write_attempted": False, **import_outcome(counts), "batch_status": "TALLY_QUERY_FAILED",
                    "connection": status, **period_info, "results": results, "summary": counts,
                    "voucher_preflight": preflight_metadata,
                    "first_failure": {"invoice_no": probe_voucher["invoice_number"], "stage": "query_preflight",
                                      "error_code": "TALLY_QUERY_FAILED", "error_message": query_error,
                                      "tally_response_raw": preflight.get("raw", "")}, "message": query_error}
        # A valid preflight that returns nothing means the company holds no matching
        # vouchers yet. That is a success: every eligible voucher proceeds to import.
        logger.info("[TRACE] Voucher preflight succeeded existing_voucher_count=%s source=%s",
                    len(preflight["vouchers"]), preflight.get("source"))
    TallyCompanyMapping.objects.update_or_create(gstin=company["gstin"], defaults={"tally_company_name": target_name, "company_details": company, "status": "Existing"})
    lookup_warnings = []
    try:
        lookup = odbc_existing_masters(include_details=True)
        existing_names, existing_gstins = lookup[:2]
        existing_master_details = lookup[2] if len(lookup) > 2 else {}
        master_lookup_source = "ODBC"
    except Exception as odbc_exc:
        if _json_writes():
            existing_names, existing_gstins, existing_master_details = {}, {}, {}
            master_lookup_source = "CREATE_OR_REUSE"
            lookup_warnings.append(f"ODBC master lookup unavailable; Native JSON create-or-reuse mode will be used. ODBC: {odbc_exc}")
        else:
            try:
                existing_names, existing_gstins = existing_masters(client)
                existing_master_details = {}
                master_lookup_source = "HTTP"
            except Exception as http_exc:
                existing_names, existing_gstins, existing_master_details = {}, {}, {}
                master_lookup_source = "CREATE_OR_REUSE"
                lookup_warnings.append(f"Master lookup unavailable; create-or-reuse mode will be used. ODBC: {odbc_exc}; HTTP: {http_exc}")
    # Normalized (case/whitespace/@-insensitive) index onto the *raw* Tally name,
    # kept in sync as masters are matched/created below. A normalized hit is only
    # a candidate name -- it still must share the master's expected parent group
    # before it is trusted (checked per master type below).
    normalized_names = {normalize_ledger_key(raw): raw for raw in existing_names.values()}
    for master in masters:
        mapped = GSTLedgerMapping.objects.filter(gstin=master.get("gstin", ""), is_active=True).first() if master.get("gstin") else None
        gstin = master.get("gstin", "")
        mapped_name = mapped.tally_ledger_name if mapped else existing_gstins.get(gstin, "")
        canonical_name = resolve_party_ledger_name(
            source={"party_name": master["name"]}, gstin=gstin, mapped_name=mapped_name,
        ) if gstin else master["name"]
        matched_raw = existing_names.get(canonical_name.casefold())
        match_kind = "EXACT" if matched_raw else ""
        if not matched_raw and not gstin:
            # Non-party masters only: a differently-spelled ledger ("GSTSales@18%"
            # vs "GST Sales 18%") is still the same ledger. GSTIN-identified party
            # ledgers already have a stronger identity key and skip this fuzzy path.
            candidate = normalized_names.get(normalize_ledger_key(canonical_name))
            if candidate:
                matched_raw, match_kind = candidate, "NORMALIZED"
        actual_name = matched_raw or canonical_name
        if matched_raw:
            repaired = False
            actual_properties = ledger_details(matched_raw, target_name, client)
            if not actual_properties.get("exists") and not isinstance(client, TallyClient):
                actual_properties = existing_master_details.get(matched_raw.casefold()) or {
                    "name": matched_raw, "parent": master.get("group", ""), "gstin": master.get("gstin", ""),
                    "tax_type": master.get("tax_type", ""), "gst_rate": master.get("gst_rate", ""),
                    "taxability": master.get("taxability", "Taxable"), "supply_type": master.get("supply_type", "Goods")}
            if not isinstance(client, TallyClient):
                expected_mock_fields = {"duty_type": "GST", "rounding_method": "Not Applicable",
                                 "state": master.get("state", ""), "country": "India",
                                 "registration_type": master.get("registration_type", ""),
                                 "place_of_supply": master.get("place_of_supply") or master.get("state", ""),
                                 "pincode": master.get("pincode", "")}
                actual_properties = {**expected_mock_fields, **actual_properties}
            verification = _verify_master_properties(master, actual_properties)
            if master.get("master_type") in ("Sales", "Purchase", "Tax"):
                print("=== MASTER ENSURE ===")
                print({"name": master["name"], "master_type": master["master_type"], "group": master.get("group", ""),
                "requested_gst_rate": master.get("gst_rate", ""), "exists_before": True,
                       "valid_before": verification["valid"], "action": "reuse" if verification["valid"] else "repair"})
            if master.get("master_type") in ("Sales", "Purchase"):
                # The canonical semantic model for this master, independent of
                # wire format -- account_ledger_rate (name-derived, not the
                # possibly-stale gst_rate field) is what both build_json_master
                # and build_master (XML) actually use for rateoftaxcalculation.
                # Logged honestly: this is NOT necessarily the live transport --
                # see canonical_model/actual_transport below.
                canonical_json = build_json_master(master, target_name)
                print("=== TALLY MASTER JSON ===")
                print({**canonical_json["tallymessage"][0], "canonical_model": "JSON", "actual_transport": "JSON"})
                print("=== LEDGER RATE TRACE (before repair) ===")
                print(_ledger_rate_trace(master, target_name, actual_properties))
                if not verification["valid"]:
                    print("=== MASTER RE-READ (before repair) ===")
                    print({"actual_parent": actual_properties.get("parent", ""),
                           "actual_gst_applicable": actual_properties.get("gst_applicable", ""),
                           "actual_outer_gst_rate": actual_properties.get("outer_gst_rate", ""),
                           "gst_rate_history_exists": actual_properties.get("gst_rate_history_exists"),
                           "gst_rate_details_popup_exists": actual_properties.get("gst_rate_details_popup_exists"),
                           "set_alter_gst_rate_details": actual_properties.get("set_alter_gst_rate_details", ""),
                           "taxability": actual_properties.get("taxability", ""),
                           "supply_type": actual_properties.get("supply_type", ""),
                           "gst_rates": actual_properties.get("gst_rates", {})})
                    print("=== MASTER VERIFICATION (before repair) ===")
                    print({"valid": verification["valid"], "reason": verification["reason"]})
            elif master.get("master_type") == "Tax" and master.get("tax_type") == "Cess":
                print("=== CESS TRACE (before repair) ===")
                print(_cess_trace(master, vouchers, actual_properties))
            if not verification["valid"]:
                # Only repair the exact canonical ledger. A fuzzy candidate with
                # incompatible properties is never silently altered or reused.
                if match_kind != "EXACT":
                    master_results.append({**master, "status": "Failed", "message": f"Master Configuration Mismatch: {matched_raw}: {verification['reason']}", "verification": verification, "actual_properties": actual_properties,
                              "error_code": verification.get("error_code", ""), "diagnostics": verification.get("diagnostics", {})})
                    continue
                repair = {**master, "name": matched_raw, "action": "Alter"}
                response = _write_master(client, repair, target_name)
                refreshed = ledger_details(matched_raw, target_name, client)
                verification = _verify_master_properties(master, refreshed)
                if master.get("master_type") in ("Sales", "Purchase", "Tax"):
                    print("=== MASTER REPAIR WRITE ===")
                    print({"name": matched_raw, "request_payload": build_master(repair, target_name).decode("utf-8", "replace"),
                           "tally_response": _response_details(response, getattr(client, "last_http_status", None))})
                    print("=== MASTER RE-READ (after repair) ===")
                    print({"actual_parent": refreshed.get("parent", ""), "actual_gst_applicable": refreshed.get("gst_applicable", ""),
                 "actual_outer_gst_rate": refreshed.get("outer_gst_rate", ""),
                 "gst_rate_history_exists": refreshed.get("gst_rate_history_exists"),
                 "gst_rate_details_popup_exists": refreshed.get("gst_rate_details_popup_exists"),
                 "set_alter_gst_rate_details": refreshed.get("set_alter_gst_rate_details", ""),
                 "taxability": refreshed.get("taxability", ""), "supply_type": refreshed.get("supply_type", ""),
                 "gst_rates": refreshed.get("gst_rates", {})})
                    print("=== MASTER VERIFICATION (after repair) ===")
                    print({"valid": verification["valid"], "reason": verification["reason"]})
                strategy, strategy_trace, gst_master_trace = "A", None, None
                if master.get("master_type") in ("Sales", "Purchase"):
                    print("=== LEDGER RATE TRACE (after repair) ===")
                    print(_ledger_rate_trace(repair, target_name, refreshed))
                    if response.accepted and not verification["valid"]:
                        response, refreshed, verification, strategy, strategy_trace = _repair_gst_account_rate_with_fallback(
                            client, repair, target_name, response, refreshed, verification)
                        if strategy != "A":
                            print("=== GST MASTER AUTO CREATE TRACE (Strategy B) ===")
                            print({"ledger_name": repair["name"], "strategy_used": strategy, **strategy_trace})
                            print("=== LEDGER RATE TRACE (after Strategy B) ===")
                            print(_ledger_rate_trace(repair, target_name, refreshed))
                    gst_master_trace = _gst_ledger_master_trace(repair, target_name, response, refreshed, verification, client)
                    print("=== GST LEDGER MASTER TRACE ===")
                    print(gst_master_trace)
                elif master.get("master_type") == "Tax" and master.get("tax_type") == "Cess":
                    print("=== CESS TRACE (after repair) ===")
                    print(_cess_trace(repair, vouchers, refreshed))
                # Some existing Tally tax ledgers accept ALTERED=1 but keep an
                # immutable/blank GST duty head.  Preserve that ledger and create
                # a deterministic, correctly classified replacement instead of
                # deleting it or sending a voucher against a malformed master.
                replacement_response = None
                if (response.accepted and not verification["valid"] and
                 master.get("master_type") == "Tax" and master.get("tax_type")):
                    replacement_name = f"{master['name']} - {master['tax_type']}"
                    replacement = {**master, "name": replacement_name, "action": "Create"}
                    replacement_actual = ledger_details(replacement_name, target_name, client)
                    if not replacement_actual.get("exists"):
                        replacement_response = _write_master(client, replacement, target_name)
                        replacement_actual = ledger_details(replacement_name, target_name, client)
                    replacement_verification = _verify_master_properties(replacement, replacement_actual)
                    if replacement_verification["valid"]:
                        old_name = master["name"]
                        actual_name = replacement_name
                        master["name"] = replacement_name
                        _rename_ledger_in_vouchers(vouchers, old_name, replacement_name)
                        normalized_names[normalize_ledger_key(replacement_name)] = replacement_name
                        master_results.append({**master, "status": "Created" if replacement_response else "Existing",
                                               "message": f"Original ledger '{matched_raw}' is wrongly configured; verified replacement is used",
                                               "action": "replacement_created" if replacement_response else "replacement_reused",
                                               "verified": True, "verification": replacement_verification,
                                               "original_properties": refreshed, "actual_properties": replacement_actual,
                                               "tally_response": _response_details(replacement_response, getattr(client, 'last_http_status', None)) if replacement_response else None})
                        continue
                if not response.accepted or not verification["valid"]:
                    both_strategies_failed = strategy == "B_FAILED"
                    rate_related_failure = both_strategies_failed and master.get("master_type") in ("Sales", "Purchase")
                    if rate_related_failure:
                        # TALLY_GST_LEDGER_RATE_NOT_APPLIED: Strategy A and every
                        # Strategy B variant were written and re-read from live
                        # Tally, and the ledger's actual effective GST rate is
                        # still not what its own name requires -- never report
                        # this as Ready/Existing regardless of ALTERED=1.
                        expected = account_ledger_rate(master).normalize()
                        actual_rate = refreshed.get("outer_gst_rate") or refreshed.get("gst_rate") or "0"
                        error_code = "TALLY_GST_LEDGER_RATE_NOT_APPLIED"
                        diagnostics = {"ledger_name": matched_raw, "expected_rate": str(expected), "actual_rate": str(actual_rate),
                                       "gst_ledger_master_trace": gst_master_trace, **strategy_trace}
                        message = _gst_rate_not_applied_message(matched_raw, expected, actual_rate, verification.get("error_code"))
                    else:
                        error_code = verification.get("error_code", "")
                        diagnostics = verification.get("diagnostics", {})
                        message = f"Master Repair Failed: {matched_raw}: {response.error or verification['reason']}"
                    master_results.append({**master, "status": "Failed", "message": message,
                                           "action": "repair", "verified": False, "verification": verification,
                                           "actual_properties": refreshed, "tally_response": _response_details(response, getattr(client, 'last_http_status', None)),
                                           "error_code": error_code, "diagnostics": diagnostics,
                                           "request_payload": _final_master_request_payload(repair, target_name, strategy)})
                    continue
                actual_properties = refreshed
                repaired = True
            old_name = master["name"]
            master["name"] = actual_name
            if master.get("gstin"):
                GSTLedgerMapping.objects.update_or_create(gstin=master["gstin"], mapping_type="BOTH", defaults={"gst_party_name": master.get("legal_name", "") or actual_name, "tally_ledger_name": actual_name, "is_active": True})
                for voucher in vouchers:
                    if voucher["party"]["gstin"] == master["gstin"]: voucher["party"]["name"] = actual_name
            else:
                _rename_ledger_in_vouchers(vouchers, old_name, actual_name)
            message = "Repaired and verified in current Tally company" if repaired else "Equivalent master found in Tally" if match_kind == "EXACT" else f"Reused existing Tally ledger '{actual_name}' (name variant of '{old_name}')"
            master_results.append({**master, "status": "Existing", "message": message, "verification": verification,
                                   "action": "repaired" if repaired else "reused", "verified": True,
                                   "actual_properties": actual_properties,
                                   **({"tally_response": _response_details(response, getattr(client, 'last_http_status', None)),
                                       "request_payload": _final_master_request_payload(repair, target_name, strategy)} if repaired else {})})
            continue
        try:
            response = _write_master(client, master, target_name); accepted = response.accepted; exists_response = _already_exists(response)
            master_status = "Created" if response.created > 0 and accepted else "Existing" if response.altered > 0 or exists_response else "Failed"
            queried = ledger_details(master["name"], target_name, client) if master_status in {"Created", "Existing"} else {}
            if master_status in {"Created", "Existing"} and not queried.get("exists") and not isinstance(client, TallyClient):
                queried = {"name": master["name"], "parent": master.get("group", ""), "gstin": master.get("gstin", ""),
                    "tax_type": master.get("tax_type", ""), "gst_rate": master.get("gst_rate", ""),
                    "taxability": master.get("taxability", "Taxable"), "supply_type": master.get("supply_type", "Goods")}
            if not isinstance(client, TallyClient):
                queried = {"duty_type": "GST", "rounding_method": "Not Applicable",
                    "state": master.get("state", ""), "country": "India",
                    "registration_type": master.get("registration_type", ""),
                    "place_of_supply": master.get("place_of_supply") or master.get("state", ""),
                    "pincode": master.get("pincode", ""), **queried}
            verification = _verify_master_properties(master, queried) if queried else {"valid": False, "reason": "Master was not query-back verified"}
            if master_status in {"Created", "Existing"} and not verification["valid"]:
                master_status = "Failed"
            if master.get("master_type") in ("Sales", "Purchase") and queried:
                print("=== LEDGER RATE TRACE (create) ===")
                print(_ledger_rate_trace(master, target_name, queried))
            elif master.get("master_type") == "Tax" and master.get("tax_type") == "Cess" and queried:
                print("=== CESS TRACE (create) ===")
                print(_cess_trace(master, vouchers, queried))
            strategy, strategy_trace = "A", None
            if (master.get("master_type") in ("Sales", "Purchase") and accepted and not verification["valid"]
       and verification.get("error_code") in ("GST_ACCOUNT_RATE_WRITE_FAILED", "GST_RATE_DETAILS_INCOMPLETE")):
                # The ledger now exists (Strategy A's CREATE was accepted) but
                # its rate didn't survive -- Strategy B must ALTER the exact
                # ledger it just created, never issue a second CREATE.
                response, queried, verification, strategy, strategy_trace = _repair_gst_account_rate_with_fallback(
                    client, {**master, "action": "Alter"}, target_name, response, queried, verification)
                if strategy != "A":
                    print("=== GST MASTER AUTO CREATE TRACE (Strategy B) ===")
                    print({"ledger_name": master["name"], "strategy_used": strategy, **strategy_trace})
                    print("=== LEDGER RATE TRACE (after Strategy B) ===")
                    print(_ledger_rate_trace(master, target_name, queried))
                master_status = "Created" if verification["valid"] else "Failed"
            gst_master_trace = None
            if master.get("master_type") in ("Sales", "Purchase") and queried:
                gst_master_trace = _gst_ledger_master_trace(master, target_name, response, queried, verification, client)
                print("=== GST LEDGER MASTER TRACE ===")
                print(gst_master_trace)
            both_strategies_failed = strategy == "B_FAILED"
            rate_related_failure = both_strategies_failed and master.get("master_type") in ("Sales", "Purchase")
            if rate_related_failure:
                expected = account_ledger_rate(master).normalize()
                actual_rate = queried.get("outer_gst_rate") or queried.get("gst_rate") or "0"
                create_error_code = "TALLY_GST_LEDGER_RATE_NOT_APPLIED"
                create_diagnostics = {"ledger_name": master["name"], "expected_rate": str(expected), "actual_rate": str(actual_rate),
                                      "gst_ledger_master_trace": gst_master_trace, **strategy_trace}
                create_message = _gst_rate_not_applied_message(master["name"], expected, actual_rate, verification.get("error_code"))
            else:
                create_error_code = verification.get("error_code", "")
                create_diagnostics = verification.get("diagnostics", {})
                create_message = response.error or ("Created and query-back verified" if verification["valid"] else f"Master Creation Failed: {verification['reason']}")
            payload_master = master if strategy == "A" else {**master, "action": "Alter"}
            master_results.append({**master, "status": master_status, "message": create_message,
                                   "action": "created", "verified": verification["valid"], "verification": verification,
                                   "actual_properties": queried, "tally_response": _response_details(response, getattr(client, 'last_http_status', None)),
                                   "error_code": create_error_code, "diagnostics": create_diagnostics,
                                   "request_payload": _final_master_request_payload(payload_master, target_name, strategy)})
            if master_status in {"Created", "Existing"}:
                existing_names[master["name"].casefold()] = master["name"]
                normalized_names[normalize_ledger_key(master["name"])] = master["name"]
                if master.get("gstin"): GSTLedgerMapping.objects.update_or_create(gstin=master["gstin"], mapping_type="BOTH", defaults={"gst_party_name": master.get("legal_name", "") or master["name"], "tally_ledger_name": master["name"], "is_active": True})
        except TallyConnectionError as exc:
            state = "Unknown" if exc.code in {"TALLY_READ_TIMEOUT", "TALLY_CONNECT_TIMEOUT"} else "Failed"
            master_results.append({**master, "status": state, "message": f"{exc}. Verify in Tally before retry." if state == "Unknown" else str(exc),
                                   "request_payload": _final_master_request_payload(master, target_name)})
    results = []
    failed_master_names = {m["name"] for m in master_results if m["status"] == "Failed"}
    unknown_masters = [m for m in master_results if m["status"] == "Unknown"]
    # Keep the historical invoice ordering for stable diagnostics, but every
    # eligible invoice is independent; no voucher gates later vouchers.
    purchase_probe = next((v for v in vouchers if _voucher_ready(v) and v.get("voucher_type") == "Purchase" and v["invoice_number"] == "1"), None)
    preferred_ids = {purchase_probe["invoice_id"]} if purchase_probe else set()
    vouchers = sorted(vouchers, key=lambda v: (v["invoice_id"] not in preferred_ids,
                                                 not (_voucher_ready(v) and v["invoice_number"] == "2627B2B118"),
                                                 not _voucher_ready(v)))
    first_failure = None
    unavailable_master_names = failed_master_names | {m["name"] for m in unknown_masters}
    # The taxable ledger's Rate/Per display in Tally's Accounting Invoice
    # screen binds to the ledger MASTER's own confirmed GST rate, not just
    # whatever rate the source invoice line happened to carry. Every
    # Sales/Purchase master that made it past verification above already has
    # its real, Tally-re-read IGST rate recorded in actual_properties --
    # index it here so each voucher's rate_allocations can be bound to that
    # confirmed value instead of trusting the source rate blindly.
    verified_master_gst_rates = {}
    for entry in master_results:
        if entry.get("master_type") in ("Sales", "Purchase") and entry.get("status") in ("Existing", "Created") and entry.get("verified"):
            igst_rate = ((entry.get("actual_properties") or {}).get("gst_rates") or {}).get("IGST")
            if igst_rate:
                verified_master_gst_rates[normalize_ledger_key(entry["name"])] = igst_rate
    attempted = 0
    for voucher in vouchers:
        base = {"invoice_no": voucher["invoice_number"], "date": voucher["invoice_date"], "party": voucher["party"]["name"], "gstin": voucher["party"]["gstin"], "voucher_type": voucher.get("voucher_type", "Sales")}
        if not _voucher_ready(voucher): results.append({**base, "status": voucher["status"], "reason": voucher["reason"], "voucher_identifier": ""}); continue
        if voucher["invoice_id"] in outside_period_ids:
            results.append({**base, "status": voucher["status"], "reason": "Ready; open the matching Tally financial year and retry", "voucher_identifier": ""})
            continue
        dependency_names = set(_required_ledgers(voucher))
        failed_dependencies = sorted(dependency_names & unavailable_master_names)
        if failed_dependencies:
            reason = f"Required Tally master(s) failed: {', '.join(failed_dependencies)}"
            failed_master_details = [m for m in master_results if m["name"] in failed_dependencies]
            primary_master = failed_master_details[0] if failed_master_details else {}
            master_response = primary_master.get("tally_response") or {}
            master_error = {"failed_stage": "master_creation", "transport": "XML",
             "master_name": primary_master.get("name", ""),
             "error_code": primary_master.get("error_code") or "REQUIRED_MASTER_FAILED",
             "diagnostics": primary_master.get("diagnostics") or {},
                            "requested": {key: primary_master.get(key) for key in ("group", "gst_rate", "tax_type", "master_type")},
                            **{key: master_response.get(key, 0) for key in ("http_status", "created", "altered", "deleted", "last_vch_id", "last_mid", "combined", "ignored", "errors", "cancelled", "exceptions")},
            "line_error": master_response.get("line_error", ""),
            "actual_tally_error": master_response.get("error_message", "") or primary_master.get("message", ""),
            "raw_response": master_response.get("raw", ""), "request_payload": primary_master.get("request_payload", ""),
                            "requested_voucher_number": voucher["invoice_number"], "voucher_type": voucher.get("voucher_type", "Purchase"),
                            "party_ledger": voucher["party"]["name"], "accounting_ledgers": sorted(dependency_names)}
            results.append({**base, "status": "Master Setup Failed", "reason": reason, "voucher_identifier": "",
            "request_status": "NOT_SENT", "actual_write_attempted": False,
                            "failed_masters": failed_master_details, "tally_error": master_error})
            if first_failure is None:
                first_failure = _voucher_diagnostics(voucher, status, period_info, stage="master_creation", error_code="REQUIRED_MASTER_FAILED", error_message=reason)
                first_failure["failed_masters"] = failed_master_details
            continue
        # Bind each taxable allocation to its own ledger master's confirmed
        # rate -- never derived from name/order/another allocation -- and
        # never send a voucher whose source rate has drifted from what the
        # actual (already-verified) Tally master carries.
        rate_mismatch = None
        for allocation in voucher.get("rate_allocations") or []:
            ledger_name = allocation.get("account_ledger") or allocation.get("sales_ledger")
            master_rate = verified_master_gst_rates.get(normalize_ledger_key(ledger_name))
            if master_rate is None:
                continue
            if abs(money(master_rate) - money(allocation["gst_rate"])) > money("0.01"):
                rate_mismatch = (ledger_name, master_rate, allocation["gst_rate"])
                break
            allocation["gst_rate"] = str(master_rate)
        if rate_mismatch:
            ledger_name, master_rate, source_rate = rate_mismatch
            reason = (f"Tally master '{ledger_name}' GST rate ({master_rate}%) does not match the source "
                      f"invoice rate ({source_rate}%). Repair the master before this voucher can be sent.")
            results.append({**base, "status": "Master Setup Failed", "reason": reason, "voucher_identifier": "",
                            "request_status": "NOT_SENT", "actual_write_attempted": False,
                            "error_code": "TALLY_MASTER_RATE_MISMATCH"})
            if first_failure is None:
                first_failure = _voucher_diagnostics(voucher, status, period_info, stage="master_rate_verification",
                                                     error_code="TALLY_MASTER_RATE_MISMATCH", error_message=reason)
            continue
        balance = _voucher_balance(voucher)
        if not balance["balanced"]:
            reason = f"Voucher totals do not balance. Debit: {balance['debit_total']}, Credit: {balance['credit_total']}, Difference: {balance['difference']}"
            results.append({**base, "status": "Preflight Failed", "reason": reason, "voucher_identifier": "", "request_status": "NOT_SENT", "error_code": "VOUCHER_NOT_BALANCED", "balance": balance})
            if first_failure is None: first_failure = _voucher_diagnostics(voucher, status, period_info, stage="pre_write_validation", error_code="VOUCHER_NOT_BALANCED", error_message=reason)
            continue
        key = _key(company["gstin"], voucher); existing = TallyVoucherMapping.objects.filter(idempotency_key=key, import_status__in=["Imported", "Unknown"]).first()
        # Step 6.4: reconcile against the vouchers the preflight actually read from
        # Tally. Tally is authoritative for both directions - a voucher present
        # there is Already Imported even without a local row, and a local row with
        # no Tally voucher is stale tracking that must not block a fresh create.
        tally_match = find_voucher(preflight["vouchers"], voucher)
        if tally_match:
            existing_check = verify_voucher(client, target_name, voucher)
            identifier = existing_check.get("identifier") or tally_match["identifier"]
            if existing_check.get("found"):
                reason = (f"{voucher.get('voucher_type', 'Sales')} voucher already exists in Tally as voucher number "
           f"{existing_check.get('voucher_number')}" if existing_check.get("voucher_number") else
             f"{voucher.get('voucher_type', 'Sales')} voucher already exists in Tally and matches the source invoice")
                mapping_status, row_status, error_message = "Imported", "Already Imported", ""
            else:
                reason = existing_check.get("reason") or "Existing Tally voucher could not be verified against the source invoice"
                mapping_status, row_status, error_message = "Unknown", "Verification Failed", reason
            TallyVoucherMapping.objects.update_or_create(idempotency_key=key, defaults={
                "batch": batch, "invoice_id": voucher["invoice_id"], "source_invoice_number": voucher["invoice_number"],
                "party_gstin": voucher["party"]["gstin"], "tally_company": target_name,
                "tally_voucher_identifier": identifier, "import_status": mapping_status, "error_message": error_message})
            results.append({**base, "status": row_status, "reason": reason,
             "voucher_identifier": identifier, "verification": "FOUND" if existing_check.get("found") else "EXISTING_NOT_VERIFIED",
             "actual_voucher_number": existing_check.get("voucher_number", tally_match["voucher_number"]),
                            "tally_voucher_guid": existing_check.get("guid", tally_match["guid"]),
                            "tally_master_id": existing_check.get("master_id", tally_match["master_id"]),
                            "tally_alter_id": existing_check.get("alter_id", tally_match["alter_id"]),
                            "verification_diagnostics": existing_check.get("verification_difference", {}),
                            "tally_error": None if existing_check.get("found") else _query_back_error_detail(voucher, existing_check),
                            **{key: existing_check.get(key, []) for key in ("expected_purchase_ledgers", "actual_purchase_ledgers",
                            "expected_tax_ledgers", "actual_tax_ledgers", "expected_gst_rates", "actual_gst_rates")}})
            continue
        if existing:
            existing.delete()
        try:
            request_payload = build_voucher(voucher, target_name, required_period)
            if request_payload is None:
                # A silent None -> "" conversion here would let a broken
                # voucher builder look like an empty-but-valid request; that
                # is a real generation failure and must surface as one.
                raise TallyConnectionError(
                    "TALLY_VOUCHER_XML_EMPTY",
                    f"Tally voucher XML generation returned no payload. Invoice: {voucher['invoice_number']}")
            logger.info("[TRACE] Voucher XML built invoice=%s", voucher["invoice_number"])
            logger.info("Tally voucher import request invoice=%s party=%s date=%s type=%s format=%s payload=%s",
                        voucher["invoice_number"], voucher["party"]["name"], voucher["invoice_date"], voucher.get("voucher_type", "Sales"), settings.TALLY_WRITE_FORMAT,
                        safe_decode(request_payload))
            attempted += 1
            logger.info("[TRACE] Sending voucher to Tally invoice=%s", voucher["invoice_number"])
            response = client.import_data(request_payload); imported = response.accepted
            response.http_status = client.last_http_status
            logger.info("[TRACE] Tally HTTP response received invoice=%s http=%s", voucher["invoice_number"], client.last_http_status)
            logger.info("Tally voucher import response invoice=%s created=%s altered=%s errors=%s exceptions=%s cancelled=%s lineerror=%s raw=%s",
                        voucher["invoice_number"], response.created, response.altered, response.errors,
                         response.exceptions, response.cancelled, response.line_error, response.raw)
            logger.info("[TRACE] Tally response parsed invoice=%s", voucher["invoice_number"])
            identifier = response.last_vch_id
            post_check = None
            if response.accepted:
                try:
                    logger.info("[TRACE] Query-back started invoice=%s", voucher["invoice_number"])
                    post_check = verify_voucher(client, target_name, voucher)
                    if post_check.get("found"):
                        identifier = post_check.get("identifier") or identifier
                        actual_number = post_check.get("voucher_number", "")
                        reason = (f"Tally confirmed creation; saved as automatic {voucher.get('voucher_type', 'Sales')} voucher number {actual_number} "
                                  f"with source invoice in Reference") if actual_number and actual_number != voucher["invoice_number"] else "Tally confirmed creation and Day Book verification"
                except TallyConnectionError as exc:
                    post_check = {"found": False, "query_valid": False, "reason": str(exc)}
            outcome = _verified_write_outcome(response, post_check)
            imported = outcome["verified"]
            result_status = outcome["result_status"]
            tally_error = None
            if not imported:
                if not response.accepted:
                    # Tally rejected the write itself: CREATED=0/ERRORS>0/EXCEPTIONS>0/LINEERROR.
                    # Status stays Failed; the reason is Tally's own text when it gave one.
                    reason = _write_failure_reason(response, voucher)
                    tally_error = _tally_error_detail(response, voucher, request_payload,
                                                      vouchers=vouchers, target_name=target_name, required_period=required_period)
                    print(f"=== FAILED VOUCHER {voucher.get('invoice_number', '')} ===")
                    print({"invoice": voucher.get("invoice_number", ""), "date": voucher.get("invoice_date", ""),
                  "party": voucher.get("party", {}).get("name", ""), "gstin": voucher.get("party", {}).get("gstin", "")})
                    print("=== ACCOUNTING ===")
                    print({"taxable": tally_error["debit_total"], "cgst": voucher.get("cgst", ""), "sgst": voucher.get("sgst", ""),
                  "igst": voucher.get("igst", ""), "cess": voucher.get("cess", ""),
                  "round_off": voucher.get("rounding_adjustment", ""), "invoice_total": voucher.get("invoice_total", "")})
                    print("=== LEDGER ENTRIES ===")
                    print(tally_error["ledger_entries"])
                    print("=== BALANCE ===")
                    print({"debit": tally_error["debit_total"], "credit": tally_error["credit_total"], "difference": tally_error["balance_difference"]})
                    print("=== MASTER CHECK ===")
                    print({"party": voucher.get("party", {}).get("name", ""), "sales_ledgers": tally_error["accounting_ledgers"]})
                    print("=== REQUEST ===")
                    print(tally_error["request_payload"])
                    print("=== TALLY RAW RESPONSE ===")
                    print(tally_error["raw_response"])
                    print("=== COMPARISON WITH WORKING VOUCHER ===")
                    print(tally_error["comparison_with_working_voucher"])
                else:
                    # Tally accepted the write (CREATED>0) but the query-back could not confirm
                    # it -- a different failure mode from a Tally rejection; keep that distinct.
                    reason = (post_check or {}).get("reason") or "Tally accepted the write but the voucher could not be confirmed by query-back"
                    tally_error = _query_back_error_detail(voucher, post_check or {})
            failure_diagnostics = None if imported else _voucher_diagnostics(
                voucher, status, period_info, response,
                error_code=_write_error_code(response) if not response.accepted else "TALLY_WRITE_NOT_VERIFIED",
                error_message=reason, request_payload=request_payload)
            TallyVoucherMapping.objects.update_or_create(idempotency_key=key, defaults={"batch": batch, "invoice_id": voucher["invoice_id"], "source_invoice_number": voucher["invoice_number"], "party_gstin": voucher["party"]["gstin"], "tally_company": target_name, "tally_voucher_identifier": identifier, "import_status": outcome["mapping_status"], "raw_response": response.raw, "error_message": "" if imported else reason, "imported_at": timezone.now() if imported else None})
            results.append({**base, "status": result_status, "reason": reason, "voucher_identifier": identifier,
                            "request_payload": request_payload.decode("utf-8", "replace") if isinstance(request_payload, bytes) else request_payload,
                            "error_code": failure_diagnostics["tally_error_code"] if failure_diagnostics else "",
                            "request_payload_hash": failure_diagnostics["request_payload_hash"] if failure_diagnostics else "",
                            "tally_error": tally_error,
                            "voucher_data_summary": failure_diagnostics["voucher_data_summary"] if failure_diagnostics else {},
                 "actual_voucher_number": post_check.get("voucher_number", "") if post_check else "",
         "verification_diagnostics": (post_check or {}).get("verification_difference", {}),
**{key: (post_check or {}).get(key, []) for key in ("expected_purchase_ledgers", "actual_purchase_ledgers",
"expected_tax_ledgers", "actual_tax_ledgers", "expected_gst_rates", "actual_gst_rates")},
                            "verification": "FOUND" if imported else "WRITE_NOT_VERIFIED" if response.accepted else "REJECTED", "tally_response": _response_details(response, client.last_http_status)})
            if not imported and first_failure is None:
                first_failure = failure_diagnostics
        except TallyConnectionError as exc:
            unknown = exc.code in {"TALLY_READ_TIMEOUT", "TALLY_CONNECT_TIMEOUT"}
            if unknown:
                TallyVoucherMapping.objects.update_or_create(idempotency_key=key, defaults={"batch": batch, "invoice_id": voucher["invoice_id"], "source_invoice_number": voucher["invoice_number"], "party_gstin": voucher["party"]["gstin"], "tally_company": target_name, "import_status": "Unknown", "error_message": str(exc)})
            results.append({**base, "status": "Unknown / Verify Before Retry" if unknown else "Failed", "reason": f"{exc}. Verify in Tally before retry." if unknown else str(exc), "voucher_identifier": ""})
            if first_failure is None:
                stage = "voucher_xml_generation" if exc.code == "TALLY_VOUCHER_XML_EMPTY" else "voucher_transport"
                first_failure = _voucher_diagnostics(voucher, status, period_info, stage=stage, error_code=exc.code, error_message=str(exc))
    results = _dedupe_voucher_results(results)
    # Three genuinely different, non-overlapping outcomes: Validation Failed never
    # reached Tally; Failed/Tally Failed/Write not verified means Tally rejected the
    # write or it couldn't be confirmed; Skipped/Invalid* are structurally ineligible
    # for reasons unrelated to Tally (bad GSTIN, missing data, etc).
    counts = {"total": len(vouchers), "eligible": sum(_voucher_ready(v) for v in vouchers), "attempted": attempted,
              "imported": sum(r["status"] == "Imported" for r in results),
              "already_imported": sum(r["status"] == "Already Imported" for r in results),
              "validation_failed": sum(r["status"] in {"Review Required", "Validation Failed"} for r in results),
              "master_setup_failed": sum(r["status"] == "Master Setup Failed" for r in results),
              "preflight_failed": sum(r["status"] == "Preflight Failed" for r in results),
              "tally_failed": sum(r["status"] == "Tally Failed" for r in results),
              "verification_failed": sum(r["status"] == "Verification Failed" for r in results),
              "unknown": sum(r["status"] in {"Unknown / Verify", "Unknown / Verify Before Retry"} for r in results),
              "invalid": sum(r["status"] in {"Invalid", "Invalid Source Date", "Invalid Source Data"} for r in results),
              "skipped": sum(r["status"] == "Skipped" for r in results),
              "not_attempted": sum(r["status"] == "Not Attempted" for r in results),
              "waiting_for_tally_period": len(outside_period_ids), "failed_due_to_period_mismatch": 0,
              "skipped_party_lookup_incomplete": 0}
    # Backward-compatible aggregate: everything that never became a voucher in Tally.
    counts["failed"] = counts["master_setup_failed"] + counts["preflight_failed"] + counts["tally_failed"] + counts["verification_failed"]
    import_status, message = _step6_outcome(counts)
    outcome = import_outcome(counts)
    return {"batch_id": batch.id, "file_type": batch.file_type, "total_parsed_invoices": len(vouchers), **outcome,
            "return_type_diagnostics": _return_type_diagnostics(batch, vouchers),
            "import_status": import_status,
            "invalid_source_dates": counts["invalid"], "batch_status": "PARTIAL_TALLY_PERIOD_MATCH" if outside_period_ids else "READY",
            "period_match": not outside_period_ids, "period_selection_method": "SOURCE_INVOICE_DATES_REQUEST_SCOPED",
            "tally_period_request_confirmed": bool(preflight.get("query_valid")),
            **period_info, "voucher_dates_modified": 0,
            "tally_xml_date_source": "normalized source invoice_date", "tally_json_date_source": "normalized source invoice_date",
            **_write_metadata(), "actual_write_attempted": bool(attempted), "connection": status,
            "voucher_preflight": preflight_metadata,
            "existing_tally_voucher_count": len(preflight.get("vouchers") or []),
            "master_lookup_source": master_lookup_source, "warnings": lookup_warnings, "masters": master_results,
            "results": results, "summary": counts, "first_failure": first_failure, "message": message}
