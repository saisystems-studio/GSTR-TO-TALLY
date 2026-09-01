import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ProviderNotFound(Exception): pass
class ProviderUnauthorized(Exception): pass
class ProviderRateLimited(Exception): pass
class ProviderTimeout(Exception): pass
class ProviderError(Exception): pass


def _first(data, *keys, default=""):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""): return value
    return default

def _address_text(address):
    complete = _first(address, "completeAddress", "address")
    if complete: return str(complete).strip()
    keys = ("bno", "buildingNumber", "bnm", "buildingName", "st", "street", "loc", "location", "dst", "district", "stcd", "stateName", "pncd", "pincode")
    values = []
    for key in keys:
        value = str(address.get(key) or "").strip()
        if value and value not in values: values.append(value)
    return ", ".join(values)


class GenericRESTProvider:
    def __init__(self, config): self.config = config

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.config["api_key"]: headers[self.config["api_key_header"]] = self.config["api_key"]
        if self.config["client_id"]: headers["X-Client-Id"] = self.config["client_id"]
        if self.config["client_secret"]: headers["X-Client-Secret"] = self.config["client_secret"]
        if self.config["username"]:
            credentials = base64.b64encode(f'{self.config["username"]}:{self.config["password"]}'.encode()).decode()
            headers["Authorization"] = f"Basic {credentials}"
        return headers

    def fetch(self, gstin):
        url = self.config["base_url"].format(gstin=quote(gstin))
        try:
            with urlopen(Request(url, headers=self._headers()), timeout=self.config["timeout"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404: raise ProviderNotFound() from exc
            if exc.code in (401, 403): raise ProviderUnauthorized() from exc
            if exc.code == 429: raise ProviderRateLimited() from exc
            raise ProviderError(f"HTTP {exc.code}") from exc
        except TimeoutError as exc: raise ProviderTimeout() from exc
        except (URLError, UnicodeError, json.JSONDecodeError) as exc: raise ProviderError(str(exc)) from exc
        root = payload.get("data") or payload.get("result") or payload.get("taxpayer") or payload
        if not isinstance(root, dict) or payload.get("found") is False: raise ProviderNotFound()
        address = _first(root, "principalPlaceOfBusiness", "pradr", "address", default={})
        if isinstance(address, dict) and isinstance(address.get("addr"), dict): address = address["addr"]
        address = address if isinstance(address, dict) else {"completeAddress": str(address)}
        principal_place_of_business = _address_text(address)
        return {"gstin": str(_first(root, "gstin", "gstinNumber", "ctin", default=gstin)).upper(),
                "legal_name": _first(root, "legal_name", "legalName", "lgnm"),
                "trade_name": _first(root, "trade_name", "tradeName", "tradeNam"),
                "address": principal_place_of_business,
                "principal_place_of_business": principal_place_of_business,
                "city": _first(address, "city", "loc"), "district": _first(address, "district", "dst"),
                "state_name": _first(address, "state_name", "stateName", "stcd", default=_first(root, "state_name", "stateName")),
                "state_code": str(_first(address, "state_code", "stateCode", default=_first(root, "state_code", "stateCode", default=gstin[:2])))[:2],
                "pincode": str(_first(address, "pincode", "pncd"))[:6],
                "registration_status": _first(root, "registration_status", "registrationStatus", "sts"),
                "taxpayer_type": _first(root, "taxpayer_type", "taxpayerType", "dty")}
