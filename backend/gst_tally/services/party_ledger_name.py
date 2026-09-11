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
    """Resolve one stable Tally ledger name without coupling it to address completeness.

    Priority: existing mapped Tally name -> Sandbox trade name -> Sandbox
    legal name -> source trade name -> source legal name -> source party
    name -> GSTIN fallback only as a last resort. Every candidate (including
    each source fallback) is passed through _real_name() so a GSTIN
    masquerading as a name never outranks a real one further down the chain.
    """
    gstin = clean_party_name(_value(party, "gstin") or _value(source, "gstin") or gstin).upper()
    mapped_name = clean_party_name(mapped_name)
    # A deliberate non-GSTIN Tally mapping identifies the existing ledger that
    # vouchers must reference. A stale GSTIN fallback must not mask a real name.
    if mapped_name and mapped_name.casefold() != gstin.casefold():
        return mapped_name
    candidates = (
        _value(party, "trade_name"),
        _value(party, "legal_name"),
        _value(source, "trade_name"),
        _value(source, "legal_name"),
        _value(source, "party_name", "name"),
    )
    for candidate in candidates:
        name = _real_name(candidate, gstin)
        if name:
            return name
    return gstin
