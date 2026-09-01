import json
import logging
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings

from gst_tally.tally.validators import state_name

from ..base import (
    GSTLookupAuthenticationError,
    GSTLookupNotFoundError,
    GSTLookupProvider,
    GSTLookupProviderError,
    GSTLookupRateLimitError,
    GSTLookupResult,
    GSTLookupTimeoutError,
    has_usable_text,
)


PINCODE_AT_END = re.compile(r"\b(\d{6})\s*$")
logger = logging.getLogger(__name__)


def _text(value):
    value = str(value or "").strip()
    return value if has_usable_text(value) else None


class JamkuProvider(GSTLookupProvider):
    def __init__(self, config, opener=urlopen):
        self.config = config
        self.opener = opener
        self.last_http_status = None

    @classmethod
    def settings_config(cls):
        return {
            "base_url": settings.JAMKU_BASE_URL,
            "endpoint": settings.JAMKU_GSTIN_ENDPOINT,
            "host": settings.JAMKU_RAPIDAPI_HOST,
            "api_key": settings.JAMKU_RAPIDAPI_KEY,
            "timeout": settings.GST_LOOKUP_TIMEOUT,
        }

    @classmethod
    def configuration_issues(cls):
        config = cls.settings_config()
        names = {
            "base_url": "JAMKU_BASE_URL",
            "endpoint": "JAMKU_GSTIN_ENDPOINT",
            "host": "JAMKU_RAPIDAPI_HOST",
            "api_key": "JAMKU_RAPIDAPI_KEY",
        }
        missing = [name for key, name in names.items() if not config[key]]
        if config["endpoint"] and "{gstin}" not in config["endpoint"]:
            missing.append("JAMKU_GSTIN_ENDPOINT:{gstin}")
        return missing

    @classmethod
    def from_settings(cls):
        return cls(cls.settings_config())

    def lookup(self, gstin):
        template = f'{self.config["base_url"].rstrip("/")}/{self.config["endpoint"].lstrip("/")}'
        url = template.format(gstin=quote(gstin, safe=""))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-rapidapi-host": self.config["host"],
            "x-rapidapi-key": self.config["api_key"],
        }
        payload = None
        for attempt in range(2):
            try:
                with self.opener(Request(url, headers=headers, method="GET"), timeout=self.config["timeout"]) as response:
                    http_status = getattr(response, "status", 200)
                    self.last_http_status = http_status
                    payload = json.loads(response.read().decode("utf-8"))
                    logger.info("GST lookup gstin=%s http_status=%s", gstin, http_status)
                    break
            except HTTPError as exc:
                self.last_http_status = exc.code
                logger.info("GST lookup gstin=%s http_status=%s error_code=HTTP_%s", gstin, exc.code, exc.code)
                if exc.code == 404: raise GSTLookupNotFoundError(gstin) from exc
                if exc.code in (401, 403): raise GSTLookupAuthenticationError() from exc
                if exc.code == 429:
                    raise GSTLookupRateLimitError("jamku", exc.headers.get("Retry-After") if exc.headers else None) from exc
                if exc.code in (500, 502, 503, 504) and attempt == 0: continue
                raise GSTLookupProviderError(f"Provider returned HTTP {exc.code}") from exc
            except (TimeoutError, socket.timeout) as exc:
                if attempt == 0: continue
                raise GSTLookupTimeoutError() from exc
            except (ConnectionResetError, ConnectionAbortedError) as exc:
                if attempt == 0: continue
                raise GSTLookupProviderError("Provider connection was reset") from exc
            except URLError as exc:
                temporary = isinstance(exc.reason, (TimeoutError, socket.timeout, ConnectionResetError, ConnectionAbortedError))
                if temporary and attempt == 0: continue
                if isinstance(exc.reason, (TimeoutError, socket.timeout)): raise GSTLookupTimeoutError() from exc
                raise GSTLookupProviderError("Provider connection failed") from exc
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise GSTLookupProviderError("Malformed provider response") from exc

        if not isinstance(payload, dict):
            raise GSTLookupProviderError("Provider response is not an object")
        if payload.get("success") is not True:
            raise GSTLookupNotFoundError(gstin)
        data = payload.get("data")
        if data in (None, {}, []):
            raise GSTLookupNotFoundError(gstin)
        if not isinstance(data, dict):
            raise GSTLookupProviderError("Malformed provider response")
        returned_gstin = _text(data.get("gstin"))
        if not returned_gstin:
            raise GSTLookupProviderError("Provider response GSTIN is missing")

        address = _text(data.get("adr"))
        pincode = _text(data.get("pincode"))
        if not pincode and address:
            match = PINCODE_AT_END.search(address.rstrip(" ,.-"))
            pincode = match.group(1) if match else None
        legal_name = _text(data.get("lgnm"))
        trade_name = _text(data.get("tradeName")) or legal_name
        nature = data.get("nba") if isinstance(data.get("nba"), list) else []
        return GSTLookupResult(
            gstin=returned_gstin.upper(),
            trade_name=trade_name,
            legal_name=legal_name,
            principal_address=address,
            status=_text(data.get("sts")),
            taxpayer_type=_text(data.get("dty")),
            registration_date=_text(data.get("rgdt")),
            cancellation_date=_text(data.get("cxdt")),
            pincode=pincode,
            state=_text(data.get("state") or data.get("stateName")) or state_name(returned_gstin[:2]),
            state_jurisdiction=_text(data.get("stj")),
            centre_jurisdiction=_text(data.get("ctj")),
            constitution_of_business=_text(data.get("ctb")),
            nature_of_business=nature,
            einvoice_status=_text(data.get("einvoiceStatus")),
            pan=_text(data.get("pan")),
        )
