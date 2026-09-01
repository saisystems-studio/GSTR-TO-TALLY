from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET
from gst_tally.services.company import get_financial_year_start
from .mappings import clean_name
from .return_mapping import extract_rate_from_name

def tally_tax_type(value):
    """Return the exact GST Tax Type value accepted by Tally's dropdown/XML."""
    key = str(value or "").strip().casefold()
    return {"central tax": "CGST", "cgst": "CGST",
            "state tax": "SGST/UTGST", "sgst": "SGST/UTGST", "sgst/utgst": "SGST/UTGST",
            "integrated tax": "IGST", "igst": "IGST", "cess": "Cess"}.get(key, str(value or "").strip())

def envelope(company=""):
    root = ET.Element("ENVELOPE"); header = ET.SubElement(root, "HEADER"); ET.SubElement(header, "TALLYREQUEST").text = "Import Data"
    data = ET.SubElement(ET.SubElement(root, "BODY"), "IMPORTDATA"); desc = ET.SubElement(data, "REQUESTDESC")
    ET.SubElement(desc, "REPORTNAME").text = "All Masters"
    if company: ET.SubElement(ET.SubElement(desc, "STATICVARIABLES"), "SVCURRENTCOMPANY").text = company
    request = ET.SubElement(data, "REQUESTDATA")
    return root, request

def _applicable_from(master):
    """Use the current Tally company's Books Beginning From date when available."""
    company_books_from = master.get("company_books_from")
    if company_books_from:
        if isinstance(company_books_from, date):
            return company_books_from.strftime("%Y%m%d")
        try:
            return date.fromisoformat(str(company_books_from).replace("/", "-")).strftime("%Y%m%d")
        except ValueError:
            pass
    try:
        parsed = date.fromisoformat(str(master.get("applicable_from") or ""))
    except ValueError:
        return "20230401"
    return get_financial_year_start(parsed).strftime("%Y%m%d")

def _rate_text(value):
    return f"{Decimal(str(value or '0')):g}"

def _typed(parent, tag, text="", type_name="String"):
    node = ET.SubElement(parent, tag, {"TYPE": type_name})
    node.text = text
    return node

def account_ledger_rate(master):
    """The authoritative GST rate for a Purchase/Sales account ledger.

    The ledger name is the source of truth ("GST Purchase 12%" -> 12,
    "GST Sales 18 %" -> 18, case/spacing-insensitive, via the existing
    generic return_mapping.extract_rate_from_name) -- not whatever
    ``gst_rate`` field a caller happened to attach to the master dict. This
    is what a repair/alter must send as RATEOFTAXCALCULATION regardless of
    any upstream inconsistency in that field, so a canonically-named ledger
    can never end up written with GST Rate = 0%. Falls back to the supplied
    ``gst_rate`` only when the name itself doesn't encode a percentage.
    """
    parsed = extract_rate_from_name(master.get("name"))
    return parsed if parsed is not None else Decimal(str(master.get("gst_rate") or "0"))

def _gst_rate_detail_heads(total_rate):
    # Tally's own interactively-saved rows always carry all five duty heads,
    # including the zero-rated Cess/State Cess ones -- a row missing them (only
    # CGST/SGST/IGST) is accepted and stored without error, but is treated as
    # incomplete and never shows as the ledger's effective rate.
    return (("CGST", "Based on Value", total_rate / 2),
            ("SGST/UTGST", "Based on Value", total_rate / 2),
            ("IGST", "Based on Value", total_rate),
            ("Cess", "Not Applicable", Decimal("0")),
            ("State Cess", "Based on Value", Decimal("0")))

def build_master(master, company="", rate_mode="both"):
    root, request = envelope(company); message = ET.SubElement(request, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
    if master["master_type"] == "Unit":
        node = ET.SubElement(message, "UNIT", {"NAME": master["name"], "ACTION": "Create"}); ET.SubElement(node, "NAME").text = master["name"]; ET.SubElement(node, "ISSIMPLEUNIT").text = "Yes"
    elif master["master_type"] == "Stock Item":
        node = ET.SubElement(message, "STOCKITEM", {"NAME": master["name"], "ACTION": "Create"}); ET.SubElement(node, "NAME").text = master["name"]; ET.SubElement(node, "PARENT").text = master.get("group", "Primary"); ET.SubElement(node, "BASEUNITS").text = master.get("unit", "")
        if master.get("hsn_sac"): ET.SubElement(node, "HSNCODE").text = master["hsn_sac"]
    else:
        node = ET.SubElement(message, "LEDGER", {"NAME": master["name"], "ACTION": master.get("action", "Create")}); ET.SubElement(node, "NAME").text = master["name"]; _typed(node, "PARENT", master["group"])
        is_party_alter = master["master_type"] == "Party" and str(master.get("action", "Create")).strip().casefold() == "alter"
        if master.get("gstin"):
            ET.SubElement(node, "PARTYGSTIN").text = master["gstin"]
            ET.SubElement(node, "GSTREGISTRATIONNO").text = master["gstin"]
        if master.get("address"):
            values = ET.SubElement(node, "ADDRESS.LIST", {"TYPE": "String"}); ET.SubElement(values, "ADDRESS").text = master["address"]
        # Verified against a live TallyPrime 7.1 instance: altering an existing
        # ledger that sends both the legacy top-level scalars (LEDSTATENAME/
        # COUNTRYNAME/PINCODE/GSTREGISTRATIONTYPE) and the dated
        # LEDMAILINGDETAILS.LIST/LEDGSTREGDETAILS.LIST aggregates together makes
        # Tally silently ignore *both* -- ALTERED=1 with no error, but a
        # query-back still shows State/Country/GSTIN blank. Sending only the
        # dated aggregates persists correctly. Create is unaffected either way,
        # so the legacy scalars stay for Create (needed by older Tally versions
        # without the dated schema) and are only dropped on Alter.
        is_alter = is_party_alter
        if master.get("state"): ET.SubElement(node, "LEDSTATENAME").text = master["state"]
        if master["master_type"] == "Party":
            ET.SubElement(node, "COUNTRYOFRESIDENCE").text = "India"
            if not is_alter:
                ET.SubElement(node, "COUNTRYNAME").text = "India"
            if master.get("pincode"): ET.SubElement(node, "PINCODE").text = master["pincode"]
            if master.get("registration_type"): ET.SubElement(node, "GSTREGISTRATIONTYPE").text = master["registration_type"]
            applicable = _applicable_from(master)
            if master.get("gstin"):
                registration = ET.SubElement(node, "LEDGSTREGDETAILS.LIST")
                ET.SubElement(registration, "APPLICABLEFROM").text = applicable
                if master.get("registration_type"): ET.SubElement(registration, "GSTREGISTRATIONTYPE").text = master["registration_type"]
                if master.get("state"): ET.SubElement(registration, "STATE").text = master["state"]
                if master.get("place_of_supply") or master.get("state"):
                    ET.SubElement(registration, "PLACEOFSUPPLY").text = master.get("place_of_supply") or master["state"]
                ET.SubElement(registration, "GSTIN").text = master["gstin"]
                # TallyPrime 7.1 silently drops the dated GST registration row
                # for party ledgers unless these GST flags are present.
                ET.SubElement(registration, "ISOTHTERRITORYASSESSEE").text = "No"
                ET.SubElement(registration, "CONSIDERPURCHASEFOREXPORT").text = "No"
                ET.SubElement(registration, "ISTRANSPORTER").text = "No"
                ET.SubElement(registration, "ISCOMMONPARTY").text = "No"
            mailing = ET.SubElement(node, "LEDMAILINGDETAILS.LIST")
            ET.SubElement(mailing, "APPLICABLEFROM").text = applicable
            ET.SubElement(mailing, "MAILINGNAME").text = master["name"]
            if master.get("address"):
                values = ET.SubElement(mailing, "ADDRESS.LIST", {"TYPE": "String"})
                ET.SubElement(values, "ADDRESS").text = master["address"]
            if master.get("state"): ET.SubElement(mailing, "STATE").text = master["state"]
            ET.SubElement(mailing, "COUNTRY").text = "India"
            if master.get("pincode"): ET.SubElement(mailing, "PINCODE").text = master["pincode"]
        if master["master_type"] == "Tax":
            # TallyPrime displays "Central Tax" in the UI but its ledger XML
            # persists/exports that GST duty head as the internal value "CGST".
            duty_head = tally_tax_type(master["tax_type"])
            ET.SubElement(node, "TAXTYPE").text = "GST"; ET.SubElement(node, "GSTDUTYHEAD").text = duty_head
            ET.SubElement(node, "RATEOFTAXCALCULATION").text = str(master.get("gst_rate", "0"))
            ET.SubElement(node, "ROUNDTYPE").text = "Not Applicable"
        if master["master_type"] in ("Sales", "Purchase"):
            # Tally stores accounting GST ledgers with the ledger-level duty
            # type "Others"; without it, Tally may accept the import but drop
            # the nested GST rate-detail rows.
            _typed(node, "TAXTYPE", "Others")
            _typed(node, "GSTAPPLICABLE", "Applicable"); _typed(node, "GSTTYPEOFSUPPLY", master.get("supply_type", "Goods"))
            # The ledger's own name is authoritative for its rate (see
            # account_ledger_rate) -- never trust a possibly-stale/missing
            # gst_rate field alone for a canonically-named ledger.
            ledger_rate = account_ledger_rate(master)
            # RATEOFTAXCALCULATION is the authoritative rate when the company's
            # ledger is configured without a GST rate breakup ("Provide breakup
            # of tax rate" = No) and must be sent on Create AND Alter, or a
            # repair (e.g. 0 -> 18) would leave it unchanged.
            # Strategy A (rate_mode="both", the default) sends the flat rate
            # and the nested breakup together, since either can be the one
            # that's actually effective depending on the company's own
            # "Provide GST rate details" configuration. If a real Tally
            # re-read shows this didn't survive, Strategy B (see service.py's
            # rate-configuration fallback) retries with only one of the two --
            # mirroring a documented Tally quirk already found in this
            # integration where sending an old-style scalar and a new dated
            # aggregate together for a Party ledger makes Tally silently drop
            # both; the same kind of conflict is plausible here.
            if rate_mode in ("both", "flat_only"):
                _typed(node, "RATEOFTAXCALCULATION", _rate_text(ledger_rate), "Number")
            if rate_mode in ("both", "nested_only"):
                taxability = master.get("taxability", "Taxable")
                gst = ET.SubElement(node, "GSTDETAILS.LIST")
                _typed(gst, "APPLICABLEFROM", _applicable_from(master), "Date")
                # Field order within GSTDETAILS.LIST matches a real TallyPrime
                # export verbatim (SUPPLYTYPE/TAXABILITY precede
                # GSTNOTIFICATIONNUMBER/GSTNATUREOFTRANSACTION/NATUREOFGOODS,
                # not after) -- Tally's XML import is order-sensitive for
                # accounting/GST aggregates elsewhere in this codebase (see
                # voucher_builder._taxable_amount), so this is not cosmetic.
                for field in ("GSTNOTIFICATIONDATE", "CALCULATIONTYPE", "REPORTINGUOM",
                              "HSNCODE", "HSNMASTERNAME", "HSN"):
                    _typed(gst, field, "", "Date" if field == "GSTNOTIFICATIONDATE" else "String")
                _typed(gst, "SUPPLYTYPE", master.get("supply_type", "Goods"))
                _typed(gst, "TAXABILITY", taxability)
                for field in ("GSTNOTIFICATIONNUMBER", "GSTNATUREOFTRANSACTION", "NATUREOFGOODS"):
                    _typed(gst, field)
                _typed(gst, "SRCOFGSTDETAILS", "Specify Details Here")
                for field in ("GSTCALCSLABONMRP", "ISREVERSECHARGEAPPLICABLE",
                              "ISNONGSTGOODS", "GSTINELIGIBLEITC", "INCLUDEEXPFORSLABCALC",
                              "ISTAXONMRP"):
                    _typed(gst, field, "", "Logical")
                _typed(gst, "REVERSECHARGERATE", "0", "Number")
                state = ET.SubElement(gst, "STATEWISEDETAILS.LIST")
                # In XML object export, Tally persists the ledger-wide Any-state
                # rate row with an empty STATENAME while JSON export shows it as
                # the internal " Any" marker.
                _typed(state, "STATENAME")
                for head, valuation_type, rate in _gst_rate_detail_heads(ledger_rate):
                    detail = ET.SubElement(state, "RATEDETAILS.LIST"); _typed(detail, "GSTRATEDUTYHEAD", head)
                    _typed(detail, "GSTRATEVALUATIONTYPE", valuation_type); _typed(detail, "GSTRATE", _rate_text(rate), "Number")
                    _typed(detail, "GSTRATEPERUNIT", "0", "Number")
    return ET.tostring(root, encoding="utf-8")

def masters_for(vouchers):
    found = {}
    for voucher in vouchers:
        party = voucher["party"]
        if clean_name(party.get("name")): found[("Party", party["name"].casefold())] = {"master_type": "Party", "name": party["name"], "group": voucher.get("party_group", "Sundry Debtors"), "applicable_from": voucher.get("invoice_date", ""), **party}
        master_type = voucher.get("voucher_type", "Sales")
        account_group = voucher.get("account_group", "Purchase Accounts" if master_type == "Purchase" else "Sales Accounts")
        # Only the GST account ledgers an actual line item in this batch
        # references are required -- not every SUPPORTED_RATES combination
        # for the return type. Creating all seven regardless of what the
        # source data contains was the "extra ledgers" bug: a batch using
        # only 5%/18% would still get 1/3/12/28/40% Purchase or Sales
        # ledgers written into the company.
        for item in voucher["items"]:
            ledger = item.get("account_ledger") or item["sales_ledger"]
            found[(master_type, ledger.casefold())] = {"master_type": master_type, "name": ledger, "group": account_group, "gst_rate": item["gst_rate"], "taxability": item.get("taxability", "Taxable"), "supply_type": item["supply_type"], "applicable_from": voucher.get("invoice_date", "")}
        for tax in voucher.get("tax_allocations", []):
            found[("Tax", tax["ledger"].casefold())] = {"master_type": "Tax", "name": tax["ledger"], "group": "Duties & Taxes", "tax_type": tax["tax_type"], "gst_rate": tax.get("gst_rate", "0")}
        if float(voucher.get("cess") or 0): found[("Tax", "cess")] = {"master_type": "Tax", "name": "Cess", "group": "Duties & Taxes", "tax_type": "Cess"}
        if float(voucher.get("other_charges") or 0): found[("Charge", "other charges")] = {"master_type": "Charge", "name": "Other Charges", "group": "Indirect Incomes"}
        if float(voucher.get("rounding_adjustment") or 0): found[("Charge", "round off")] = {"master_type": "Charge", "name": "Round Off", "group": "Indirect Expenses"}
    order = {"Party": 0, "Sales": 1, "Purchase": 1, "Tax": 2, "Charge": 3}
    return sorted(found.values(), key=lambda row: (order[row["master_type"]], row["name"].casefold()))
