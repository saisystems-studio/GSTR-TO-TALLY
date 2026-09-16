from collections import OrderedDict
from decimal import Decimal
from gst_tally.services.party_ledger_name import clean_party_name, resolve_party_ledger_name
from gst_tally.services.party_lookup import normalize_gstin, normalized_party_result
from .validators import dec, money, state_name, transaction_type, valid_gstin
from .return_mapping import get_tally_mapping, rate_text
from .round_off import UPLOADED

clean_name = clean_party_name

def correction_key(gstin, invoice_number, invoice_date):
    date_text = invoice_date.isoformat() if hasattr(invoice_date, "isoformat") else str(invoice_date or "")
    return f"{gstin}|{invoice_number}|{date_text}"

def sales_ledger_name(rate, supply_type="Goods"):
    return get_tally_mapping("GSTR-1").account_ledger(rate)

def _looks_exempt(value):
    normalized = str(value or "").strip().casefold().replace("-", " ")
    return normalized in {"exempt", "exempted", "expt"} or normalized.startswith("exempt ")

def _source_exempt(row):
    """True only when the source explicitly labels the row as exempt."""
    if any(_looks_exempt(getattr(row, field, "")) for field in ("invoice_type", "supply_type", "source_type")):
        return True
    source = row.source_line or {}
    exempt_keys = ("taxability", "supplytype", "supply type", "sup typ", "sup_typ",
                   "invoice type", "invoice_type", "inv typ", "inv_typ", "nature", "category")
    for key, value in source.items():
        key_text = str(key or "").strip().casefold().replace("_", " ")
        if any(marker in key_text for marker in exempt_keys) and _looks_exempt(value):
            return True
    return False

def registration_type_for(gstin, party=None):
    """Regular for a valid GSTIN (using the fetched taxpayer type when known),
    Unregistered otherwise -- never a state selection or invented default."""
    if not valid_gstin(gstin):
        return "Unregistered"
    fetched = getattr(party, "taxpayer_type", "") if party else ""
    return fetched or "Regular"

def normalized_vouchers(batch, company):
    tally_mapping = get_tally_mapping(batch.gst_return_type)
    models = __import__("gst_tally.models", fromlist=["GSTParty", "GSTLedgerMapping"])
    actionable = batch.invoices.filter(processing_state__in=["PENDING", "RETRY"])
    invoice_gstins = [normalize_gstin(gstin) for gstin in actionable.values_list("customer_gstin", flat=True)]
    parties = {p.gstin: p for p in models.GSTParty.objects.filter(
        gstin__in=invoice_gstins)}
    saved_ledgers = {m.gstin: m.tally_ledger_name for m in models.GSTLedgerMapping.objects.filter(
        gstin__in=invoice_gstins, is_active=True)}
    groups = OrderedDict()
    for row in actionable.order_by("id"):
        party_gstin = normalize_gstin(row.customer_gstin)
        key = (party_gstin, row.invoice_no, row.invoice_date)
        party = parties.get(party_gstin)
        source = (batch.source_parties or {}).get(party_gstin, {}) or (batch.source_parties or {}).get(row.customer_gstin, {})
        normalized_party = normalized_party_result(
            party_gstin, party, source,
            lookup_status=getattr(party, "lookup_status", "") if party else "",
        )
        # "Needs Attention" is a status shown via row["status"]/skip_reason_code
        # (see party_eligibility/prepare) -- it must never overwrite the actual
        # ledger name. A party whose Sandbox lookup hasn't resolved still gets
        # the real trade_name -> legal_name -> source name -> GSTIN priority
        # name here; party_eligibility is what keeps an unresolved party's
        # voucher out of actual Tally master/ledger creation (see _voucher_ready).
        party_name = resolve_party_ledger_name(
            party, source, party_gstin,
            mapped_name=saved_ledgers.get(party_gstin, ""),
        )
        name_source = normalized_party["name_source"] if party_name == normalized_party["name"] else "Name/Mapping"
        party_state = normalized_party["state"] or state_name(party_gstin[:2])
        tx_type, pos = transaction_type(company.get("state"), row.place_of_supply or row.state_code, party_state, party_gstin)
        if key not in groups:
            # Traces the exact point a party's Trade Name could be lost between
            # Sandbox and the Tally ledger: source_party_name/sandbox_* show
            # what enrichment actually returned, persisted_trade_name shows
            # what's on the GSTParty row this function read, and
            # resolved_party_ledger_name is what master AND voucher both use --
            # masters_for() builds every Party master straight from this same
            # voucher["party"] dict, so these two can never disagree by
            # construction (no separate/later party-name derivation exists).
            party_diag = (getattr(party, "lookup_diagnostics", None) or {}) if party else {}
            print("=== PARTY MASTER TRACE ===")
            print("gstin:", party_gstin)
            print("source_party_name:", source.get("party_name") or source.get("name") or "")
            print("sandbox_lookup_attempted:", bool(party_diag.get("lookup_attempted")))
            print("sandbox_http_status:", party_diag.get("http_status"))
            print("sandbox_success:", normalized_party["name_source"] in {"SANDBOX_TRADE_NAME", "SANDBOX_LEGAL_NAME"})
            print("sandbox_trade_name:", normalized_party["trade_name"] if normalized_party["name_source"] == "SANDBOX_TRADE_NAME" else "")
            print("sandbox_legal_name:", normalized_party["legal_name"] if normalized_party["name_source"] in {"SANDBOX_TRADE_NAME", "SANDBOX_LEGAL_NAME"} else "")
            print("persisted_trade_name:", getattr(party, "trade_name", "") or getattr(party, "legal_name", "") if party else "")
            print("resolved_party_ledger_name:", party_name)
            print("name_source:", name_source)
            groups[key] = {"invoice_id": row.id, "invoice_number": row.invoice_no, "invoice_date": row.invoice_date.isoformat() if row.invoice_date else "",
                "voucher_date": (row.voucher_date or row.invoice_date).isoformat() if (row.voucher_date or row.invoice_date) else "",
                "is_carry_forward": row.is_carry_forward,
                "original_period": row.original_period, "posting_period": row.posting_period,
                "party": {"gstin": party_gstin, "trade_name": normalized_party["trade_name"], "name": party_name,
                          "name_source": name_source, "legal_name": normalized_party["legal_name"],
                          "mailing_name": party_name,
                          "address": normalized_party["address"], "state": normalized_party["state"],
                          "country": normalized_party["country"], "gstin_status": normalized_party.get("gstin_status", ""),
                          "pincode": normalized_party["pincode"], "registration_type": normalized_party["registration_type"] or registration_type_for(party_gstin, party),
                          "place_of_supply": normalized_party["state"] or party_state,
                          # A source/mapping-derived display name (including a
                          # pre-existing Tally ledger reused via GSTLedgerMapping)
                          # is never genuine Sandbox taxpayer enrichment -- this
                          # stays false until Sandbox itself actually supplies
                          # legal_name/trade_name for this GSTIN.
                          "party_details_complete": normalized_party["party_details_complete"],
                          "data_source": normalized_party.get("data_source", {})},
                "return_type": tally_mapping.return_type, "direction": tally_mapping.direction,
                "voucher_type": tally_mapping.voucher_type, "party_group": tally_mapping.party_group,
                "account_group": tally_mapping.account_group,
                "place_of_supply": pos, "transaction_type": tx_type, "items": [], "taxable_total": "0", "cgst": "0", "sgst": "0", "igst": "0", "cess": "0", "other_charges": "0", "round_off": "0", "source_round_off": "0", "source_type": UPLOADED, "invoice_total": str(money(row.invoice_value)), "source_invoice_values": [], "source_rows": [], "source_trace": []}
        voucher = groups[key]
        exempt = _source_exempt(row)
        # rate_available distinguishes a genuinely-missing GST rate (source
        # has no Tax %/Rate of Tax column, or the cell is blank) from a real
        # 0% rate -- tax_percent is nullable precisely to preserve that
        # distinction (see canonical_invoice.parse_decimal). A missing rate
        # is never treated as 0%.
        rate_available = row.tax_percent is not None
        supply = "Services" if str(row.supply_type).casefold() == "services" else "Goods"
        # Three ledger-selection paths, in priority order:
        #   1. Exempt (source explicitly says so -- never inferred from a 0%
        #      or missing rate) -> the return-type's exempt ledger.
        #   2. A real rate is present -> existing rate-wise ledger, unchanged.
        #   3. Rate is genuinely unavailable -> the common, non-rate-suffixed
        #      fallback ledger; the actual tax component amounts (not a
        #      derived rate) decide which Duties & Taxes ledgers apply --
        #      see the tax_allocations loop below.
        if exempt:
            account_ledger = tally_mapping.exempt_account_ledger()
            gst_rate = "0"
            taxability = "Exempt"
        elif rate_available:
            account_ledger = tally_mapping.account_ledger(row.tax_percent)
            gst_rate = str(dec(row.tax_percent))
            taxability = "Taxable"
        else:
            account_ledger = tally_mapping.common_account_ledger()
            gst_rate = "0"
            taxability = "Taxable"
        voucher["items"].append({"name": clean_name(row.item_name), "description": row.description, "hsn_sac": row.hsn_sac,
            "quantity": str(row.quantity or ""), "unit": row.unit, "rate": str(row.rate or ""),
            "taxable_value": str(money(row.taxable_value)), "gst_rate": gst_rate, "discount": str(money(row.discount)),
            "cgst": str(money(row.cgst)), "sgst": str(money(row.sgst)), "igst": str(money(row.igst)),
            "cess": str(money(row.cess)), "supply_type": supply,
            "taxability": taxability, "rate_available": rate_available,
            "account_ledger": account_ledger,
            "sales_ledger": account_ledger,
            "gst_details_source": "Specify Details Here" if (exempt or rate_available) else "As per Company/Group"})
        voucher["source_rows"].append(row.id)
        voucher["source_trace"].append({"database_row_id": row.id, "source_row_number": (row.source_line or {}).get("source_row_number"),
            "invoice_number": row.invoice_no, "invoice_date": row.invoice_date.isoformat() if row.invoice_date else "",
            "gstin": party_gstin, "invoice_value": str(money(row.invoice_value)), "taxable_value": str(money(row.taxable_value)),
            "gst_rate": str(dec(row.tax_percent)), "cgst": str(money(row.cgst)), "sgst": str(money(row.sgst)),
            "igst": str(money(row.igst)), "cess": str(money(row.cess)), "reverse_charge": row.reverse_charge,
            "invoice_type": row.invoice_type, "raw": row.source_line or {}})
        if row.invoice_value is not None: voucher["source_invoice_values"].append(str(money(row.invoice_value)))
        for field in ("taxable_total", "cgst", "sgst", "igst", "cess"):
            source_field = "taxable_value" if field == "taxable_total" else field
            voucher[field] = str(money(dec(voucher[field]) + dec(getattr(row, source_field, 0))))
        if not dec(voucher["other_charges"]):
            voucher["other_charges"] = str(money(row.other_charges))
        if not dec(voucher["source_round_off"]):
            voucher["source_round_off"] = str(money(row.round_off))
        voucher["round_off"] = "0"
        if not voucher["invoice_total"] or dec(voucher["invoice_total"]) == 0: voucher["invoice_total"] = str(money(row.invoice_value))
    for voucher in groups.values():
        allocations = {}
        for item in voucher["items"]:
            key = (dec(item["gst_rate"]), item["sales_ledger"])
            allocation = allocations.setdefault(key, {"gst_rate": rate_text(item["gst_rate"]), "sales_ledger": item["sales_ledger"],
                                                       "account_ledger": item.get("account_ledger") or item["sales_ledger"],
                                                       "taxability": item.get("taxability", "Taxable"),
                                                       "rate_available": item.get("rate_available", True),
                                                       "gst_details_source": item.get("gst_details_source", "Specify Details Here"),
                                                       "taxable_value": Decimal("0"), "cgst": Decimal("0"), "sgst": Decimal("0"), "igst": Decimal("0")})
            for field in ("taxable_value", "cgst", "sgst", "igst"): allocation[field] += dec(item.get(field))
        voucher["rate_allocations"] = [{**value, **{field: str(money(value[field])) for field in ("taxable_value", "cgst", "sgst", "igst")}}
                                       for _, value in sorted(allocations.items(), key=lambda pair: (pair[0][0], pair[0][1]))]
        tax_allocations = {}
        for allocation in voucher["rate_allocations"]:
            components = ("IGST",) if dec(allocation["igst"]) else (("CGST", "SGST") if dec(allocation["cgst"]) or dec(allocation["sgst"]) else ())
            rate_available = allocation.get("rate_available", True)
            for component in components:
                amount = dec(allocation[component.lower()])
                if amount:
                    # A missing rate never falls back to the rate-wise "Input
                    # CGST 9%"-style naming (which would fabricate a rate) --
                    # the fixed common Duties & Taxes ledgers are used
                    # instead, keyed purely on the actual nonzero amount.
                    name = tally_mapping.tax_ledger(component, allocation["gst_rate"]) if rate_available else tally_mapping.common_tax_ledger(component)
                    current = tax_allocations.setdefault(name, {"ledger": name, "component": component, "amount": Decimal("0"),
                                                                  "gst_rate": (str(money(dec(allocation["gst_rate"]) if component == "IGST" else dec(allocation["gst_rate"]) / 2))
                                                                               if rate_available else "0"),
                                                                  "tax_type": {"CGST": "CGST", "SGST": "SGST/UTGST", "IGST": "IGST"}[component]})
                    current["amount"] += amount
        voucher["tax_allocations"] = [{**row, "amount": str(money(row["amount"]))} for row in tax_allocations.values()]
        # The voucher-level Cess line (see voucher_builder.py/json_voucher_builder.py)
        # follows the same missing-rate fallback: only switch its ledger name
        # away from the existing "Cess" when a non-exempt item in this
        # voucher genuinely had no rate available.
        voucher["cess_ledger"] = (tally_mapping.common_tax_ledger("CESS")
                                   if any(not item.get("rate_available", True) and item.get("taxability") != "Exempt"
                                          for item in voucher["items"])
                                   else "Cess")
        invoice_values = set(voucher.pop("source_invoice_values", []))
        voucher["invoice_value_conflict"] = len(invoice_values) > 1
        voucher["invoice_value_candidates"] = sorted(invoice_values)
        for field in ("cgst", "sgst", "igst", "cess"): voucher[f"source_{field}"] = voucher[field]
        corrections = (batch.company_details or {}).get("voucher_corrections", {})
        audit = corrections.get(correction_key(voucher["party"]["gstin"], voucher["invoice_number"], voucher["invoice_date"]), {})
        field_map = {"invoice_value": "invoice_total", "taxable_value": "taxable_total", "cgst": "cgst",
                     "sgst": "sgst", "igst": "igst", "cess": "cess", "round_off": "source_round_off"}
        for correction_field, value in (audit.get("fields") or {}).items():
            target = field_map.get(correction_field)
            if target:
                voucher[target] = str(money(value))
        voucher["correction_audit"] = audit
        voucher["round_off_corrected"] = "round_off" in (audit.get("fields") or {})
    return list(groups.values())
