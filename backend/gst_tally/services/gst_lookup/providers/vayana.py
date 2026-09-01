import json
import re
import socket
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from ..base import (GSTLookupAuthenticationError, GSTLookupConfigurationError,
                    GSTLookupNotFoundError, GSTLookupProvider, GSTLookupProviderError,
                    GSTLookupRateLimitError, GSTLookupResult, GSTLookupTimeoutError)
from ..vayana_auth import SUPPORTED_ALGORITHMS, VayanaAuthSigner
from .generic_rest import _first


def _text(value):
    return str(value or "").strip() or None


def _address_details(address):
    address = address if isinstance(address, dict) else {}
    return {"door_number": _text(address.get("bno")), "building_name": _text(address.get("bnm")),
            "floor": _text(address.get("flno")), "street": _text(address.get("st")),
            "location": _text(address.get("loc")), "state": _text(address.get("stcd")),
            "pincode": _text(address.get("pncd")), "latitude": _text(address.get("lt")),
            "longitude": _text(address.get("lg"))}


def _display_address(details):
    parts = []
    for key in ("door_number", "building_name", "floor", "street", "location", "state"):
        value = details.get(key)
        if value and value not in parts: parts.append(value)
    text = ", ".join(parts)
    pincode = details.get("pincode")
    return f"{text} - {pincode}" if text and pincode else (text or pincode)


def _additional_places(value):
    places = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict): continue
        details = _address_details(item.get("addr"))
        places.append({"address": _display_address(details), "address_details": details,
                       "business_nature": item.get("ntr") if isinstance(item.get("ntr"), list) else []})
    return places


class VayanaProvider(GSTLookupProvider):
    SEARCH_PATH = "/gus/commonapi/v1.3/search"
    GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

    @classmethod
    def settings_config(cls):
        return {"base_url": settings.VAYANA_BASE_URL, "client_id": settings.VAYANA_CLIENT_ID,
                "cust_id": settings.VAYANA_CUST_ID,
                "private_key_path": settings.VAYANA_PRIVATE_KEY_PATH, "auth_gstin": settings.VAYANA_AUTH_GSTIN,
                "signature_algorithm": settings.VAYANA_SIGNATURE_ALGORITHM, "timeout": settings.GST_LOOKUP_TIMEOUT}

    @classmethod
    def configuration_issues(cls):
        config = cls.settings_config()
        issues = []
        if not config["base_url"]: issues.append("VAYANA_BASE_URL")
        if not config["cust_id"] and not config["client_id"]: issues.append("VAYANA_CLIENT_ID or VAYANA_CUST_ID")
        if config["cust_id"] and config["client_id"]: issues.append("Configure only one of VAYANA_CLIENT_ID or VAYANA_CUST_ID")
        if not config["private_key_path"]: issues.append("VAYANA_PRIVATE_KEY_PATH")
        elif not Path(config["private_key_path"]).is_file(): issues.append("VAYANA_PRIVATE_KEY_PATH")
        if not config["auth_gstin"]: issues.append("VAYANA_AUTH_GSTIN")
        if str(config["signature_algorithm"]).upper() not in SUPPORTED_ALGORITHMS: issues.append("VAYANA_SIGNATURE_ALGORITHM")
        return issues

    @classmethod
    def from_settings(cls):
        issues = cls.configuration_issues()
        if issues: raise GSTLookupConfigurationError("Missing Vayana configuration: " + ", ".join(issues))
        return cls(cls.settings_config())

    def __init__(self, config, opener=urlopen):
        self.config, self.opener = config, opener
        self.signer = VayanaAuthSigner(config["private_key_path"], config["signature_algorithm"])

    @staticmethod
    def _provider_code(payload):
        if not isinstance(payload, dict): return ""
        for key in ("errorCode", "error_code", "code", "status_cd"):
            value = str(payload.get(key) or "").strip().upper()
            if value.startswith("FO"): return value
        return ""

    def lookup(self, gstin):
        gstin = str(gstin or "").strip().upper()
        if not self.GSTIN_PATTERN.fullmatch(gstin):
            raise GSTLookupProviderError("Invalid GSTIN")
        url = f'{self.config["base_url"].rstrip("/")}{self.SEARCH_PATH}?{urlencode({"gstin": gstin, "action": "TP"})}'
        headers = self.signer.headers(cust_id=self.config["cust_id"], client_id=self.config["client_id"],
                                      auth_gstin=self.config["auth_gstin"])
        try:
            with self.opener(Request(url, headers=headers, method="GET"), timeout=self.config["timeout"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except (AttributeError, UnicodeError, json.JSONDecodeError):
                error_payload = {}
            code = self._provider_code(error_payload)
            if code == "FO8007": raise GSTLookupNotFoundError(gstin) from exc
            if code == "FO8001": raise GSTLookupProviderError("Vayana rejected a malformed request") from exc
            if exc.code in (401, 403): raise GSTLookupAuthenticationError() from exc
            if exc.code == 429: raise GSTLookupRateLimitError("vayana", exc.headers.get("Retry-After") if exc.headers else None) from exc
            if exc.code >= 500: raise GSTLookupProviderError("Vayana service is unavailable") from exc
            raise GSTLookupProviderError(f"Vayana returned HTTP {exc.code}") from exc
        except (TimeoutError, socket.timeout) as exc: raise GSTLookupTimeoutError() from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)): raise GSTLookupTimeoutError() from exc
            raise GSTLookupProviderError("Vayana connection failed") from exc
        except (UnicodeError, json.JSONDecodeError) as exc: raise GSTLookupProviderError("Vayana returned malformed JSON") from exc
        code = self._provider_code(payload)
        if code == "FO8007": raise GSTLookupNotFoundError(gstin)
        if code == "FO8001": raise GSTLookupProviderError("Vayana rejected a malformed request")
        root = payload.get("data", payload) if isinstance(payload, dict) else None
        if not isinstance(root, dict): raise GSTLookupProviderError("Vayana taxpayer data is missing")
        if not str(root.get("gstin") or "").strip(): raise GSTLookupProviderError("Vayana response GSTIN is missing")
        principal = root.get("pradr") if isinstance(root.get("pradr"), dict) else {}
        address_data = principal.get("addr", {}) if isinstance(principal.get("addr"), dict) else {}
        details = _address_details(address_data)
        returned_gstin = str(_first(root, "gstin") or gstin).strip().upper()
        legal_name = _text(root.get("lgnm"))
        return GSTLookupResult(gstin=returned_gstin, legal_name=legal_name,
            trade_name=_text(root.get("tradeNam")) or legal_name,
            status=_text(root.get("sts")), principal_address=_display_address(details),
            state=details["state"], registration_date=_text(root.get("rgdt")),
            taxpayer_type=_text(root.get("dty")), pincode=details["pincode"],
            cancellation_date=_text(root.get("cxdt")), constitution_of_business=_text(root.get("ctb")),
            einvoice_status=_text(root.get("einvoiceStatus")),
            nature_of_business=root.get("nba") if isinstance(root.get("nba"), list) else [],
            state_jurisdiction=_text(root.get("stj")), centre_jurisdiction=_text(root.get("ctj")),
            state_jurisdiction_code=_text(root.get("stjCd")), centre_jurisdiction_code=_text(root.get("ctjCd")),
            last_updated_date=_text(root.get("lstupddt") or root.get("lstupdt")),
            principal_address_details=details,
            principal_business_nature=principal.get("ntr") if isinstance(principal.get("ntr"), list) else [],
            additional_places=_additional_places(root.get("adadr")))
