from datetime import date
from xml.etree import ElementTree as ET

from .validators import dec, money
from gst_tally.services.company import financial_year_details


def _amount(
    parent,
    ledger,
    value,
    positive=False,
    party=False,
    bill_reference="",
):
    """
    Normal accounting ledger entry.

    Used for:
        - Party ledger
        - Input/Output CGST
        - Input/Output SGST
        - Input/Output IGST
        - Cess
        - Other Charges
    """

    entry = ET.SubElement(
        parent,
        "LEDGERENTRIES.LIST",
    )

    ET.SubElement(
        entry,
        "LEDGERNAME",
    ).text = ledger

    ET.SubElement(
        entry,
        "ISDEEMEDPOSITIVE",
    ).text = (
        "No" if positive else "Yes"
    )

    ET.SubElement(
        entry,
        "ISPARTYLEDGER",
    ).text = (
        "Yes" if party else "No"
    )

    signed = money(value if positive else -dec(value))

    if party:
        ET.SubElement(
            entry,
            "ISLASTDEEMEDPOSITIVE",
        ).text = (
            "No" if positive else "Yes"
        )

    ET.SubElement(
        entry,
        "AMOUNT",
    ).text = str(signed)

    if party and bill_reference:
        bill = ET.SubElement(
            entry,
            "BILLALLOCATIONS.LIST",
        )

        ET.SubElement(
            bill,
            "NAME",
        ).text = bill_reference

        ET.SubElement(
            bill,
            "BILLTYPE",
        ).text = "New Ref"

        ET.SubElement(
            bill,
            "AMOUNT",
        ).text = str(signed)

    return entry


def _signed_amount(
    parent,
    ledger,
    value,
):
    """
    Signed ledger amount.

    Mainly used for Round Off.
    """

    value = money(value)

    entry = ET.SubElement(
        parent,
        "LEDGERENTRIES.LIST",
    )

    ET.SubElement(
        entry,
        "LEDGERNAME",
    ).text = ledger

    ET.SubElement(
        entry,
        "ISDEEMEDPOSITIVE",
    ).text = (
        "Yes" if value < 0 else "No"
    )

    ET.SubElement(
        entry,
        "ISPARTYLEDGER",
    ).text = "No"

    ET.SubElement(
        entry,
        "AMOUNT",
    ).text = str(value)

    return entry


def _rate_text(value):
    """
    Convert Decimal GST rate into Tally-friendly number text.

    Examples:
        5.000  -> 5
        18.000 -> 18
        2.500  -> 2.5
    """

    return f"{dec(value):g}"


def _rate_metadata(
    entry,
    gst_rate,
):
    """
    Write GST rate metadata for one taxable Purchase/Sales ledger row.

    RATEOFINVOICETAX/BASICRATEOFINVOICETAX/RATE are flat TYPE="Number"
    scalars (not wrapped in a ".LIST" collection), and every taxable ledger
    -- including the first one on the voucher -- receives its own rate here,
    computed only from this allocation's gst_rate. Never inferred from
    another allocation, the ledger name, or position in the voucher.
    """

    rate = dec(gst_rate)

    rate_text = _rate_text(rate)

    # Full GST rate for this ledger allocation. Tally's Accounting Invoice
    # Rate/Per column binds to these scalars -- RATE is required alongside
    # RATEOFINVOICETAX/BASICRATEOFINVOICETAX/GSTTAXRATE (see _taxable_amount)
    # for every taxable row to display its rate, first row included.
    for tag in (
        "RATEOFINVOICETAX",
        "BASICRATEOFINVOICETAX",
        "RATE",
    ):
        ET.SubElement(
            entry,
            tag,
            {
                "TYPE": "Number",
            },
        ).text = rate_text

    # Explicit GST breakup.
    rate_details = (
        (
            "CGST",
            rate / 2,
        ),
        (
            "SGST/UTGST",
            rate / 2,
        ),
        (
            "IGST",
            rate,
        ),
    )

    for head, value in rate_details:
        detail = ET.SubElement(
            entry,
            "RATEDETAILS.LIST",
        )

        ET.SubElement(
            detail,
            "GSTRATEDUTYHEAD",
            {
                "TYPE": "String",
            },
        ).text = head

        ET.SubElement(
            detail,
            "GSTRATEVALUATIONTYPE",
            {
                "TYPE": "String",
            },
        ).text = "Based on Value"

        ET.SubElement(
            detail,
            "GSTRATE",
            {
                "TYPE": "Number",
            },
        ).text = _rate_text(value)


def _taxable_amount(
    parent,
    ledger,
    value,
    gst_rate,
    positive=False,
):
    """
    Create one taxable Purchase/Sales ledger allocation.

    Example:

        GST Purchase 5%
        Taxable = 190
        Rate = 5

        generates an independent 5% taxable ledger row.

        GST Purchase 18% gets its own 18% row.

        No GST rate is inferred from another ledger.
    """

    entry = ET.SubElement(
        parent,
        "LEDGERENTRIES.LIST",
    )

    # Tally audit marker.
    audit = ET.SubElement(
        entry,
        "OLDAUDITENTRYIDS.LIST",
        {
            "TYPE": "Number",
        },
    )

    ET.SubElement(
        audit,
        "OLDAUDITENTRYIDS",
    ).text = "-1"

    # -----------------------------------------
    # IMPORTANT GST RATE FIX
    # -----------------------------------------

    _rate_metadata(
        entry,
        gst_rate,
    )

    # -----------------------------------------

    ET.SubElement(
        entry,
        "ROUNDTYPE",
        {
            "TYPE": "String",
        },
    ).text = "Not Applicable"

    ET.SubElement(
        entry,
        "LEDGERNAME",
        {
            "TYPE": "String",
        },
    ).text = ledger

    # Full GST rate. RATE/RATEOFINVOICETAX/BASICRATEOFINVOICETAX are written
    # by _rate_metadata() above, from this same allocation's gst_rate.
    ET.SubElement(
        entry,
        "GSTTAXRATE",
        {
            "TYPE": "Number",
        },
    ).text = _rate_text(gst_rate)

    deemed_positive = "No" if positive else "Yes"

    ET.SubElement(
        entry,
        "ISDEEMEDPOSITIVE",
        {
            "TYPE": "Logical",
        },
    ).text = deemed_positive

    ET.SubElement(
        entry,
        "LEDGERFROMITEM",
    ).text = "No"

    ET.SubElement(
        entry,
        "REMOVEZEROENTRIES",
    ).text = "No"

    ET.SubElement(
        entry,
        "ISPARTYLEDGER",
    ).text = "No"

    ET.SubElement(
        entry,
        "GSTOVERRIDDEN",
    ).text = "No"

    ET.SubElement(
        entry,
        "ISGSTASSESSABLEVALUEOVERRIDDEN",
    ).text = "No"

    ET.SubElement(
        entry,
        "STRDISGSTAPPLICABLE",
    ).text = "No"

    ET.SubElement(
        entry,
        "STRDGSTISPARTYLEDGER",
    ).text = "No"

    ET.SubElement(
        entry,
        "STRDGSTISDUTYLEDGER",
    ).text = "No"

    ET.SubElement(
        entry,
        "CONTENTNEGISPOS",
    ).text = "No"

    ET.SubElement(
        entry,
        "ISLASTDEEMEDPOSITIVE",
    ).text = deemed_positive

    # -----------------------------------------
    # Amount
    # -----------------------------------------

    amount = str(money(value if positive else -dec(value)))

    ET.SubElement(
        entry,
        "AMOUNT",
        {
            "TYPE": "Amount",
        },
    ).text = amount

    ET.SubElement(
        entry,
        "VATEXPAMOUNT",
        {
            "TYPE": "Amount",
        },
    ).text = amount

    # -----------------------------------------
    # GST assessable amount
    # -----------------------------------------

    assessable = str(money(value))

    ET.SubElement(
        entry,
        "GSTASSESSABLEVALUE",
        {
            "TYPE": "Amount",
        },
    ).text = assessable

    ET.SubElement(
        entry,
        "GSTOVRDNASSESSABLEVALUE",
        {
            "TYPE": "Amount",
        },
    ).text = assessable

    ET.SubElement(
        entry,
        "GSTASSBLVALUE",
        {
            "TYPE": "Amount",
        },
    ).text = assessable

    return entry


def build_voucher(
    voucher,
    company,
    period=None,
):
    """
    Build Tally Purchase/Sales Accounting Invoice XML.
    """

    # ==========================================================
    # ENVELOPE
    # ==========================================================

    root = ET.Element(
        "ENVELOPE",
    )

    header = ET.SubElement(
        root,
        "HEADER",
    )

    ET.SubElement(
        header,
        "TALLYREQUEST",
    ).text = "Import Data"

    # ==========================================================
    # BODY
    # ==========================================================

    body = ET.SubElement(
        root,
        "BODY",
    )

    data = ET.SubElement(
        body,
        "IMPORTDATA",
    )

    desc = ET.SubElement(
        data,
        "REQUESTDESC",
    )

    ET.SubElement(
        desc,
        "REPORTNAME",
    ).text = "Vouchers"

    # ==========================================================
    # STATIC VARIABLES
    # ==========================================================

    variables = ET.SubElement(
        desc,
        "STATICVARIABLES",
    )

    ET.SubElement(
        variables,
        "SVCURRENTCOMPANY",
    ).text = company

    invoice_date = date.fromisoformat(voucher["invoice_date"])

    required_period = period or financial_year_details(invoice_date)

    ET.SubElement(
        variables,
        "SVFROMDATE",
        {
            "TYPE": "Date",
        },
    ).text = required_period[
        "start"
    ].strftime("%d-%b-%Y")

    ET.SubElement(
        variables,
        "SVTODATE",
        {
            "TYPE": "Date",
        },
    ).text = required_period[
        "end"
    ].strftime("%d-%b-%Y")

    # ==========================================================
    # REQUEST DATA
    # ==========================================================

    request_data = ET.SubElement(
        data,
        "REQUESTDATA",
    )

    message = ET.SubElement(
        request_data,
        "TALLYMESSAGE",
        {
            "xmlns:UDF": "TallyUDF",
        },
    )

    # ==========================================================
    # VOUCHER
    # ==========================================================

    voucher_type = voucher.get(
        "voucher_type",
        "Sales",
    )

    purchase = voucher_type == "Purchase"

    node = ET.SubElement(
        message,
        "VOUCHER",
        {
            "VCHTYPE": voucher_type,
            "ACTION": "Create",
            "OBJVIEW": "Invoice Voucher View",
        },
    )

    parsed = date.fromisoformat(voucher["invoice_date"])

    tally_date = parsed.strftime("%Y%m%d")

    ET.SubElement(
        node,
        "DATE",
    ).text = tally_date

    ET.SubElement(
        node,
        "EFFECTIVEDATE",
    ).text = tally_date

    ET.SubElement(
        node,
        "VOUCHERTYPENAME",
    ).text = voucher_type

    ET.SubElement(
        node,
        "VOUCHERNUMBER",
    ).text = voucher["invoice_number"]

    # ==========================================================
    # SUPPLIER INVOICE REFERENCE
    # ==========================================================

    ET.SubElement(
        node,
        "REFERENCE",
    ).text = voucher["invoice_number"]

    ET.SubElement(
        node,
        "REFERENCEDATE",
    ).text = tally_date

    # ==========================================================
    # PARTY
    # ==========================================================

    party_name = voucher["party"]["name"]

    ET.SubElement(
        node,
        "PARTYLEDGERNAME",
    ).text = party_name

    ET.SubElement(
        node,
        "PARTYNAME",
    ).text = party_name

    ET.SubElement(
        node,
        "PLACEOFSUPPLY",
    ).text = voucher.get(
        "place_of_supply",
        "",
    )

    # ==========================================================
    # PARTY DETAILS
    #
    # PARTYLEDGERNAME/PARTYNAME alone only populate the invoice's party
    # *name* -- the Party Details popup (State/Country/GST Registration
    # Type/GSTIN) needs its own voucher-level fields, sourced from the
    # already-resolved (and, for a registered GSTIN, already master-verified/
    # repaired -- see import_batch's Party master step) voucher["party"]
    # dict. Only written when the source actually has the value, so an
    # unregistered/incomplete party never gets a fabricated field.
    # ==========================================================

    party = voucher.get("party") or {}

    if party.get("gstin"):
        ET.SubElement(
            node,
            "PARTYGSTIN",
        ).text = party["gstin"]

    if party.get("state"):
        ET.SubElement(
            node,
            "STATENAME",
        ).text = party["state"]

    if party.get("country"):
        ET.SubElement(
            node,
            "COUNTRYNAME",
        ).text = party["country"]

    if party.get("registration_type"):
        ET.SubElement(
            node,
            "GSTREGISTRATIONTYPE",
        ).text = party["registration_type"]

    if party.get("mailing_name"):
        ET.SubElement(
            node,
            "PARTYMAILINGNAME",
        ).text = party["mailing_name"]

    if party.get("pincode"):
        ET.SubElement(
            node,
            "PARTYPINCODE",
        ).text = party["pincode"]

    if party.get("address"):
        party_address = ET.SubElement(
            node,
            "PARTYADDRESS.LIST",
        )

        ET.SubElement(
            party_address,
            "PARTYADDRESS",
        ).text = party["address"]

    # ==========================================================
    # ACCOUNTING INVOICE MODE
    # ==========================================================

    ET.SubElement(
        node,
        "PERSISTEDVIEW",
    ).text = "Invoice Voucher View"

    ET.SubElement(
        node,
        "OBJVIEW",
    ).text = "Invoice Voucher View"

    ET.SubElement(
        node,
        "ISINVOICE",
    ).text = "Yes"

    ET.SubElement(
        node,
        "ISDELETED",
    ).text = "No"

    ET.SubElement(
        node,
        "ISOPTIONAL",
    ).text = "No"

    # ==========================================================
    # PARTY LEDGER
    # ==========================================================

    _amount(
        node,
        party_name,
        voucher["invoice_total"],
        positive=purchase,
        party=True,
        bill_reference=voucher["invoice_number"],
    )

    # ==========================================================
    # PURCHASE / SALES TAXABLE LEDGERS
    # ==========================================================

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

            gst_rate = allocation["gst_rate"]

            taxable_value = allocation["taxable_value"]

            print(
                "RATE ALLOCATION",
                {
                    "ledger": ledger_name,
                    "gst_rate": gst_rate,
                    "taxable_value": taxable_value,
                },
            )

            _taxable_amount(
                node,
                ledger_name,
                taxable_value,
                gst_rate,
                positive=not purchase,
            )

    else:

        # Fallback if rate_allocations
        # are not supplied.

        totals = {}
        rates = {}

        for item in voucher["items"]:

            ledger_name = item["sales_ledger"]

            totals[ledger_name] = dec(totals.get(ledger_name)) + dec(
                item["taxable_value"]
            )

            rates[ledger_name] = item["gst_rate"]

        for (
            ledger_name,
            value,
        ) in sorted(totals.items()):

            _taxable_amount(
                node,
                ledger_name,
                value,
                rates[ledger_name],
                positive=not purchase,
            )

    # ==========================================================
    # GST TAX LEDGERS
    # ==========================================================

    if voucher.get("tax_allocations") is not None:

        for tax in voucher["tax_allocations"]:

            _amount(
                node,
                tax["ledger"],
                tax["amount"],
                positive=not purchase,
            )

    else:

        for field in (
            "cgst",
            "sgst",
            "igst",
        ):

            if dec(voucher[field]):

                _amount(
                    node,
                    field.upper(),
                    voucher[field],
                    positive=not purchase,
                )

    # ==========================================================
    # CESS
    # ==========================================================

    if dec(voucher.get("cess")):

        _amount(
            node,
            "Cess",
            voucher["cess"],
            positive=not purchase,
        )

    # ==========================================================
    # OTHER CHARGES
    # ==========================================================

    if dec(voucher.get("other_charges")):

        other_charges = dec(voucher["other_charges"])

        _amount(
            node,
            "Other Charges",
            other_charges,
            positive=(other_charges > 0),
        )

    # ==========================================================
    # ROUND OFF
    # ==========================================================

    if dec(voucher.get("rounding_adjustment")):

        _signed_amount(
            node,
            "Round Off",
            -dec(voucher["rounding_adjustment"]),
        )

    # ==========================================================
    # NARRATION
    # ==========================================================

    ET.SubElement(
        node,
        "NARRATION",
    ).text = (
        "Imported from GST batch; "
        f"source invoice "
        f'{voucher["invoice_number"]}'
    )

    # ==========================================================
    # FINAL XML
    # ==========================================================

    for entry in node.findall("LEDGERENTRIES.LIST"):

        ledger = entry.findtext("LEDGERNAME")

        if ledger and (
            "GST Purchase" in ledger
            or "GST Sales" in ledger
        ):
            print(
                "TALLY TAXABLE ROW",
                {
                    "ledger": ledger,
                    "RATE": entry.findtext("RATE"),
                    "RATEOFINVOICETAX":
                        entry.findtext("RATEOFINVOICETAX"),
                    "BASICRATEOFINVOICETAX":
                        entry.findtext("BASICRATEOFINVOICETAX"),
                    "GSTTAXRATE":
                        entry.findtext("GSTTAXRATE"),
                    "AMOUNT":
                        entry.findtext("AMOUNT"),
                },
            )

    xml = ET.tostring(
        root,
        encoding="utf-8",
    )

    return xml
