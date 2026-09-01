import base64
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ..base import (GSTLookupAuthenticationError, GSTLookupNotFoundError,
                    GSTLookupProvider, GSTLookupProviderError, GSTLookupRateLimitError,
                    GSTLookupResult, GSTLookupTimeoutError)


def _first(data, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""): return value
    return ""


def _address_text(value):
    if isinstance(value, str): return value.strip()
    if not isinstance(value, dict): return ""
    address = value.get("addr", value)
    complete = _first(address, "completeAddress", "complete_address", "address")
    if complete: return str(complete).strip()
    parts = []
    for key in ("bno", "buildingNumber", "flno", "bnm", "buildingName", "st", "street", "loc", "location", "city", "dst", "district", "stcd", "stateName"):
        part = str(address.get(key) or "").strip()
        if part and part not in parts: parts.append(part)
    pincode = str(_first(address, "pncd", "pincode") or "").strip()
    text = ", ".join(parts)
    return f"{text} - {pincode}" if text and pincode else (text or pincode)


class GenericRESTProvider(GSTLookupProvider):
    """Generic JSON-over-HTTP adapter. Provider-specific adapters can replace this mapping."""

    def __init__(self, config, opener=urlopen):
        self.config = config
        self.opener = opener

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.config.get("api_key"): headers[self.config.get("api_key_header", "X-API-Key")] = self.config["api_key"]
        if self.config.get("client_id"): headers["X-Client-Id"] = self.config["client_id"]
        if self.config.get("client_secret"): headers["X-Client-Secret"] = self.config["client_secret"]
        if self.config.get("username"):
            token = base64.b64encode(f'{self.config["username"]}:{self.config.get("password", "")}'.encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        elif self.config.get("authorization"):
            headers["Authorization"] = self.config["authorization"]
        return headers

    def lookup(self, gstin):
        endpoint = self.config.get("endpoint", "")
        template = f'{self.config["base_url"].rstrip("/")}/{endpoint.lstrip("/")}' if endpoint else self.config["base_url"]
        url = template.format(gstin=quote(gstin, safe=""))
        try:
            with self.opener(Request(url, headers=self._headers()), timeout=self.config["timeout"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404: raise GSTLookupNotFoundError(gstin) from exc
            if exc.code in (401, 403): raise GSTLookupAuthenticationError() from exc
            if exc.code == 429: raise GSTLookupRateLimitError("generic_rest", exc.headers.get("Retry-After") if exc.headers else None) from exc
            raise GSTLookupProviderError(f"Provider returned HTTP {exc.code}") from exc
        except (TimeoutError, socket.timeout) as exc: raise GSTLookupTimeoutError() from exc
        except (URLError, UnicodeError, json.JSONDecodeError) as exc: raise GSTLookupProviderError(str(exc)) from exc

        if not isinstance(payload, dict) or payload.get("found") is False: raise GSTLookupNotFoundError(gstin)
        root = payload.get("data") or payload.get("result") or payload.get("taxpayer") or payload
        if isinstance(root, list): root = root[0] if root else None
        if not isinstance(root, dict): raise GSTLookupProviderError("Provider taxpayer data is missing")
        text = lambda *keys: str(_first(root, *keys) or "").strip() or None
        trade_name = text("trade_name", "tradeName", "tradeNam")
        address = _address_text(_first(root, "principal_address", "principal_place_of_business", "principalPlaceOfBusiness", "pradr", "address")) or None
        returned_gstin = str(_first(root, "gstin", "gstinNumber", "ctin") or gstin).strip().upper()
        return GSTLookupResult(
            gstin=returned_gstin,
            legal_name=text("legal_name", "legalName", "lgnm"),
            trade_name=trade_name,
            status=text("status", "registrationStatus", "sts"),
            principal_address=address,
            state=text("state", "stateName", "stcd"),
            registration_date=text("registration_date", "registrationDate", "rgdt"),
            taxpayer_type=text("taxpayer_type", "taxpayerType", "dty"),
            pincode=None,
        )
