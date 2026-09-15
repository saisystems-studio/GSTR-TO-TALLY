from datetime import date
from .validators import dec, money
from gst_tally.services.company import financial_year_details


def _ledger(name, value, debit=False, party=False, bill_reference=""):
    """Plain ledger allocation.

    Deliberately carries no rateofinvoicetax/basicrateofinvoicetax/
    ratedetails GST-rate metadata: supplying any of those at the voucher's
    ledger-entry level makes Tally classify the transaction's GST Rate
    Details as "As per Voucher" (an override) instead of "As per Ledger",
    even when the values match the ledger master exactly. The rate must be
    resolved purely from the named GST Purchase/Sales ledger's own master.
    """
    amount = -money(value) if debit else money(value)
    result = {"oldauditentryids": [{"metadata": True, "type": "Number"}, "-1"],
            "ledgername": name, "isdeemedpositive": amount < 0, "ledgerfromitem": False,
            "removezeroentries": False, "ispartyledger": party, "amount": str(amount)}
    if party and bill_reference:
        result["billallocations"] = [{"name": bill_reference, "billtype": "New Ref", "amount": str(amount)}]
    return result


def build_json_voucher(voucher, company, period=None):
    invoice_date = date.fromisoformat(voucher["invoice_date"])
    voucher_date = date.fromisoformat(voucher.get("voucher_date") or voucher["invoice_date"])
    tally_date = voucher_date.strftime("%Y%m%d")
    required_period = period or financial_year_details(voucher_date)
    voucher_type = voucher.get("voucher_type", "Sales")
    purchase = voucher_type == "Purchase"
    entries = [_ledger(voucher["party"]["name"], voucher["invoice_total"], debit=not purchase, party=True,
                       bill_reference=voucher["invoice_number"])]
    allocations = voucher.get("rate_allocations") or []
    if allocations:
        entries.extend(_ledger(row.get("account_ledger") or row["sales_ledger"], row["taxable_value"], debit=purchase)
                       for row in sorted(allocations, key=lambda item: (dec(item["gst_rate"]), item["sales_ledger"])))
    else:
        totals = {}
        for item in voucher["items"]: totals[item["sales_ledger"]] = dec(totals.get(item["sales_ledger"])) + dec(item["taxable_value"])
        entries.extend(_ledger(name, value, debit=purchase) for name, value in sorted(totals.items()))
    if voucher.get("tax_allocations") is not None:
        entries.extend(_ledger(row["ledger"], row["amount"], debit=purchase) for row in voucher["tax_allocations"])
    else:
        for field in ("cgst", "sgst", "igst"):
            if dec(voucher.get(field)): entries.append(_ledger(field.upper(), voucher[field], debit=purchase))
    if dec(voucher.get("cess")): entries.append(_ledger(voucher.get("cess_ledger", "Cess"), voucher["cess"], debit=purchase))
    if dec(voucher.get("other_charges")): entries.append(_ledger("Other Charges", voucher["other_charges"]))
    if dec(voucher.get("rounding_adjustment")): entries.append(_ledger("Round Off", voucher["rounding_adjustment"], debit=purchase))
    party = voucher["party"]
    # Accounting Invoice mode mirrors voucher_builder.py's XML: Invoice Voucher View
    # with isinvoice=True, and no inventory allocations (ledger entries only).
    message = {"metadata": {"type": "Voucher", "vchtype": voucher_type, "action": "Create", "objview": "Invoice Voucher View"},
               "date": tally_date, "effectivedate": tally_date, "vouchertypename": voucher_type,
               "vouchernumber": voucher["invoice_number"],
               # reference/referencedate is what Tally's Purchase Accounting Invoice
               # screen displays as "Supplier Invoice No." / "Date" for GSTR-2A/2B;
               # for Sales it is the standard Order/Ref field. Either way it is the
               # source document identity, distinct from Tally's own voucher number.
               "reference": voucher["invoice_number"], "referencedate": tally_date,
               "partyname": party["name"], "partyledgername": party["name"],
               "partygstin": party.get("gstin", ""), "placeofsupply": voucher.get("place_of_supply", ""),
               "statename": party.get("state", ""), "partypincode": party.get("pincode", ""),
               "persistedview": "Invoice Voucher View",
               "isdeleted": False, "isoptional": False, "isinvoice": True, "ledgerentries": entries}
    if not purchase:
        # "Buyer" print fields describe the customer being billed on a Sales
        # invoice; they do not apply to a Purchase voucher, where the party is
        # the supplier, not the buyer.
        message["basicbuyername"] = party["name"]
        if party.get("address"): message["basicbuyeraddress"] = [{"metadata": True, "type": "String"}, party["address"]]
    return {"static_variables": [{"name": "svVchImportFormat", "value": "jsonex"},
                                  {"name": "svCurrentCompany", "value": company},
                                  {"name": "svFromDate", "value": required_period["start"].strftime("%Y%m%d")},
                                  {"name": "svToDate", "value": required_period["end"].strftime("%Y%m%d")}],
            "tallymessage": [message]}
