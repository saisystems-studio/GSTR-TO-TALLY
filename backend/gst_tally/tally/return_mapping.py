import re
from dataclasses import dataclass
from decimal import Decimal

from .validators import dec


SUPPORTED_RATES = (Decimal("1"), Decimal("3"), Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28"), Decimal("40"))


def normalize_ledger_key(name):
    """Case/whitespace/``@``-insensitive identity for ledger-name matching.

    ``GST Sales 18%``, ``GSTSales@18%``, ``GST SALES @ 18 %`` and
    ``GSTSales 18%`` all reduce to the same key so an existing Tally ledger
    with any of those spellings is recognised as the same master and never
    duplicated. A normalized key is only a matching *candidate*; callers must
    still confirm the underlying Tally ledger's parent group/rate/taxability
    before reusing it (see service.py's master reconciliation).
    """
    return re.sub(r"[\s@]+", "", str(name or "")).upper()


def extract_rate_from_name(name):
    """Extract the trailing GST percentage encoded in a ledger name, or ``None``."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(name or ""))
    return dec(match.group(1)) if match else None


@dataclass(frozen=True)
class TallyReturnMapping:
    return_type: str
    direction: str
    voucher_type: str
    party_group: str
    account_group: str
    account_prefix: str
    tax_prefix: str

    def account_ledger(self, rate):
        return f"GST {self.account_prefix} {rate_text(rate)}%"

    def tax_ledger(self, component, rate):
        component = str(component).upper()
        tax_rate = dec(rate) if component == "IGST" else dec(rate) / Decimal("2")
        prefix = f"{self.tax_prefix} " if self.tax_prefix else ""
        return f"{prefix}{component} {rate_text(tax_rate)}%"

    def as_dict(self):
        return {
            "return_type": self.return_type,
            "direction": self.direction,
            "voucher_type": self.voucher_type,
            "party_group": self.party_group,
            "account_group": self.account_group,
            "account_prefix": self.account_prefix,
            "tax_prefix": self.tax_prefix,
        }


def normalize_return_type(value):
    return str(value or "").strip().upper().replace("_", "-").replace(" ", "")


def rate_text(value):
    return format(dec(value).normalize(), "f")


def get_tally_mapping(return_type):
    value = normalize_return_type(return_type)
    if value in {"GSTR-1", "GSTR1", "1"}:
        normalized = "GSTR-1"
    elif value in {"GSTR-2A", "GSTR2A", "2A"}:
        normalized = "GSTR-2A"
    elif value in {"GSTR-2B", "GSTR2B", "2B"}:
        normalized = "GSTR-2B"
    else:
        raise ValueError(f"Unsupported GST return type '{return_type or ''}'; accounting direction cannot be determined")
    if normalized == "GSTR-1":
        return TallyReturnMapping(normalized, "sales", "Sales", "Sundry Debtors",
                                  "Sales Accounts", "Sales", "Output")
    return TallyReturnMapping(normalized, "purchase", "Purchase", "Sundry Creditors",
                              "Purchase Accounts", "Purchase", "Input")


def tax_components(cgst, sgst, igst):
    """Source tax fields are authoritative; IGST takes precedence on a mixed row."""
    if dec(igst):
        return ("IGST",)
    if dec(cgst) or dec(sgst):
        return ("CGST", "SGST")
    return ()
