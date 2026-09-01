import logging

from gst_tally.utils.file_utils import normalize_header

logger = logging.getLogger(__name__)

TRADE_NAME_ALIASES = (
    "trade_name", "tradeName", "tradeNam", "trdnm", "seller_trade_name",
    "supplier_trade_name", "SellerDtls.TrdNm", "Supplier_Trade_Name",
    "Trade/Legal name", "Supplier Name", "Party Name",
)
ADDRESS_ALIASES = (
    "principal_place_of_business", "principalPlaceOfBusiness", "pradr", "address",
    "seller_address", "supplier_address", "Supplier_Address", "place_of_business",
)
ADDRESS_COMPONENTS = (
    ("address1", ("SellerDtls.Addr1", "Supplier_Address1", "Addr1", "address_line_1", "address1", "bno")),
    ("address2", ("SellerDtls.Addr2", "Supplier_Address2", "Addr2", "address_line_2", "address2", "bnm", "street")),
    ("place", ("SellerDtls.Loc", "place", "location", "loc", "city")),
    ("district", ("district", "dst")),
    ("state", ("SellerDtls.Stcd", "state", "state_name", "stateName", "stcd")),
    ("pincode", ("SellerDtls.Pin", "pincode", "pin", "pncd")),
)


def _flatten(value, prefix=""):
    flattened = {}
    if not isinstance(value, dict): return flattened
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict): flattened.update(_flatten(child, path))
        else: flattened[path] = child
    return flattened


def _find(source, aliases):
    flattened = _flatten(source)
    indexed = {}
    for path, value in flattened.items():
        indexed.setdefault(normalize_header(path), (value, path))
        indexed.setdefault(normalize_header(path.rsplit(".", 1)[-1]), (value, path))
    for alias in aliases:
        found = indexed.get(normalize_header(alias))
        if found and found[0] not in (None, ""):
            return str(found[0]).strip(), found[1]
    return "", ""


def extract_source_party(*sources):
    trade_name = trade_source = address = address_source = ""
    components = {}
    component_sources = {}
    for source in sources:
        if not isinstance(source, dict): continue
        if not trade_name: trade_name, trade_source = _find(source, TRADE_NAME_ALIASES)
        if not address:
            value, path = _find(source, ADDRESS_ALIASES)
            if value and not value.startswith("{"): address, address_source = value, path
        for label, aliases in ADDRESS_COMPONENTS:
            if label not in components:
                value, path = _find(source, aliases)
                if value: components[label], component_sources[label] = value, path
    if not address:
        ordered = [components.get(key, "") for key in ("address1", "address2", "place", "district", "state")]
        parts = []
        for value in ordered:
            if value and value not in parts: parts.append(value)
        pincode = components.get("pincode", "")
        text = ", ".join(parts)
        address = f"{text} - {pincode}" if text and pincode else (text or pincode)
        address_source = " + ".join(component_sources.values())
    return {"trade_name": trade_name, "principal_place_of_business": address,
            "trade_name_source": trade_source or "not present", "address_source": address_source or "not present"}


def add_source_party(parties, gstin, *sources):
    gstin = str(gstin or "").strip().upper()
    if not gstin: return
    found = extract_source_party(*sources)
    current = parties.setdefault(gstin, {"trade_name": "", "principal_place_of_business": "",
                                         "trade_name_source": "not present", "address_source": "not present"})
    for field in ("trade_name", "principal_place_of_business"):
        if not current[field] and found[field]:
            current[field] = found[field]
            current[f"{field.replace('principal_place_of_business', 'address')}_source"] = found[
                f"{field.replace('principal_place_of_business', 'address')}_source"]


def log_source_parties(parties):
    for gstin, party in parties.items():
        logger.info("GSTIN %s trade_name source = %s; address source = %s", gstin,
                    party.get("trade_name_source", "not present"), party.get("address_source", "not present"))
