"""User-facing normalization of a Tally voucher import failure.

Turns the technical detail this package already collects (Tally's own
LINEERROR/EXCEPTIONMESSAGE/DESCRIPTION text, or one of this app's own
deterministic pre-import validation codes -- see service.py) into the fixed,
plain-language vocabulary the Step 6 UI renders. Never invents a cause: a
specific error_code is only returned when either (a) the caller already
supplies one of this app's own deterministic error codes (a real ledger name
was actually missing, a real balance check actually failed, ...), or (b) the
real Tally response text contains one of the known signatures below. Anything
else falls back to UNKNOWN_TALLY_EXCEPTION -- "Tally rejected this voucher,
but did not provide a specific reason" -- rather than guessing (task spec
section 11).
"""
import re

UNKNOWN_TALLY_EXCEPTION = "UNKNOWN_TALLY_EXCEPTION"

_DEFINITIONS = {
    "LEDGER_NOT_FOUND": {
        "title": "Required Ledger Not Found",
        "user_message": "The required ledger for this voucher is not available in Tally.",
        "action_message": "Verify the ledger in Tally and retry the import.",
        "retryable": True,
    },
    "PARTY_LEDGER_NOT_FOUND": {
        "title": "Party Not Available in Tally",
        "user_message": "The party ledger for {party} is not available in Tally.",
        "action_message": "Create or verify the party ledger and retry.",
        "retryable": True,
    },
    "DATE_OUTSIDE_TALLY_PERIOD": {
        "title": "Invoice Date Not Available in Current Tally Period",
        "user_message": "The invoice date {date} is outside the currently available Tally period.",
        "action_message": "Open the required financial year/period in Tally and retry.",
        "retryable": True,
    },
    "DUPLICATE_VOUCHER": {
        "title": "Voucher Already Exists",
        "user_message": "Voucher No. {invoice_no} already exists in Tally.",
        "action_message": "No duplicate voucher was created. Verify the existing voucher before retrying.",
        "retryable": False,
    },
    "AMOUNT_MISMATCH": {
        "title": "Voucher Total Mismatch",
        "user_message": "The debit and credit values of this voucher do not match.",
        "action_message": "Review the voucher amounts and retry.",
        "retryable": True,
    },
    "TALLY_CONNECTION_LOST": {
        "title": "Tally Connection Lost",
        "user_message": "The connection to Tally was interrupted during import.",
        "action_message": "Keep Tally open and click Retry Import.",
        "retryable": True,
    },
    "COMPANY_NOT_AVAILABLE": {
        "title": "Tally Company Not Available",
        "user_message": "The verified Tally company is no longer available.",
        "action_message": "Open the correct company in Tally and retry.",
        "retryable": True,
    },
    UNKNOWN_TALLY_EXCEPTION: {
        "title": "Import Could Not Be Completed",
        "user_message": "Tally rejected this voucher, but did not provide a specific reason.",
        "action_message": "Please verify the voucher details and Tally configuration, then retry.",
        "retryable": True,
    },
}

# This app's own deterministic error codes (set only when the app itself
# already knows the exact cause -- a real required-master lookup failed, a
# real balance check failed, and so on -- see service.py) map straight to the
# matching plain-language type without needing to re-scan any text.
_CODE_ALIASES = {
    "TALLY_MASTER_RATE_MISMATCH": "LEDGER_NOT_FOUND",
    "VOUCHER_NOT_BALANCED": "AMOUNT_MISMATCH",
    "TALLY_DATE_OUT_OF_RANGE": "DATE_OUTSIDE_TALLY_PERIOD",
    "TALLY_QUERY_FAILED": "TALLY_CONNECTION_LOST",
    "TALLY_CONNECTION_REFUSED": "TALLY_CONNECTION_LOST",
    "TALLY_CONNECTION_TIMEOUT": "TALLY_CONNECTION_LOST",
    "TALLY_READ_TIMEOUT": "TALLY_CONNECTION_LOST",
    "TALLY_CONNECT_TIMEOUT": "TALLY_CONNECTION_LOST",
    "TALLY_HOST_UNREACHABLE": "TALLY_CONNECTION_LOST",
    "TALLY_PORT_UNREACHABLE": "TALLY_CONNECTION_LOST",
    "TALLY_DISABLED": "TALLY_CONNECTION_LOST",
    "TALLY_INVALID_RESPONSE": "TALLY_CONNECTION_LOST",
    "TALLY_EXISTING_NOT_VERIFIED": "TALLY_CONNECTION_LOST",
    "GSTIN_MISMATCH": "COMPANY_NOT_AVAILABLE",
    "COMPANY_NOT_OPEN": "COMPANY_NOT_AVAILABLE",
    "TALLY_EXCEPTION_WITHOUT_MESSAGE": UNKNOWN_TALLY_EXCEPTION,
    "UNEXPECTED_ERROR": UNKNOWN_TALLY_EXCEPTION,
}

# Deterministic text signatures actually seen in real Tally LINEERROR /
# EXCEPTIONMESSAGE / DESCRIPTION text. Checked in order; the first match
# wins. Every pattern is anchored to language Tally itself genuinely uses --
# never a generic guess from an unrelated counter (task spec section 11).
_TEXT_SIGNATURES = [
    ("DUPLICATE_VOUCHER", re.compile(r"already exists|duplicate", re.IGNORECASE)),
    (
        "PARTY_LEDGER_NOT_FOUND",
        re.compile(
            r"party ledger.*(does not exist|not found|unavailable)|"
            r"(?:ledger|account).*party.*does not exist",
            re.IGNORECASE,
        ),
    ),
    (
        "LEDGER_NOT_FOUND",
        re.compile(
            r"ledger.*(does not exist|not found|unavailable|could not be found)|"
            r"unable to alloc|invalid ledger",
            re.IGNORECASE,
        ),
    ),
    (
        "DATE_OUTSIDE_TALLY_PERIOD",
        re.compile(
            r"voucher date is (missing|invalid|outside)|date is outside|"
            r"period.*(closed|not open)|outside.*period",
            re.IGNORECASE,
        ),
    ),
    (
        "AMOUNT_MISMATCH",
        re.compile(
            r"debit.*credit.*(mismatch|not equal|do not match)|"
            r"voucher.*(not balanced|does not balance)|totals? do not (match|balance)",
            re.IGNORECASE,
        ),
    ),
    (
        "COMPANY_NOT_AVAILABLE",
        re.compile(
            r"company.*(not open|unavailable|not available|could not be (found|opened))",
            re.IGNORECASE,
        ),
    ),
]


def _fill(template, **context):
    try:
        return template.format(**context)
    except (KeyError, IndexError):
        return template


def _detect_from_text(text):
    for code, pattern in _TEXT_SIGNATURES:
        if pattern.search(text or ""):
            return code
    return None


def normalize_tally_error(
    error_code="",
    technical_message="",
    invoice_no="",
    party="",
    date="",
    retryable=None,
):
    """Build the fixed {error_code, title, user_message, action_message,
    technical_message, retryable} shape the UI renders (task spec section 3).

    `error_code` is trusted first when it already names one of this app's own
    deterministic pre-import codes; otherwise the actual Tally response text
    is scanned for a known signature. Unmatched input returns
    UNKNOWN_TALLY_EXCEPTION rather than a fabricated reason.
    """
    code = _CODE_ALIASES.get(error_code)
    if code is None and error_code in _DEFINITIONS:
        code = error_code
    if code is None:
        code = _detect_from_text(technical_message)
    if code is None:
        code = UNKNOWN_TALLY_EXCEPTION
    definition = _DEFINITIONS[code]
    context = {
        "invoice_no": invoice_no or "this voucher",
        "party": party or "the party",
        "date": date or "the invoice date",
    }
    return {
        "error_code": code,
        "title": definition["title"],
        "user_message": _fill(definition["user_message"], **context),
        "action_message": definition["action_message"],
        "technical_message": technical_message or "",
        "retryable": definition["retryable"] if retryable is None else retryable,
    }
