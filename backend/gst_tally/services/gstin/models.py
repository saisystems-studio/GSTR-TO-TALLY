from dataclasses import dataclass


@dataclass(frozen=True)
class GSTINDetails:
    gstin: str
    trade_name: str
    principal_place_of_business: str

    def as_dict(self):
        return {
            "gstin": self.gstin,
            "trade_name": self.trade_name,
            "principal_place_of_business": self.principal_place_of_business,
        }


class GSTINProviderError(Exception): pass
class GSTINConfigurationError(GSTINProviderError): pass
class GSTINAuthenticationError(GSTINProviderError): pass
class GSTINNotFoundError(GSTINProviderError): pass
class GSTINRateLimitError(GSTINProviderError): pass
class GSTINTimeoutError(GSTINProviderError): pass
class GSTINUnavailableError(GSTINProviderError): pass
class GSTINResponseError(GSTINProviderError): pass
