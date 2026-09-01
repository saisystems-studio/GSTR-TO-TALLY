from gst_tally.utils.file_utils import parse_date, parse_decimal
from gst_tally.utils.uploaded_files import load_json_upload
from .source_party import add_source_party, log_source_parties


def parse(file_obj):
    payload = load_json_upload(file_obj)
    rows, parties = [], {}
    seller = payload.get("SellerDtls", {}) if isinstance(payload, dict) else {}
    company_candidates = [payload.get("gstin"), payload.get("supplier_gstin"), payload.get("seller_gstin"), seller.get("Gstin")] if isinstance(payload, dict) else []
    for party_index, party in enumerate(payload.get("b2b", []) if isinstance(payload, dict) else [], 1):
        gstin = str(party.get("ctin") or party.get("gstin") or "").strip().upper()
        add_source_party(parties, gstin, party, party.get("SellerDtls", {}), party.get("SupplierDtls", {}))
        for invoice_index, invoice in enumerate(party.get("inv", []) or [], 1):
            add_source_party(parties, gstin, invoice, invoice.get("SellerDtls", {}), invoice.get("SupplierDtls", {}))
            for item_index, item in enumerate(invoice.get("itms", []) or [{}], 1):
                detail = item.get("itm_det", item) if isinstance(item, dict) else {}
                quantity = detail.get("qty") or detail.get("quantity")
                rate = detail.get("urt") or detail.get("rate")
                rows.append({
                    "invoice_date": parse_date(invoice.get("idt")), "customer_gstin": gstin,
                    "invoice_no": str(invoice.get("inum") or "").strip(),
                    "taxable_value": parse_decimal(detail.get("txval")), "tax_percent": parse_decimal(detail.get("rt")),
                    "cgst": parse_decimal(detail.get("camt")), "sgst": parse_decimal(detail.get("samt")),
                    "igst": parse_decimal(detail.get("iamt")), "invoice_value": parse_decimal(invoice.get("val")),
                    "state_code": str(invoice.get("pos") or "").strip(),
                    "reverse_charge": str(invoice.get("rchrg") or "").strip(),
                    "invoice_type": str(invoice.get("inv_typ") or "").strip(),
                    "filing_period": str(payload.get("fp") or "").strip(), "filing_type": "GSTR1",
                    "filing_date": parse_date(invoice.get("fldtr1") or party.get("fldtr1")), "source_type": "B2B",
                    "item_name": str(detail.get("nm") or detail.get("item_name") or detail.get("description") or "").strip(),
                    "description": str(detail.get("desc") or detail.get("description") or "").strip(),
                    "hsn_sac": str(detail.get("hsn_sc") or detail.get("hsn") or detail.get("sac") or "").strip(),
                    "quantity": parse_decimal(quantity), "unit": str(detail.get("uqc") or detail.get("unit") or "").strip(),
                    "rate": parse_decimal(rate), "discount": parse_decimal(detail.get("discount")),
                    "cess": parse_decimal(detail.get("csamt")),
                    "place_of_supply": str(invoice.get("pos") or "").strip(),
                    "supply_type": str(detail.get("supply_type") or invoice.get("supply_type") or "").strip(),
                    "other_charges": parse_decimal(invoice.get("other_charges")),
                    "round_off": parse_decimal(invoice.get("round_off")),
                    "source_line": ({"source_row_number": f"b2b:{party_index}:inv:{invoice_index}:item:{item_index}", **detail}
                                    if isinstance(detail, dict) else {}),
                })
    log_source_parties(parties)
    from .company import source_company_metadata
    return rows, {"return_period": str(payload.get("fp", "")) if isinstance(payload, dict) else "", "parties": parties,
                  **source_company_metadata(company_candidates, seller)}
