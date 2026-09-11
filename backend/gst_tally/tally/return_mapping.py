import re
from dataclasses import dataclass
from decimal import Decimal

from .validators import dec


SUPPORTED_RATES = (Decimal("1"), Decimal("3"), Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28"), Decimal("40"))


def normalize_ledger_key(name):
    return re.sub(r"[\s@]+", "", str(name or "")).upper()


def extract_rate_from_name(name):
    """Extract the trailing GST percentage encoded in a ledger name, or ``None``."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(name or ""))
    return dec(match.group(1)) if match else None


# Fixed, non-rate-suffixed Duties & Taxes ledgers used only when a source row
# genuinely has no GST rate at all (rate_available=False) -- the tax
# component amounts themselves (never a derived/assumed rate) still decide
# which of these is posted. Deliberately asymmetric with the rate-wise
# tax_ledger() naming ("Input CGST 9%" uses "SGST", not "SGST/UTGST", and
# GSTR-1's own common ledgers carry no "Output" prefix at all) -- this is a
# distinct, explicitly-specified naming set, not a generalization of it.
_COMMON_TAX_LABELS = {"CGST": "CGST", "SGST": "SGST/UTGST", "IGST": "IGST", "CESS": "Cess"}


def common_tax_ledger(direction, component):
    label = _COMMON_TAX_LABELS[str(component).upper()]
    return f"GST Input {label}" if direction == "purchase" else f"GST {label}"


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

    def common_account_ledger(self):
        """The single, non-rate-suffixed Sales/Purchase account ledger used
        when a source row has no GST rate at all (rate_available=False) --
        distinct from a genuine rate-wise ledger (e.g. "GST Sales 0%") and
        from the exempt ledger below."""
        return f"GST {self.account_prefix}"

    def exempt_account_ledger(self):
        """The single, return-type-directional exempt-supply ledger -- used
        only when the source explicitly identifies the row as exempt (never
        merely because GST rate is 0 or missing)."""
        return f"GST {self.account_prefix} Exempted"

    def tax_ledger(self, component, rate):
        component = str(component).upper()
        tax_rate = dec(rate) if component == "IGST" else dec(rate) / Decimal("2")
        prefix = f"{self.tax_prefix} " if self.tax_prefix else ""
        return f"{prefix}{component} {rate_text(tax_rate)}%"

    def common_tax_ledger(self, component):
        return common_tax_ledger(self.direction, component)

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
        return TallyReturnMapping(normalized, "sales", "Sales", "Sundry Debtors","Sales Accounts", "Sales", "Output")
    return TallyReturnMapping(normalized, "purchase", "Purchase", "Sundry Creditors","Purchase Accounts", "Purchase", "Input")


def tax_components(cgst, sgst, igst):
    """Source tax fields are authoritative; IGST takes precedence on a mixed row."""
    if dec(igst):
        return ("IGST",)
    if dec(cgst) or dec(sgst):
        return ("CGST", "SGST")
    return ()
