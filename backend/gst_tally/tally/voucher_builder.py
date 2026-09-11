from datetime import date
from xml.etree import ElementTree as ET

from .validators import dec, money
from gst_tally.services.company import financial_year_details


def _amount(parent, ledger, value, positive=False, party=False, bill_reference=""):
    """Normal accounting ledger entry."""
    entry = ET.SubElement(parent, "LEDGERENTRIES.LIST")
    ET.SubElement(entry, "LEDGERNAME").text = ledger
    ET.SubElement(entry, "ISDEEMEDPOSITIVE").text = "No" if positive else "Yes"
    ET.SubElement(entry, "ISPARTYLEDGER").text = "Yes" if party else "No"

    signed = money(value if positive else -dec(value))

    if party:
        ET.SubElement(entry, "ISLASTDEEMEDPOSITIVE").text = "No" if positive else "Yes"

    ET.SubElement(entry, "AMOUNT").text = str(signed)

    if party and bill_reference:
        bill = ET.SubElement(entry, "BILLALLOCATIONS.LIST")
        ET.SubElement(bill, "NAME").text = bill_reference
        ET.SubElement(bill, "BILLTYPE").text = "New Ref"
        ET.SubElement(bill, "AMOUNT").text = str(signed)

    return entry


def _signed_amount(parent, ledger, value):
    """Signed ledger amount, mainly used for Round Off."""
    value = money(value)
    entry = ET.SubElement(parent, "LEDGERENTRIES.LIST")
    ET.SubElement(entry, "LEDGERNAME").text = ledger
    ET.SubElement(entry, "ISDEEMEDPOSITIVE").text = "Yes" if value < 0 else "No"
    ET.SubElement(entry, "ISPARTYLEDGER").text = "No"
    ET.SubElement(entry, "AMOUNT").text = str(value)
    return entry


def _taxable_amount(parent, ledger, value, positive=False):
    """Create one taxable Purchase/Sales ledger allocation.

    Deliberately carries no RATEOFINVOICETAX/BASICRATEOFINVOICETAX/RATE/
    GSTTAXRATE/RATEDETAILS.LIST rate metadata: supplying any of those at the
    voucher's ledger-entry level makes Tally classify the transaction's GST
    Rate Details as "As per Voucher" (an override) instead of "As per
    Ledger", even when the values match the ledger master exactly. The rate
    must be resolved purely from the named GST Purchase/Sales ledger's own
    master -- which is why each rate gets its own distinctly-named ledger
    (e.g. "GST Purchase 5%" vs "GST Purchase 18%") rather than a shared
    ledger with a voucher-level rate override.
    """
    entry = ET.SubElement(parent, "LEDGERENTRIES.LIST")
    audit = ET.SubElement(entry, "OLDAUDITENTRYIDS.LIST", {"TYPE": "Number"})
    ET.SubElement(audit, "OLDAUDITENTRYIDS").text = "-1"

    ET.SubElement(entry, "ROUNDTYPE", {"TYPE": "String"}).text = "Not Applicable"
    ET.SubElement(entry, "LEDGERNAME", {"TYPE": "String"}).text = ledger

    deemed_positive = "No" if positive else "Yes"
    ET.SubElement(entry, "ISDEEMEDPOSITIVE", {"TYPE": "Logical"}).text = deemed_positive
    ET.SubElement(entry, "LEDGERFROMITEM").text = "No"
    ET.SubElement(entry, "REMOVEZEROENTRIES").text = "No"
    ET.SubElement(entry, "ISPARTYLEDGER").text = "No"
    ET.SubElement(entry, "GSTOVERRIDDEN").text = "No"
    ET.SubElement(entry, "ISGSTASSESSABLEVALUEOVERRIDDEN").text = "No"
    ET.SubElement(entry, "STRDISGSTAPPLICABLE").text = "No"
    ET.SubElement(entry, "STRDGSTISPARTYLEDGER").text = "No"
    ET.SubElement(entry, "STRDGSTISDUTYLEDGER").text = "No"
    ET.SubElement(entry, "CONTENTNEGISPOS").text = "No"
    ET.SubElement(entry, "ISLASTDEEMEDPOSITIVE").text = deemed_positive

    amount = str(money(value if positive else -dec(value)))
    ET.SubElement(entry, "AMOUNT", {"TYPE": "Amount"}).text = amount
    ET.SubElement(entry, "VATEXPAMOUNT", {"TYPE": "Amount"}).text = amount

    assessable = str(money(value))
    ET.SubElement(entry, "GSTASSESSABLEVALUE", {"TYPE": "Amount"}).text = assessable
    ET.SubElement(entry, "GSTASSBLVALUE", {"TYPE": "Amount"}).text = assessable

    return entry


def build_voucher(voucher, company, period=None):
    """Build Tally Purchase/Sales Accounting Invoice XML."""
    root = ET.Element("ENVELOPE")
    header = ET.SubElement(root, "HEADER")
    ET.SubElement(header, "TALLYREQUEST").text = "Import Data"

    body = ET.SubElement(root, "BODY")
    data = ET.SubElement(body, "IMPORTDATA")
    desc = ET.SubElement(data, "REQUESTDESC")
    ET.SubElement(desc, "REPORTNAME").text = "Vouchers"

    variables = ET.SubElement(desc, "STATICVARIABLES")
    ET.SubElement(variables, "SVCURRENTCOMPANY").text = company

    invoice_date = date.fromisoformat(voucher["invoice_date"])
    required_period = period or financial_year_details(invoice_date)
    ET.SubElement(variables, "SVFROMDATE", {"TYPE": "Date"}).text = required_period["start"].strftime("%d-%b-%Y")
    ET.SubElement(variables, "SVTODATE", {"TYPE": "Date"}).text = required_period["end"].strftime("%d-%b-%Y")

    request_data = ET.SubElement(data, "REQUESTDATA")
    message = ET.SubElement(request_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})

    voucher_type = voucher.get("voucher_type", "Sales")
    purchase = voucher_type == "Purchase"
    node = ET.SubElement(
        message,
        "VOUCHER",
        {"VCHTYPE": voucher_type, "ACTION": "Create", "OBJVIEW": "Invoice Voucher View"},
    )

    tally_date = invoice_date.strftime("%Y%m%d")
    ET.SubElement(node, "DATE").text = tally_date
    ET.SubElement(node, "EFFECTIVEDATE").text = tally_date
    ET.SubElement(node, "VOUCHERTYPENAME").text = voucher_type
    ET.SubElement(node, "VOUCHERNUMBER").text = voucher["invoice_number"]
    ET.SubElement(node, "REFERENCE").text = voucher["invoice_number"]
    ET.SubElement(node, "REFERENCEDATE").text = tally_date

    party = voucher.get("party") or {}
    party_name = party["name"]
    ET.SubElement(node, "PARTYLEDGERNAME").text = party_name
    ET.SubElement(node, "PARTYNAME").text = party_name
    ET.SubElement(node, "PLACEOFSUPPLY").text = voucher.get("place_of_supply", "")

    if party.get("gstin"):
        ET.SubElement(node, "PARTYGSTIN").text = party["gstin"]
    if party.get("state"):
        ET.SubElement(node, "STATENAME").text = party["state"]
    if party.get("country"):
        # Verified against a live Tally company: COUNTRYNAME is not a
        # recognized Voucher-level field -- Tally accepts the import
        # (CREATED=1) but silently drops the tag, leaving Country blank in
        # GSTR-2B/3B's Resolution of Uncertain Transactions (even though the
        # normal voucher's own Party Details popup still shows it correctly
        # from the party ledger master). COUNTRYOFRESIDENCE is the field
        # Tally actually persists on a voucher -- the same field the ledger
        # master builder writes for a Party ledger (see master_builder.py).
        ET.SubElement(node, "COUNTRYOFRESIDENCE").text = party["country"]
    if party.get("registration_type"):
        ET.SubElement(node, "GSTREGISTRATIONTYPE").text = party["registration_type"]
    if party.get("mailing_name"):
        ET.SubElement(node, "PARTYMAILINGNAME").text = party["mailing_name"]
    if party.get("pincode"):
        ET.SubElement(node, "PARTYPINCODE").text = party["pincode"]
    if party.get("address"):
        party_address = ET.SubElement(node, "PARTYADDRESS.LIST")
        ET.SubElement(party_address, "PARTYADDRESS").text = party["address"]

    ET.SubElement(node, "PERSISTEDVIEW").text = "Invoice Voucher View"
    ET.SubElement(node, "OBJVIEW").text = "Invoice Voucher View"
    ET.SubElement(node, "ISINVOICE").text = "Yes"
    ET.SubElement(node, "ISDELETED").text = "No"
    ET.SubElement(node, "ISOPTIONAL").text = "No"

    _amount(
        node,
        party_name,
        voucher["invoice_total"],
        positive=purchase,
        party=True,
        bill_reference=voucher["invoice_number"],
    )

    allocations = voucher.get("rate_allocations") or []
    if allocations:
        sorted_allocations = sorted(
            allocations,
            key=lambda row: (
                dec(row["gst_rate"]),
                row.get("account_ledger") or row["sales_ledger"],
            ),
        )
        for allocation in sorted_allocations:
            ledger_name = allocation.get("account_ledger") or allocation["sales_ledger"]
            taxable_value = allocation["taxable_value"]
            _taxable_amount(node, ledger_name, taxable_value, positive=not purchase)
    else:
        totals = {}
        for item in voucher["items"]:
            ledger_name = item["sales_ledger"]
            totals[ledger_name] = dec(totals.get(ledger_name)) + dec(item["taxable_value"])
        for ledger_name, value in sorted(totals.items()):
            _taxable_amount(node, ledger_name, value, positive=not purchase)

    if voucher.get("tax_allocations") is not None:
        for tax in voucher["tax_allocations"]:
            _amount(node, tax["ledger"], tax["amount"], positive=not purchase)
    else:
        for field in ("cgst", "sgst", "igst"):
            if dec(voucher[field]):
                _amount(node, field.upper(), voucher[field], positive=not purchase)

    if dec(voucher.get("cess")):
        _amount(node, voucher.get("cess_ledger", "Cess"), voucher["cess"], positive=not purchase)

    if dec(voucher.get("other_charges")):
        other_charges = dec(voucher["other_charges"])
        _amount(node, "Other Charges", other_charges, positive=(other_charges > 0))

    if dec(voucher.get("rounding_adjustment")):
        _signed_amount(node, "Round Off", -dec(voucher["rounding_adjustment"]))

    ET.SubElement(node, "NARRATION").text = (
        "Imported from GST batch; "
        f"source invoice "
        f'{voucher["invoice_number"]}'
    )

    return ET.tostring(root, encoding="utf-8")


def build_voucher_batch(vouchers, company, period=None):
    """Build one Import Data envelope containing multiple voucher messages."""
    if not vouchers:
        return b""
    first = ET.fromstring(build_voucher(vouchers[0], company, period))
    request_data = first.find("./BODY/IMPORTDATA/REQUESTDATA")
    for voucher in vouchers[1:]:
        source = ET.fromstring(build_voucher(voucher, company, period))
        message = source.find("./BODY/IMPORTDATA/REQUESTDATA/TALLYMESSAGE")
        if message is not None:
            request_data.append(message)
    return ET.tostring(first, encoding="utf-8")
