from django.conf import settings

from .providers import provider_class
from .providers.generic_rest import (ProviderError, ProviderNotFound, ProviderRateLimited,
                                     ProviderTimeout, ProviderUnauthorized)

class TaxpayerNotFound(Exception): pass
class TaxpayerUnauthorized(Exception): pass
class TaxpayerRateLimited(Exception): pass
class TaxpayerTimeout(Exception): pass
class TaxpayerAPIError(Exception): pass

class GSTTaxpayerService:
    @classmethod
    def configuration_issues(cls):
        issues = []
        if not settings.GST_TAXPAYER_API_ENABLED: issues.append("API is disabled")
        if not settings.GST_TAXPAYER_PROVIDER: issues.append("provider is missing")
        else:
            try: provider_class(settings.GST_TAXPAYER_PROVIDER)
            except ValueError: issues.append("provider is unsupported")
        if not settings.GST_TAXPAYER_API_BASE_URL: issues.append("base URL is missing")
        elif settings.GST_TAXPAYER_PROVIDER == "generic_rest" and "{gstin}" not in settings.GST_TAXPAYER_API_BASE_URL:
            issues.append("base URL must contain {gstin}")
        if settings.GST_TAXPAYER_PROVIDER == "generic_rest" and not settings.GST_TAXPAYER_API_KEY:
            issues.append("API key is missing")
        return issues

    @classmethod
    def configured(cls):
        return not cls.configuration_issues()

    @classmethod
    def from_settings(cls):
        issues = cls.configuration_issues()
        if issues: raise TaxpayerAPIError(f"GST taxpayer lookup is not configured ({'; '.join(issues)})")
        config = {"base_url": settings.GST_TAXPAYER_API_BASE_URL, "api_key": settings.GST_TAXPAYER_API_KEY,
                  "api_key_header": settings.GST_TAXPAYER_API_KEY_HEADER, "username": settings.GST_TAXPAYER_API_USERNAME,
                  "password": settings.GST_TAXPAYER_API_PASSWORD, "client_id": settings.GST_TAXPAYER_API_CLIENT_ID,
                  "client_secret": settings.GST_TAXPAYER_API_CLIENT_SECRET, "timeout": settings.GST_TAXPAYER_API_TIMEOUT}
        try: return cls(provider_class(settings.GST_TAXPAYER_PROVIDER)(config))
        except ValueError as exc: raise TaxpayerAPIError(str(exc)) from exc

    def __init__(self, provider): self.provider = provider

    def fetch_taxpayer(self, gstin):
        try:
            result = self.provider.fetch(gstin)
            return {
                "gstin": str(result.get("gstin", gstin)).strip().upper(),
                "trade_name": str(result.get("trade_name", "")).strip(),
                "principal_place_of_business": str(
                    result.get("principal_place_of_business") or result.get("address") or ""
                ).strip(),
                "lookup_source": settings.GST_TAXPAYER_PROVIDER,
                "lookup_status": "Ready",
            }
        except ProviderNotFound as exc: raise TaxpayerNotFound(gstin) from exc
        except ProviderUnauthorized as exc: raise TaxpayerUnauthorized() from exc
        except ProviderRateLimited as exc: raise TaxpayerRateLimited() from exc
        except ProviderTimeout as exc: raise TaxpayerTimeout() from exc
        except ProviderError as exc: raise TaxpayerAPIError(str(exc)) from exc
