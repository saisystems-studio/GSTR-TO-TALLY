from django.conf import settings

from .models import GSTINConfigurationError
from .providers import ClearTaxGSTINProvider


class GSTINLookupService:
    @classmethod
    def configuration_issues(cls):
        issues = []
        if not settings.GST_TAXPAYER_API_ENABLED: issues.append("GST taxpayer API is disabled")
        if not settings.GST_TAXPAYER_PROVIDER: issues.append("GST taxpayer provider is missing")
        elif settings.GST_TAXPAYER_PROVIDER != "cleartax": issues.append("GST taxpayer provider is unsupported")
        if settings.GST_TAXPAYER_PROVIDER == "cleartax":
            if not settings.CLEARTAX_GST_BASE_URL: issues.append("ClearTax base URL is missing")
            if not settings.CLEARTAX_GST_TAXABLE_ENTITY_ID: issues.append("ClearTax taxable entity ID is missing")
            if not settings.CLEARTAX_GST_AUTH_TOKEN: issues.append("ClearTax auth token is missing")
            if not settings.CLEARTAX_ALLOW_DEPRECATED_API:
                issues.append("ClearTax GST 1.0 taxpayer endpoint is deprecated; explicit opt-in is required")
        return issues

    @classmethod
    def configured(cls):
        return not cls.configuration_issues()

    @classmethod
    def from_settings(cls):
        issues = cls.configuration_issues()
        if issues: raise GSTINConfigurationError("; ".join(issues))
        return cls(ClearTaxGSTINProvider(
            settings.CLEARTAX_GST_BASE_URL,
            settings.CLEARTAX_GST_TAXABLE_ENTITY_ID,
            settings.CLEARTAX_GST_AUTH_TOKEN,
            timeout=settings.GST_TAXPAYER_API_TIMEOUT,
        ))

    def __init__(self, provider): self.provider = provider
    def lookup(self, gstin): return self.provider.get_gstin_details(gstin).as_dict()
