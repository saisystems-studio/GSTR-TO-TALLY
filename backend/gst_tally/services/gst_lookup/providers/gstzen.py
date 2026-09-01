from django.conf import settings

from ..base import GSTLookupConfigurationError, GSTLookupProvider


class GSTZenProvider(GSTLookupProvider):
    """Activation boundary for GSTZen's authorized GST Validator API.

    GSTZen's public pages advertise the product but do not publish the endpoint,
    authentication headers, or JSON response schema. This adapter deliberately
    performs no HTTP request until that official contract is supplied.
    """

    @classmethod
    def settings_config(cls):
        return {"base_url": settings.GSTZEN_BASE_URL, "endpoint": settings.GSTZEN_API_ENDPOINT,
                "api_key": settings.GSTZEN_API_KEY, "client_id": settings.GSTZEN_CLIENT_ID,
                "client_secret": settings.GSTZEN_CLIENT_SECRET, "timeout": settings.GST_LOOKUP_TIMEOUT}

    @classmethod
    def configuration_issues(cls):
        config = cls.settings_config()
        missing = []
        if not config["base_url"]: missing.append("GSTZEN_BASE_URL")
        if not config["endpoint"]: missing.append("GSTZEN_API_ENDPOINT")
        if not config["api_key"]: missing.append("GSTZEN_API_KEY")
        # Even apparently complete values cannot be trusted until the vendor's
        # endpoint/auth/response contract has been reviewed and implemented.
        missing.append("GSTZEN_OFFICIAL_API_CONTRACT")
        return missing

    @classmethod
    def from_settings(cls):
        raise GSTLookupConfigurationError(
            "GSTZen official Validator API endpoint, authentication contract, and response schema are required"
        )

    def lookup(self, gstin):
        raise GSTLookupConfigurationError("GSTZen official API contract is not configured")
