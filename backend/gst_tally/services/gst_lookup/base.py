from abc import ABC, abstractmethod
from dataclasses import dataclass, field


def has_usable_text(value):
    return str(value or "").strip().lower() not in {"", "-", "none", "null"}


@dataclass(frozen=True)
class GSTLookupResult:
    gstin: str
    legal_name: str | None = None
    trade_name: str | None = None
    status: str | None = None
    principal_address: str | None = None
    state: str | None = None
    state_code: str | None = None
    registration_date: str | None = None
    taxpayer_type: str | None = None
    pincode: str | None = None
    cancellation_date: str | None = None
    constitution_of_business: str | None = None
    einvoice_status: str | None = None
    nature_of_business: list = field(default_factory=list)
    state_jurisdiction: str | None = None
    centre_jurisdiction: str | None = None
    pan: str | None = None
    state_jurisdiction_code: str | None = None
    centre_jurisdiction_code: str | None = None
    last_updated_date: str | None = None
    principal_address_details: dict = field(default_factory=dict)
    principal_business_nature: list = field(default_factory=list)
    additional_places: list = field(default_factory=list)

    def as_dict(self):
        return {
            "gstin": self.gstin,
            "legal_name": self.legal_name,
            "trade_name": self.trade_name,
            "status": self.status,
            "principal_address": self.principal_address,
            "state": self.state,
            "state_code": self.state_code,
            "registration_date": self.registration_date,
            "taxpayer_type": self.taxpayer_type,
            "pincode": self.pincode,
            "cancellation_date": self.cancellation_date,
            "constitution_of_business": self.constitution_of_business,
            "einvoice_status": self.einvoice_status,
            "nature_of_business": self.nature_of_business,
            "state_jurisdiction": self.state_jurisdiction,
            "centre_jurisdiction": self.centre_jurisdiction,
            "central_jurisdiction": self.centre_jurisdiction,
            "pan": self.pan,
            "state_jurisdiction_code": self.state_jurisdiction_code,
            "centre_jurisdiction_code": self.centre_jurisdiction_code,
            "last_updated_date": self.last_updated_date,
            "principal_address_details": self.principal_address_details,
            "principal_business_nature": self.principal_business_nature,
            "additional_places": self.additional_places,
        }


class GSTLookupError(Exception): pass
class GSTLookupConfigurationError(GSTLookupError): pass
class GSTLookupAuthenticationError(GSTLookupError): pass
class GSTLookupNotFoundError(GSTLookupError): pass
class GSTLookupRateLimitError(GSTLookupError):
    def __init__(self, provider=None, retry_after=None):
        super().__init__("Provider rate limit reached")
        self.provider = provider
        self.retry_after = retry_after
class GSTLookupTimeoutError(GSTLookupError): pass
class GSTLookupProviderError(GSTLookupError): pass


class GSTLookupProvider(ABC):
    @abstractmethod
    def lookup(self, gstin):
        raise NotImplementedError
