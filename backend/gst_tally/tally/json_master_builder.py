from decimal import Decimal

from .master_builder import _applicable_from, _gst_rate_detail_heads, account_ledger_rate, tally_tax_type


TALLY_APPLICABLE = "\x04 Applicable"
TALLY_ANY = "\x04 Any"
TALLY_NOT_APPLICABLE = "\x04 Not Applicable"


def _variables(company):
    return [
        {"name": "svMstImportFormat", "value": "jsonex"},
        {"name": "svCurrentCompany", "value": company},
    ]


def _rate_text(value):
    """Tally's native JSON always prefixes NUMBER-type scalars with a leading
    space (see a real Tally export: "alterid": " 207", "gstrate": " 9") and
    shows whole rates with no decimals but a fractional half-rate (e.g. 5%/2)
    with exactly two."""
    decimal_value = Decimal(str(value or "0"))
    if decimal_value == decimal_value.to_integral_value():
        return f" {int(decimal_value)}"
    return f" {decimal_value:.2f}"


def _metadata(master):
    # metadata.name identifies which existing object an Alter targets; the
    # message's own top-level "name" (set separately by each _*_message
    # builder) is the value Tally assigns as the ledger's (possibly new)
    # name. These differ only when existing_name is set to rename an
    # already-existing ledger (e.g. a Party ledger enriched from
    # GSTIN-as-name to its real business name) -- everywhere else
    # existing_name is absent and both stay identical.
    is_alter = str(master.get("action", "Create")).strip().casefold() == "alter"
    return {
        "type": "Ledger",
        "name": master.get("existing_name") or master["name"],
        "reservedname": "",
        "action": "alter" if is_alter else "create",
    }


def _language_name(name):
    return [{
        "name": [
            {"metadata": True, "type": "String"},
            name,
        ],
        "languageid": " 1033",
    }]


def _gst_rate_details(ledger_rate):
    # Reuses master_builder's own (head, valuation_type, rate) tuples so the
    # JSON and XML masters can never disagree on the GST rate breakup --
    # only the enum encoding differs: XML sends the plain string, native
    # JSON requires Tally's internal "\x04 " fixed-list marker.
    return [
        {
            "gstratedutyhead": head,
            "gstratevaluationtype": TALLY_NOT_APPLICABLE if valuation_type == "Not Applicable" else valuation_type,
            "gstrate": _rate_text(rate),
            "gstrateperunit": "0",
        }
        for head, valuation_type, rate in _gst_rate_detail_heads(ledger_rate)
    ]


def _party_message(master):
    applicable = _applicable_from(master)
    # Verified against a live TallyPrime 7.1 instance: altering an existing
    # ledger that sends both the legacy top-level scalars (LEDSTATENAME/
    # COUNTRYNAME/PINCODE/GSTREGISTRATIONTYPE) and the dated
    # LEDMAILINGDETAILS.LIST/LEDGSTREGDETAILS.LIST aggregates together makes
    # Tally silently ignore *both* -- ALTERED=1 with no error, but a
    # query-back still shows State/Country/GSTIN blank. Sending only the
    # dated aggregates persists correctly. Create is unaffected either way,
    # so the legacy scalars stay for Create (needed by older Tally versions
    # without the dated schema) and are only dropped on Alter. Mirrors
    # master_builder.build_master's is_party_alter handling.
    is_alter = str(master.get("action", "Create")).strip().casefold() == "alter"
    message = {
        "metadata": _metadata(master),
        "name": master["name"],
        "parent": master["group"],
        "taxtype": "Others",
        "countryofresidence": "India",
        "isbillwiseon": True,
        "languagename": _language_name(master["name"]),
    }

    gstin = str(master.get("gstin") or "").strip()
    registration_type = str(master.get("registration_type") or "").strip()
    state = str(master.get("state") or "").strip()
    place_of_supply = str(master.get("place_of_supply") or state).strip()
    pincode = str(master.get("pincode") or "").strip()
    address = str(master.get("address") or "").strip()

    if gstin:
        message["partygstin"] = gstin
        message["gstregistrationno"] = gstin
    if state:
        message["ledstatename"] = state
    if not is_alter:
        message["countryname"] = "India"
    if pincode:
        message["pincode"] = pincode
    if registration_type:
        message["gstregistrationtype"] = registration_type

    if gstin:
        reg = {
            "applicablefrom": applicable,
            "gstin": gstin,
            "isothterritoryassessee": "No",
            "considerpurchaseforexport": "No",
            "istransporter": "No",
            "iscommonparty": "No",
        }
        if registration_type:
            reg["gstregistrationtype"] = registration_type
        if state:
            reg["state"] = state
        if place_of_supply:
            reg["placeofsupply"] = place_of_supply
        message["ledgstregdetails"] = [reg]

    mailing = {
        "applicablefrom": applicable,
        "mailingname": master.get("mailing_name") or master["name"],
        "country": "India",
    }
    if address:
        mailing["address"] = [
            {"metadata": True, "type": "String"},
            address,
        ]
    if state:
        mailing["state"] = state
    if pincode:
        mailing["pincode"] = pincode
    message["ledmailingdetails"] = [mailing]

    return message


def _tax_message(master):
    message = {
        "metadata": _metadata(master),
        "name": master["name"],
        "parent": master["group"],
        "taxtype": "GST",
        "gstdutyhead": tally_tax_type(master["tax_type"]),
        "rateoftaxcalculation": _rate_text(master.get("gst_rate", "0")),
        "languagename": _language_name(master["name"]),
    }
    # Cess is the one GST duty head with its own valuation-type dropdown
    # (ad-valorem/per-unit/etc.) distinct from a plain percentage rate;
    # "Any" leaves that choice unconstrained rather than forcing one.
    if tally_tax_type(master["tax_type"]) == "Cess":
        message["valuationtype"] = TALLY_ANY
    return message


def _account_message(master, rate_mode="both"):
    """Sales/Purchase GST account ledger. Applies to both Create and Alter --
    there is no branch here that skips the nested GST detail hierarchy for
    Alter; the only thing that differs by action is _metadata()'s "action"
    field, which every master type shares."""
    ledger_rate = Decimal(account_ledger_rate(master))
    supply_type = master.get("supply_type", "Goods")
    taxability = master.get("taxability", "Taxable")
    # "As per Company/Group" (the common, non-rate-suffixed fallback ledgers
    # e.g. "GST Sales"/"GST Purchase") means this ledger has no ledger-level
    # GST detail override at all -- Tally itself never exposes a Taxability
    # Type for that mode, so supplytype/taxability must be omitted here
    # rather than sent with a possibly-wrong forced "Taxable" value. Every
    # rate-wise and exempt ledger keeps today's default, unchanged.
    gst_details_source = master.get("gst_details_source", "Specify Details Here")
    inherits_from_company = gst_details_source == "As per Company/Group"

    message = {
        "metadata": _metadata(master),
        "name": master["name"],
        "parent": master["group"],
        "taxtype": "Others",
        "gstapplicable": TALLY_APPLICABLE,
        "gsttypeofsupply": supply_type,
        "hsndetails": [{
            "applicablefrom": _applicable_from(master),
            "srcofhsndetails": "As per Company/Group",
        }],
        "languagename": _language_name(master["name"]),
    }
    # Mirrors master_builder.build_master's rate_mode: "both" (default,
    # Strategy A) sends the flat rate and the nested breakup together, since
    # either can be the one that's actually effective depending on the
    # company's own "Provide GST rate details" configuration. If a real
    # Tally re-read shows this didn't survive, Strategy B (see service.py's
    # _repair_gst_account_rate_with_fallback) retries with only one of the two.
    if rate_mode in ("both", "flat_only"):
        message["rateoftaxcalculation"] = _rate_text(ledger_rate)
    if rate_mode in ("both", "nested_only"):
        gst_detail = {"applicablefrom": _applicable_from(master)}
        if not inherits_from_company:
            gst_detail["supplytype"] = supply_type
            gst_detail["taxability"] = taxability
        gst_detail.update({
            "srcofgstdetails": gst_details_source,
            "gstcalcslabonmrp": False,
            "isreversechargeapplicable": False,
            "isnongstgoods": False,
            "gstineligibleitc": False,
            "includeexpforslabcalc": False,
            "istaxonmrp": False,
            "statewisedetails": [{
                "statename": TALLY_ANY,
                "ratedetails": _gst_rate_details(ledger_rate),
            }],
        })
        message["gstdetails"] = [gst_detail]
    return message


def _simple_ledger_message(master):
    return {
        "metadata": _metadata(master),
        "name": master["name"],
        "parent": master["group"],
        "taxtype": "Others",
        "languagename": _language_name(master["name"]),
    }


def build_json_master(master, company, rate_mode="both"):
    """Build native Tally JSONEx master payload.

    `rate_mode` mirrors master_builder.build_master's Strategy A/B contract:
    "both" (default) sends the flat RATEOFTAXCALCULATION and the nested GST
    rate breakup together; "flat_only"/"nested_only" send just one, for
    service.py's Strategy B retry when Strategy A's rate doesn't survive a
    real Tally re-read. Only Sales/Purchase account ledgers use rate_mode.

    Always returns a dict -- never None -- for every master_type, and applies
    identically regardless of whether master["action"] is Create or Alter.
    """
    master_type = master["master_type"]

    if master_type == "Party":
        message = _party_message(master)
    elif master_type == "Tax":
        message = _tax_message(master)
    elif master_type in ("Sales", "Purchase"):
        message = _account_message(master, rate_mode)
    else:
        message = _simple_ledger_message(master)

    return {
        "static_variables": _variables(company),
        "tallymessage": [message],
    }
