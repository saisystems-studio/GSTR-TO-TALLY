import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..base import GSTINProvider
from ..models import (GSTINAuthenticationError, GSTINDetails, GSTINNotFoundError,
                      GSTINRateLimitError, GSTINResponseError, GSTINTimeoutError,
                      GSTINUnavailableError)


def _address_text(pradr):
    address = pradr.get("addr", pradr) if isinstance(pradr, dict) else {}
    values = []
    for key in ("bno", "flno", "bnm", "st", "loc", "city", "dst", "stcd"):
        value = str(address.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    pincode = str(address.get("pncd") or "").strip()
    text = ", ".join(values)
    return f"{text} - {pincode}" if text and pincode else (pincode or text)


class ClearTaxGSTINProvider(GSTINProvider):
    """Adapter for ClearTax's documented, deprecated GST 1.0 taxpayer endpoint."""

    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, base_url, taxable_entity_id, auth_token, timeout=20, retries=2, opener=urlopen):
        self.base_url = base_url.rstrip("/")
        self.taxable_entity_id = taxable_entity_id
        self.auth_token = auth_token
        self.timeout = timeout
        self.retries = retries
        self.opener = opener

    def _url(self, gstin):
        path = f"/gst/api/v0.2/taxable_entities/{quote(self.taxable_entity_id, safe='')}/gstin_verification"
        return f"{self.base_url}{path}?{urlencode({'gstin': gstin})}"

    def _request(self, gstin):
        request = Request(self._url(gstin), headers={
            "Accept": "application/json",
            "X-Cleartax-Auth-Token": self.auth_token,
        })
        for attempt in range(self.retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code in (401, 403): raise GSTINAuthenticationError("ClearTax authentication failed") from exc
                if exc.code == 404: raise GSTINNotFoundError(gstin) from exc
                if exc.code == 429 and attempt == self.retries: raise GSTINRateLimitError("ClearTax rate limit exceeded") from exc
                if exc.code not in self.RETRYABLE: raise GSTINUnavailableError(f"ClearTax returned HTTP {exc.code}") from exc
                if attempt == self.retries: raise GSTINUnavailableError(f"ClearTax returned HTTP {exc.code}") from exc
            except (TimeoutError, socket.timeout) as exc:
                if attempt == self.retries: raise GSTINTimeoutError("ClearTax request timed out") from exc
            except URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    if attempt == self.retries: raise GSTINTimeoutError("ClearTax request timed out") from exc
                elif attempt == self.retries:
                    raise GSTINUnavailableError("ClearTax is unavailable") from exc
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise GSTINResponseError("ClearTax returned malformed JSON") from exc
            time.sleep(0.25 * (2 ** attempt))

    def get_gstin_details(self, gstin):
        payload = self._request(gstin)
        if not isinstance(payload, dict):
            raise GSTINResponseError("ClearTax returned an unexpected response")
        root = payload.get("data", payload)
        if isinstance(root, list):
            root = root[0] if root else None
        if not isinstance(root, dict):
            raise GSTINResponseError("ClearTax taxpayer data is missing")
        trade_name = str(root.get("tradeNam") or "").strip()
        address = _address_text(root.get("pradr"))
        if not trade_name or not address:
            raise GSTINResponseError("ClearTax response is missing tradeNam or pradr")
        returned_gstin = str(root.get("gstin") or gstin).strip().upper()
        return GSTINDetails(returned_gstin, trade_name, address)
