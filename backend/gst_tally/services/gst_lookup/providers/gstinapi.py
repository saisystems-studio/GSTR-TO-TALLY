import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings

from ..base import (
    GSTLookupAuthenticationError, GSTLookupNotFoundError, GSTLookupProvider,
    GSTLookupProviderError, GSTLookupRateLimitError, GSTLookupResult,
    GSTLookupTimeoutError, has_usable_text,
)


def _text(value):
    value = str(value or "").strip()
    return value if has_usable_text(value) else None


class GSTINAPIProvider(GSTLookupProvider):
    def __init__(self, config, opener=urlopen):
        self.config = config
        self.opener = opener
        self.last_http_status = None

    @classmethod
    def settings_config(cls):
        return {
            "base_url": settings.GSTINAPI_BASE_URL,
            "endpoint": settings.GSTINAPI_ENDPOINT,
            "api_key": settings.GSTINAPI_API_KEY,
            "api_key_header": settings.GSTINAPI_API_KEY_HEADER,
            "timeout": settings.GST_LOOKUP_TIMEOUT,
        }

    @classmethod
    def configuration_issues(cls):
        config = cls.settings_config()
        names = {"base_url": "GSTINAPI_BASE_URL", "endpoint": "GSTINAPI_ENDPOINT",
                 "api_key": "GSTINAPI_API_KEY", "api_key_header": "GSTINAPI_API_KEY_HEADER"}
        missing = [name for key, name in names.items() if not config[key]]
        if config["endpoint"] and "{gstin}" not in config["endpoint"]:
            missing.append("GSTINAPI_ENDPOINT:{gstin}")
        return missing

    @classmethod
    def from_settings(cls):
        return cls(cls.settings_config())

    def lookup(self, gstin):
        template = f'{self.config["base_url"].rstrip("/")}/{self.config["endpoint"].lstrip("/")}'
        url = template.format(gstin=quote(gstin, safe=""))
        headers = {"Accept": "application/json", self.config["api_key_header"]: self.config["api_key"]}
        payload = None
        for attempt in range(2):
            try:
                with self.opener(Request(url, headers=headers, method="GET"), timeout=self.config["timeout"]) as response:
                    self.last_http_status = getattr(response, "status", 200)
                    payload = json.loads(response.read().decode("utf-8"))
                    break
            except HTTPError as exc:
                self.last_http_status = exc.code
                if exc.code == 404: raise GSTLookupNotFoundError(gstin) from exc
                if exc.code in (401, 403): raise GSTLookupAuthenticationError() from exc
                if exc.code == 429:
                    raise GSTLookupRateLimitError("gstinapi", exc.headers.get("Retry-After") if exc.headers else None) from exc
                if exc.code == 502 and attempt == 0: continue
                if exc.code == 402: raise GSTLookupProviderError("Fallback provider credits exhausted") from exc
                if exc.code >= 500: raise GSTLookupProviderError("fallback_5xx") from exc
                raise GSTLookupProviderError(f"Fallback provider returned HTTP {exc.code}") from exc
            except (TimeoutError, socket.timeout) as exc:
                if attempt == 0: continue
                raise GSTLookupTimeoutError() from exc
            except (ConnectionResetError, ConnectionAbortedError) as exc:
                if attempt == 0: continue
                raise GSTLookupProviderError("Fallback provider connection was reset") from exc
            except URLError as exc:
                temporary = isinstance(exc.reason, (TimeoutError, socket.timeout, ConnectionResetError, ConnectionAbortedError))
                if temporary and attempt == 0: continue
                if isinstance(exc.reason, (TimeoutError, socket.timeout)): raise GSTLookupTimeoutError() from exc
                raise GSTLookupProviderError("Fallback provider connection failed") from exc
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise GSTLookupProviderError("fallback_invalid_json") from exc

        if not isinstance(payload, dict): raise GSTLookupProviderError("fallback_invalid_json")
        # GSTINAPI may return either a flat taxpayer object or a nested data object.
        # A successful 2xx response is identified by its GSTIN, not a Jamku-style success flag.
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not data: raise GSTLookupProviderError("fallback_empty_response")
        returned_gstin = _text(data.get("gstin"))
        if not returned_gstin: raise GSTLookupProviderError("fallback_schema_mismatch")
        return GSTLookupResult(
            gstin=returned_gstin.upper(), trade_name=_text(data.get("trade_name")),
            legal_name=_text(data.get("legal_name")), principal_address=_text(data.get("address")),
            status=_text(data.get("status")), taxpayer_type=_text(data.get("taxpayer_type")),
            registration_date=_text(data.get("registration_date")),
            cancellation_date=_text(data.get("cancellation_date")),
            constitution_of_business=_text(data.get("business_constitution")),
            state=_text(data.get("state")), pincode=_text(data.get("pincode")),
            state_jurisdiction=_text(data.get("state_jurisdiction")),
        )
