import re


INVALID_NAMES = {"", "-", "none", "null", "unknown"}


def clean_party_name(value):
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    return "" if name.casefold() in INVALID_NAMES else name


def _value(source, *names):
    if not source:
        return ""
    for name in names:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, "")
        if clean_party_name(value):
            return clean_party_name(value)
    return ""


def _real_name(value, gstin):
    name = clean_party_name(value)
    return "" if gstin and name.casefold() == gstin.casefold() else name


def resolve_party_ledger_name(party=None, source=None, gstin="", mapped_name=""):
    """Resolve one stable Tally ledger name without coupling it to address completeness."""
    gstin = clean_party_name(_value(party, "gstin") or _value(source, "gstin") or gstin).upper()
    trade_name = _real_name(_value(party, "trade_name"), gstin) or _value(source, "trade_name")
    legal_name = _real_name(_value(party, "legal_name"), gstin) or _value(source, "legal_name")
    party_name = (_real_name(_value(party, "party_name", "name"), gstin) or
                  _value(source, "party_name", "name"))
    mapped_name = clean_party_name(mapped_name)
    # A deliberate non-GSTIN Tally mapping identifies the existing ledger that
    # vouchers must reference. A stale GSTIN fallback must not mask a real name.
    if mapped_name and mapped_name.casefold() != gstin.casefold():
        return mapped_name
    return trade_name or legal_name or party_name or gstin
