"""Voucher lookup through Tally **read** requests.

This module only ever issues export/query envelopes. The voucher collection is
the primary read because it returns the identifiers (GUID / Master ID / Alter ID)
needed for idempotency; the standard Day Book export corroborates it. Neither
read shares an envelope with the master or voucher *import* builders.
"""
import logging
from decimal import Decimal, InvalidOperation
import re

from .read_requests import (build_daybook_query_xml, build_voucher_query_xml,
                            DAY_BOOK_REPORT, VOUCHER_COLLECTION, tally_date)
from .read_parsers import parse_daybook_export, parse_voucher_query_response
from .client import TallyClient

logger = logging.getLogger(__name__)

# Kept importable for callers/tests that still refer to the old builder name.
build_day_book_request = build_daybook_query_xml


def _log_request(operation, request_type, report, company, from_date, to_date):
    logger.info("[TALLY-READ] operation=%s request_type=%s report=%s company=%s from_date=%s to_date=%s",
                operation, request_type, report, company, tally_date(from_date), tally_date(to_date or from_date))


def _log_response(operation, parsed):
    logger.info("[TALLY-READ] operation=%s response_kind=%s response_request=%s response_report=%s "
                "response_company=%s company_match=%s query_valid=%s vouchers=%s reason=%s",
                operation, parsed.get("kind"), parsed.get("request") or "-", parsed.get("report") or "-",
                parsed.get("company") or "-", parsed.get("company_match"), parsed.get("query_valid"),
                len(parsed.get("vouchers") or []), parsed.get("reason") or "-")


def _read(client, payload, parser, company, operation):
    parsed = parser(client.post(payload), company, operation)
    _log_response(operation, parsed)
    return parsed


def query_vouchers(client, company, from_date, to_date=None, operation="voucher_query"):
    """Query existing vouchers in ``company`` between two dates.

    A valid response with no vouchers is a success with ``vouchers == []``; it is
    never reported as a query failure. A transport, envelope, or company mismatch
    keeps ``query_valid`` false so callers can block writes.
    """
    to_date = to_date or from_date
    reads = []

    _log_request(f"{operation}:collection", "Export", f"Collection {VOUCHER_COLLECTION}", company, from_date, to_date)
    collection = _read(client, build_voucher_query_xml(company, from_date, to_date),
                       parse_voucher_query_response, company, f"{operation}:collection")
    reads.append({"source": collection["source"], "query_valid": collection["query_valid"],
                  "kind": collection["kind"], "request": collection["request"],
                  "report": collection["report"], "company": collection["company"],
                  "vouchers": len(collection["vouchers"]), "reason": collection["reason"]})

    # The Day Book export corroborates an empty collection and stands in for it
    # when the collection itself is not answered. It is never used to invent a
    # successful result: if both reads fail, the query stays invalid.
    daybook = None
    if collection["query_valid"] and collection["vouchers"]:
        merged, source, valid, reason = collection["vouchers"], collection["source"], True, ""
        authoritative = collection
    else:
        _log_request(f"{operation}:daybook", "Export Data", DAY_BOOK_REPORT, company, from_date, to_date)
        daybook = _read(client, build_daybook_query_xml(company, from_date, to_date),
                        parse_daybook_export, company, f"{operation}:daybook")
        reads.append({"source": daybook["source"], "query_valid": daybook["query_valid"],
                      "kind": daybook["kind"], "request": daybook["request"],
                      "report": daybook["report"], "company": daybook["company"],
                      "vouchers": len(daybook["vouchers"]), "reason": daybook["reason"]})
        if collection["query_valid"] and daybook["query_valid"]:
            merged, source, valid, reason = _merge(collection["vouchers"], daybook["vouchers"]), "VOUCHER_COLLECTION+DAY_BOOK", True, ""
            authoritative = collection
        elif daybook["query_valid"]:
            merged, source, valid = daybook["vouchers"], daybook["source"], True
            reason = f"Voucher collection read unavailable ({collection['reason']}); Day Book export was used"
            authoritative = daybook
        elif collection["query_valid"]:
            merged, source, valid = collection["vouchers"], collection["source"], True
            reason = f"Day Book export unavailable ({daybook['reason']}); voucher collection was used"
            authoritative = collection
        else:
            merged, source, valid = [], "INVALID_QUERY_RESPONSE", False
            reason = f"{collection['reason']}; {daybook['reason']}"
            authoritative = collection

    return {"query_valid": valid, "vouchers": merged, "source": source, "reason": reason,
            "request": authoritative["request"], "report": authoritative["report"],
            "company": authoritative["company"], "company_match": authoritative["company_match"],
            "kind": authoritative["kind"], "raw": authoritative["raw"], "reads": reads,
            "from_date": tally_date(from_date), "to_date": tally_date(to_date)}


def _merge(primary, secondary):
    seen = {(row["voucher_type"].casefold(), row["voucher_number"].casefold()) for row in primary}
    extra = [row for row in secondary
             if (row["voucher_type"].casefold(), row["voucher_number"].casefold()) not in seen]
    return [*primary, *extra]


def find_voucher(vouchers, voucher):
    """Return the Tally record matching a source invoice, or ``None``.

    Voucher number/reference plus voucher type is the identity. The party ledger
    is compared only when Tally actually returned one, because some export paths
    omit it and an absent field must not read as a mismatch.
    """
    wanted_number = str(voucher.get("invoice_number", "")).strip().casefold()
    wanted_type = str(voucher.get("voucher_type", "Sales")).strip().casefold()
    wanted_party = str(voucher.get("party", {}).get("name", "")).strip().casefold()
    for record in vouchers:
        if record.get("cancelled"):
            continue
        number = record["voucher_number"].strip().casefold()
        reference = record["reference"].strip().casefold()
        party = record["party"].strip().casefold()
        if number != wanted_number and reference != wanted_number:
            continue
        if record["voucher_type"].strip().casefold() != wanted_type:
            continue
        if wanted_party and party and party != wanted_party:
            continue
        return record
    return None


def index_vouchers(vouchers):
    """Map ``(voucher_type, number)`` to a Tally record for batch reconciliation."""
    index = {}
    for record in vouchers:
        for number in {record["voucher_number"].strip().casefold(), record["reference"].strip().casefold()}:
            if number:
                index.setdefault((record["voucher_type"].strip().casefold(), number), record)
    return index


def _number(value):
    try:
        return abs(Decimal(str(value or "0").replace(",", "")))
    except InvalidOperation:
        return Decimal("0")


def _rate(value):
    try:
        return format(Decimal(str(value or "").replace("%", "").strip()).normalize(), "f")
    except InvalidOperation:
        return ""


def _ledger_key(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _is_taxable_ledger_name(name):
    return bool(re.match(r"\s*gst\s*(purchase|sales)\b", str(name or ""), re.IGNORECASE))


def _ledger_kind(name):
    text = str(name or "")
    if _is_taxable_ledger_name(text):
        return "taxable"
    if re.match(r"\s*(?:input|output)\s*cgst\b", text, re.IGNORECASE):
        return "cgst"
    if re.match(r"\s*(?:input|output)\s*sgst\b", text, re.IGNORECASE):
        return "sgst"
    if re.match(r"\s*(?:input|output)\s*igst\b", text, re.IGNORECASE):
        return "igst"
    if re.match(r"\s*round\s*off\s*$", text, re.IGNORECASE):
        return "round_off"
    return "other"


# Priority-4 GST-rate fallback (see _actual_rate below): restricted to exactly
# the canonical ``GST Purchase/Sales NN%`` and ``Input/Output CGST/SGST/IGST
# NN%`` ledger names this application itself generates (return_mapping.
# account_ledger/tax_ledger). Never applied to any other ledger name, so a
# real Tally rate omission on an unrelated ledger is never silently fabricated
# into a match (see test_query_back_rejects_ledger_master_rate_when_allocation_rate_is_blank).
_CANONICAL_RATE_LEDGER = re.compile(
    r"^\s*(?:GST\s+(?:Sales|Purchase)|(?:Output|Input)\s+(?:CGST|SGST|IGST))\s+(\d+(?:\.\d+)?)\s*%\s*$",
    re.IGNORECASE)


def _controlled_name_rate(name):
    match = _CANONICAL_RATE_LEDGER.match(str(name or ""))
    return _rate(match.group(1)) if match else ""


def _actual_rate(entry):
    """Resolve a query-back ledger entry's GST rate in priority order:
    (1) explicit voucher allocation, (2) nested GST rate detail -- both already
    folded onto ``gst_rate`` by _ledger_entry/_resolve_entry_rates before this
    runs -- then (3) the controlled canonical ledger-name fallback above."""
    return entry.get("gst_rate") or _controlled_name_rate(entry.get("ledger"))


def _signed(value):
    try:
        return Decimal(str(value or "0").replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


AMOUNT_TOLERANCE = Decimal("0.01")


def _first_mismatch_reason(diff):
    """The single most useful mismatch to report first -- exact ledger, rate,
    expected/actual amounts and the signed difference -- never the generic
    'accounting ledger allocations do not match' summary alone."""
    if diff.get("missing_purchase_allocations"):
        row = diff["missing_purchase_allocations"][0]
        return (f"Mismatch type: MISSING_TAXABLE_LEDGER. Ledger: {row['ledger']}. "
                f"Expected rate: {row['gst_rate']}%. This ledger/rate combination was not found in the Tally voucher.")
    if diff.get("missing_tax_ledgers"):
        return f"Mismatch type: MISSING_TAX_LEDGER. Ledger: {diff['missing_tax_ledgers'][0]}. Not found in the Tally voucher."
    if diff.get("amount_differences"):
        row = diff["amount_differences"][0]
        difference = (Decimal(row["actual"]) - Decimal(row["expected"])).quantize(Decimal("0.01"))
        return (f"Mismatch type: {row['kind']}. Ledger: {row['ledger']}. "
                f"Expected: {row['expected']}. Actual: {row['actual']}. Difference: {difference}.")
    if diff.get("round_off_mismatch"):
        row = diff["round_off_mismatch"]
        return (f"Mismatch type: ROUND_OFF. Expected: {row['expected']}. Actual: {row['actual']}. "
                f"Difference: {row['difference']}.")
    if diff.get("party_mismatch"):
        return f"Mismatch type: PARTY_LEDGER. {diff['party_mismatch']['reason']}."
    return "Voucher exists, but its accounting ledger allocations do not match the source invoice"


def _resolve_entry_rates(client, company, match):
    """Fill in each taxable ledger entry's GST rate from the Day Book export.

    Verified against a live TallyPrime 7.1 instance: the Voucher collection read
    (``VOUCHER_COLLECTION`` in read_requests.py, used for identifiers/idempotency)
    never returns RATEOFINVOICETAX/RATEDETAILS on LEDGERENTRIES.LIST, even though
    they are listed in its FETCH -- Tally silently drops those nested fields from a
    FETCH-filtered Collection export. The standard Day Book report *is* a full
    native export (no FETCH filtering) and does carry them. So when the entries
    already resolved (from the Collection read) are missing a rate on a taxable
    Purchase/Sales ledger, this re-reads the same voucher from the Day Book and
    copies just the rate fields across, leaving amounts/identifiers untouched.
    """
    entries = match.get("ledger_entries") or match.get("taxable_allocations") or []
    if not entries or not any(_is_taxable_ledger_name(entry.get("ledger")) and not entry.get("gst_rate")
                              for entry in entries):
        return [dict(entry) for entry in entries]
    voucher_date = match.get("date")
    if not voucher_date:
        return [dict(entry) for entry in entries]
    try:
        daybook = parse_daybook_export(client.post(build_daybook_query_xml(company, voucher_date)),
                                       company, "voucher_rate_resolution")
    except Exception:
        return [dict(entry) for entry in entries]
    if not daybook["query_valid"]:
        return [dict(entry) for entry in entries]
    wanted_type = str(match.get("voucher_type", "")).strip().casefold()
    wanted_number = str(match.get("voucher_number", "")).strip().casefold()
    wanted_guid = str(match.get("guid", "")).strip()
    source_record = next((record for record in daybook["vouchers"]
                          if (wanted_guid and record.get("guid") == wanted_guid) or
                          (record["voucher_type"].strip().casefold() == wanted_type and
                           record["voucher_number"].strip().casefold() == wanted_number)), None)
    if not source_record:
        return [dict(entry) for entry in entries]
    by_ledger = {}
    for entry in source_record.get("ledger_entries") or []:
        by_ledger.setdefault(_ledger_key(entry.get("ledger")), []).append(entry)
    resolved = []
    for entry in entries:
        if entry.get("gst_rate") or not _is_taxable_ledger_name(entry.get("ledger")):
            resolved.append(dict(entry))
            continue
        richer = next((candidate for candidate in by_ledger.get(_ledger_key(entry.get("ledger"))) or []
                       if candidate.get("gst_rate")), None)
        resolved.append({**entry, **richer} if richer else dict(entry))
    return resolved


def _verification_details(voucher, match):
    expected_rows = voucher.get("rate_allocations") or voucher.get("items") or []
    expected_purchase = [{"ledger": str(row.get("account_ledger") or row.get("sales_ledger") or "").strip(),
                          "gst_rate": _rate(row.get("gst_rate")),
                          "amount": _number(row.get("taxable_value"))} for row in expected_rows]
    actual_entries = match.get("ledger_entries") or match.get("taxable_allocations") or []
    expected_tax = voucher.get("tax_allocations") or []
    expected_purchase_keys = {_ledger_key(row["ledger"]) for row in expected_purchase}
    expected_tax_keys = {_ledger_key(row.get("ledger")) for row in expected_tax}
    actual_purchase = [row for row in actual_entries
                       if _ledger_key(row.get("ledger")) in expected_purchase_keys or
                       re.match(r"\s*gst\s*purchase", str(row.get("ledger") or ""), re.IGNORECASE)]
    actual_tax = [row for row in actual_entries
                  if _ledger_key(row.get("ledger")) in expected_tax_keys or
                  re.match(r"\s*input\s*(?:c|s|i)gst", str(row.get("ledger") or ""), re.IGNORECASE)]

    # Match key includes the resolved rate (not just the ledger name) so a
    # ledger whose rate genuinely can't be confirmed (an unrelated/custom
    # ledger with no rate in its name and no query-back rate data) still fails
    # to match rather than being accepted on name alone.
    actual_index = {(_ledger_key(row.get("ledger")), _actual_rate(row)): row for row in actual_purchase}
    missing_purchase, amount_differences = [], []
    for expected in expected_purchase:
        key = (_ledger_key(expected["ledger"]), expected["gst_rate"])
        actual = actual_index.get(key)
        if not actual:
            missing_purchase.append({"ledger": expected["ledger"], "gst_rate": expected["gst_rate"]})
        elif expected["amount"] and abs(_number(actual.get("amount")) - expected["amount"]) > AMOUNT_TOLERANCE:
            amount_differences.append({"ledger": expected["ledger"], "kind": "TAXABLE_LEDGER_AMOUNT",
                                       "expected": str(expected["amount"]), "actual": str(_number(actual.get("amount")))})

    actual_tax_index = {_ledger_key(row.get("ledger")): row for row in actual_tax}
    missing_tax = []
    for expected in expected_tax:
        actual = actual_tax_index.get(_ledger_key(expected.get("ledger")))
        if not actual:
            missing_tax.append(str(expected.get("ledger") or ""))
        elif _number(expected.get("amount")) and abs(_number(actual.get("amount")) - _number(expected.get("amount"))) > AMOUNT_TOLERANCE:
            amount_differences.append({"ledger": expected.get("ledger", ""), "kind": "TAX_LEDGER_AMOUNT",
                                       "expected": str(_number(expected.get("amount"))),
                                       "actual": str(_number(actual.get("amount")))})

    # Round Off is its own accounting entry -- never folded into the taxable/
    # tax buckets above -- compared using build_voucher's own sign convention
    # (Round Off is written as -rounding_adjustment; see voucher_builder.py),
    # so both a positive and a negative round-off are handled correctly.
    expected_round_off = _signed(voucher.get("rounding_adjustment"))
    actual_round_off_entry = next((row for row in actual_entries if _ledger_kind(row.get("ledger")) == "round_off"), None)
    round_off_mismatch = None
    if expected_round_off and not actual_round_off_entry:
        round_off_mismatch = {"expected": str(-expected_round_off), "actual": "0.00", "difference": str(expected_round_off)}
    elif expected_round_off and actual_round_off_entry:
        actual_signed = _signed(actual_round_off_entry.get("amount"))
        expected_signed = -expected_round_off
        if abs(actual_signed - expected_signed) > AMOUNT_TOLERANCE:
            round_off_mismatch = {"expected": str(expected_signed), "actual": str(actual_signed),
                                  "difference": str((actual_signed - expected_signed).quantize(Decimal("0.01")))}
    elif not expected_round_off and actual_round_off_entry and abs(_signed(actual_round_off_entry.get("amount"))) > AMOUNT_TOLERANCE:
        stray = _signed(actual_round_off_entry.get("amount"))
        round_off_mismatch = {"expected": "0.00", "actual": str(stray), "difference": str(stray)}

    # Party ledger is verified as its own section (name + amount), never mixed
    # into the taxable/tax GST allocation comparison above. Consistent with
    # find_voucher's existing rule ("the party ledger is compared only when
    # Tally actually returned one... an absent field must not read as a
    # mismatch"), this only asserts when the party ledger can be unambiguously
    # located by its exact expected name; a query shape that doesn't carry a
    # party ledger entry at all (e.g. a summary Day Book record) is simply not
    # checked here rather than being reported as a failure.
    party_name = str(voucher.get("party", {}).get("name") or "").strip()
    party_key = _ledger_key(party_name)
    non_party_keys = {_ledger_key(row.get("ledger")) for row in actual_entries
                      if _ledger_kind(row.get("ledger")) in {"taxable", "cgst", "sgst", "igst", "round_off"}}
    party_entry = next((row for row in actual_entries if _ledger_key(row.get("ledger")) == party_key), None) if party_key else None
    party_mismatch = None
    if party_entry is not None:
        expected_total = _number(voucher.get("invoice_total"))
        actual_party_amount = _number(party_entry.get("amount"))
        if expected_total and abs(actual_party_amount - expected_total) > AMOUNT_TOLERANCE:
            party_mismatch = {"reason": f"Party ledger '{party_entry.get('ledger', '')}' amount {actual_party_amount} "
                                        f"does not match the invoice total {expected_total}"}
        else:
            # Direction sanity via Tally's own Dr/Cr flag: the party (control)
            # ledger and the taxable/tax ledgers must sit on opposite sides of
            # the double entry. If every entry shares the same flag, the
            # voucher's accounting direction is structurally broken,
            # independent of amounts -- this is the "sign" check requested,
            # done through ISDEEMEDPOSITIVE rather than a raw amount sign
            # (which is not a reliable Dr/Cr indicator on its own).
            party_flag_text = str(party_entry.get("is_deemed_positive") or "").strip()
            other_flag_texts = [str(row.get("is_deemed_positive") or "").strip() for row in actual_entries
                                if _ledger_key(row.get("ledger")) in non_party_keys]
            # Only evaluated when Tally actually populated the flag on every
            # relevant entry; a fixture/report shape that omits ISDEEMEDPOSITIVE
            # altogether must not be misread as "same side" from blank data.
            if party_flag_text and other_flag_texts and all(other_flag_texts):
                party_positive = party_flag_text.casefold() == "yes"
                other_flags = {text.casefold() == "yes" for text in other_flag_texts}
                if other_flags == {party_positive}:
                    party_mismatch = {"reason": "Party ledger and taxable/tax ledgers are on the same accounting "
                                                "side (Dr/Cr direction is inconsistent)"}

    expected_rates = sorted({row["gst_rate"] for row in expected_purchase if row["gst_rate"]}, key=Decimal)
    actual_rates = sorted({_actual_rate(row) for row in actual_purchase if _actual_rate(row)}, key=Decimal)
    verification_difference = {"missing_purchase_allocations": missing_purchase,
                               "missing_tax_ledgers": missing_tax,
                               "amount_differences": amount_differences,
                               "round_off_mismatch": round_off_mismatch,
                               "party_mismatch": party_mismatch}
    diagnostics = {
        "invoice_number": voucher.get("invoice_number", ""),
        "party_name": voucher.get("party", {}).get("name", ""),
        "gstin": voucher.get("party", {}).get("gstin", ""),
        "voucher_id": match.get("identifier", ""),
        "expected_purchase_ledgers": [row["ledger"] for row in expected_purchase],
        "actual_purchase_ledgers": [row.get("ledger", "") for row in actual_purchase],
        "expected_tax_ledgers": [row.get("ledger", "") for row in expected_tax],
        "actual_tax_ledgers": [row.get("ledger", "") for row in actual_tax],
        "expected_gst_rates": expected_rates, "actual_gst_rates": actual_rates,
        "raw_tally_ledger_fields": actual_entries,
        "verification_difference": verification_difference,
    }
    diagnostics["matched"] = not (missing_purchase or missing_tax or amount_differences or round_off_mismatch or party_mismatch)
    diagnostics["mismatch_reason"] = "" if diagnostics["matched"] else _first_mismatch_reason(verification_difference)
    return diagnostics


def verify_voucher(client, company, voucher):
    """Query-back for a single voucher on its own date. Read/export only."""
    voucher_type = voucher.get("voucher_type", "Sales")
    result = query_vouchers(client, company, voucher["invoice_date"],
                            operation=f"voucher_query_back:{voucher.get('invoice_number', '')}")
    if not result["query_valid"]:
        return {"found": False, "query_valid": False, "identifier": "", "reason": result["reason"],
                "source": "INVALID_QUERY_RESPONSE", "raw": result["raw"], "reads": result["reads"]}
    match = find_voucher(result["vouchers"], voucher)
    if not match:
        return {"found": False, "query_valid": True, "identifier": "",
                "reason": f"{voucher_type} voucher was not found in the Tally voucher query for {result['from_date']}",
                "source": result["source"], "raw": result["raw"], "reads": result["reads"]}
    match = {**match, "ledger_entries": _resolve_entry_rates(client, company, match)}
    diagnostics = _verification_details(voucher, match)
    if not diagnostics["matched"]:
        logger.warning("Tally voucher verification mismatch: %s", diagnostics)
        return {"found": False, "query_valid": True, "identifier": match["identifier"],
                "reason": diagnostics["mismatch_reason"],
                **diagnostics, "source": result["source"], "raw": result["raw"], "reads": result["reads"]}
    return {"found": True, "query_valid": True, "identifier": match["identifier"],
            "voucher_type": match["voucher_type"], "party": match["party"],
            "voucher_number": match["voucher_number"], "source_reference": match["reference"],
            "guid": match["guid"], "master_id": match["master_id"], "alter_id": match["alter_id"],
            "taxable_allocations": match.get("taxable_allocations", []),
            **diagnostics,
            "reason": f"{voucher_type} voucher confirmed by Tally voucher query ({result['source']})",
            "source": result["source"], "raw": result["raw"], "reads": result["reads"]}
