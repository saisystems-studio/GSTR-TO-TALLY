import re

# Tally's own product-release fields (prod_maj_rel/prod_min_rel) are what
# "detected" means here; this tuple is the fixed *business rule* threshold
# ("Tally 7.1 and above -> JSON"), not a guessed/hardcoded detected version.
MIN_JSON_VERSION = (7, 1)


def parse_tally_version(value):
    """Parse a Tally version string/number into a comparable (major, minor)
    tuple for semantic (not lexical) comparison -- "10.0" must sort above
    "7.1", which a plain string comparison gets wrong.

    Accepts "7.1", "7", 7.1, "Release 7.1.2", etc. Returns None if no numeric
    version can be found at all; callers must not guess when this happens.
    """
    if value is None or value == "":
        return None
    numbers = re.findall(r"\d+", str(value))
    if not numbers:
        return None
    major = int(numbers[0])
    minor = int(numbers[1]) if len(numbers) > 1 else 0
    return (major, minor)


def format_tally_version(version_tuple):
    if not version_tuple:
        return ""
    major, minor = version_tuple
    return f"{major}.{minor}"


def select_transport(version_tuple):
    """The one transport router: version < 7.1 -> XML, version >= 7.1 -> JSON.

    Returns None (never a guess) when the version itself is unknown; the
    caller must treat that as TALLY_VERSION_UNAVAILABLE rather than picking
    a transport.
    """
    if not version_tuple:
        return None
    return "JSON" if version_tuple >= MIN_JSON_VERSION else "XML"
