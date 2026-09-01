import json
import socket
import hashlib
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..base import (GSTLookupAuthenticationError, GSTLookupConfigurationError,
                    GSTLookupNotFoundError, GSTLookupProvider, GSTLookupProviderError,
                    GSTLookupResult, GSTLookupTimeoutError)

ACCESS_CACHE_KEY = "gst:sandbox:access-token"
SESSION_CACHE_KEY = "gst:sandbox:taxpayer-session"

def _session_key(company_gstin):
    digest = hashlib.sha256(str(company_gstin or "").strip().upper().encode()).hexdigest()
    return f"{SESSION_CACHE_KEY}:{digest}"

class SandboxOTPRequired(GSTLookupAuthenticationError):
    def __init__(self, message="Sandbox taxpayer session is required", diagnostics=None):
        super().__init__(message)
        self.code = "SANDBOX_SESSION_REQUIRED"
        self.safe_message = message
        self.diagnostics = diagnostics or {}

class SandboxAuthenticationFailure(GSTLookupAuthenticationError):
    def __init__(self, code, message, diagnostics=None):
        super().__init__(message); self.code = code; self.safe_message = message; self.diagnostics = diagnostics or {}

class SandboxNetworkError(GSTLookupProviderError): pass

class SandboxOTPRequestFailure(GSTLookupProviderError):
    def __init__(self, code, message, diagnostics=None):
        super().__init__(message); self.code = code; self.safe_message = message; self.diagnostics = diagnostics or {}

def _nested_value(payload, exact=(), token_predicate=None):
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in exact and isinstance(value, str) and value.strip(): return value.strip()
        for key, value in payload.items():
            if token_predicate and token_predicate(str(key).lower(), value): return value.strip()
        for value in payload.values():
            found = _nested_value(value, exact, token_predicate)
            if found: return found
    elif isinstance(payload, list):
        for value in payload:
            found = _nested_value(value, exact, token_predicate)
            if found: return found
    return None

def _ttl(payload, default):
    value = _nested_value(payload, {"expires_in", "expiresin", "expiry", "expires"})
    try: return max(1, int(float(value)))
    except (TypeError, ValueError): return default

def _first_text(data, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

def _extract_public_search_data(payload):
    """Normalize the real Sandbox public GSTIN search envelope, which may nest
    the taxpayer fields under data.data with a status_cd (like the taxpayer
    detail schema), or return them flat under data."""
    envelope = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    nested = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    if nested is not None:
        return str(envelope.get("status_cd") or ""), nested
    if envelope.get("gstin"):
        return "1", envelope
    return "", {}

class SandboxGSTProvider(GSTLookupProvider):
    AUTH_PATH = "/authenticate"
    PUBLIC_GSTIN_SEARCH_PATH = "/gst/compliance/public/gstin/search"
    OTP_PATH = "/gst/compliance/tax-payer/otp"
    OTP_VERIFY_PATH = "/gst/compliance/tax-payer/otp/verify"

    def __init__(self, config=None, opener=urlopen):
        self.config, self.opener = config or self.settings_config(), opener
        self.last_http_status, self.last_response_keys = None, []
        self.last_response_body, self.last_request_sent = None, False
        self.last_lookup_attempted = False
        self.lookup_request_count = 0

    @staticmethod
    def settings_config():
        return {"base_url": settings.SANDBOX_BASE_URL, "api_key": settings.SANDBOX_API_KEY,
                "api_secret": settings.SANDBOX_API_SECRET, "api_version": settings.SANDBOX_API_VERSION,
                "timeout": settings.GST_LOOKUP_TIMEOUT,
                "access_ttl": settings.SANDBOX_ACCESS_TOKEN_TTL, "session_ttl": settings.SANDBOX_TAXPAYER_SESSION_TTL}

    @classmethod
    def from_settings(cls): return cls(cls.settings_config())

    @classmethod
    def configuration_issues(cls):
        config = cls.settings_config()
        names = {"base_url": "SANDBOX_BASE_URL", "api_key": "SANDBOX_API_KEY", "api_secret": "SANDBOX_API_SECRET"}
        return [name for key, name in names.items() if not str(config.get(key) or "").strip()]

    def _url(self, path): return f'{self.config["base_url"].rstrip("/")}/{path.lstrip("/")}'

    def _post(self, path, headers, body=None, query=""):
        request = Request(self._url(path) + query, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method="POST")
        self.last_request_sent = True
        try:
            with self.opener(request, timeout=self.config["timeout"]) as response:
                self.last_http_status = getattr(response, "status", 200)
                raw_body = response.read().decode("utf-8")
                self.last_response_body = raw_body
                payload = json.loads(raw_body)
        except HTTPError as exc:
            self.last_http_status = exc.code
            try: self.last_response_body = exc.read().decode("utf-8", "replace")
            except Exception: self.last_response_body = ""
            if exc.code in (401, 403):
                error = GSTLookupAuthenticationError(f"Sandbox authentication failed (HTTP {exc.code})"); error.http_status = exc.code; raise error from exc
            raise GSTLookupProviderError(f"Sandbox request failed (HTTP {exc.code})") from exc
        except (TimeoutError, socket.timeout) as exc:
            self.last_http_status = 0; raise GSTLookupTimeoutError() from exc
        except URLError as exc:
            self.last_http_status = 0; raise SandboxNetworkError("Sandbox network request failed") from exc
        except (UnicodeError, json.JSONDecodeError) as exc: raise GSTLookupProviderError("Sandbox returned an invalid response") from exc
        if not isinstance(payload, dict): raise GSTLookupProviderError("Sandbox returned an invalid response")
        self.last_response_keys = sorted(str(key) for key in payload.keys())
        return payload

    def authenticate(self, force=False):
        if not force:
            cached = cache.get(ACCESS_CACHE_KEY)
            if cached: return cached
        diagnostics = {"sandbox_configured": not self.configuration_issues(), "api_key_configured": bool(self.config.get("api_key")),
            "api_secret_configured": bool(self.config.get("api_secret")), "authenticate_attempted": True,
            "authenticate_http_status": None, "access_token_received": False, "response_keys": []}
        try:
            payload = self._post(self.AUTH_PATH, {"accept": "application/json", "x-api-key": self.config["api_key"],
                "x-api-secret": self.config["api_secret"], "x-api-version": self.config["api_version"]})
        except GSTLookupAuthenticationError as exc:
            code = f"SANDBOX_AUTH_{getattr(exc, 'http_status', 401)}"; diagnostics["authenticate_http_status"] = getattr(exc, "http_status", None)
            raise SandboxAuthenticationFailure(code, "Invalid Sandbox API credentials.", diagnostics) from exc
        except GSTLookupTimeoutError as exc:
            raise SandboxAuthenticationFailure("SANDBOX_AUTH_TIMEOUT", "Sandbox authentication timed out.", diagnostics) from exc
        except SandboxNetworkError as exc:
            raise SandboxAuthenticationFailure("SANDBOX_AUTH_NETWORK_ERROR", "Sandbox authentication network request failed.", diagnostics) from exc
        except GSTLookupProviderError as exc:
            diagnostics.update(authenticate_http_status=self.last_http_status, response_keys=self.last_response_keys)
            raise SandboxAuthenticationFailure("SANDBOX_AUTH_INVALID_RESPONSE", "Sandbox authentication returned an invalid response.", diagnostics) from exc
        diagnostics.update(authenticate_http_status=self.last_http_status, response_keys=self.last_response_keys)
        token = _nested_value(payload, {"access_token", "accesstoken"})
        if not token: raise SandboxAuthenticationFailure("SANDBOX_ACCESS_TOKEN_MISSING", "Sandbox access token was missing.", diagnostics)
        diagnostics["access_token_received"] = True; self.last_auth_diagnostics = diagnostics
        cache.set(ACCESS_CACHE_KEY, token, _ttl(payload, self.config["access_ttl"]))
        return token

    def _taxpayer_body(self, username, company_gstin): return {"username": str(username).strip(), "gstin": str(company_gstin).strip().upper()}
    def _session_headers(self, token): return {"authorization": token, "content-type": "application/json", "x-api-version": self.config["api_version"]}
    def _public_headers(self, token):
        return {"authorization": token, "content-type": "application/json", "x-api-key": self.config["api_key"],
                "x-api-version": self.config["api_version"]}

    def request_otp(self, username, company_gstin):
        if not str(username or "").strip() or not str(company_gstin or "").strip():
            raise GSTLookupProviderError("Taxpayer username and company GSTIN are required")
        token = self.authenticate()
        body = self._taxpayer_body(username, company_gstin)
        diagnostics = {"stage": "otp_request", "sandbox_configured": True, "sandbox_authenticated": True,
            "username_present": True, "company_gstin": str(company_gstin).strip().upper(),
            "otp_request_attempted": True, "otp_sent": False, "otp_http_status": None}
        try: self._post(self.OTP_PATH, self._session_headers(token), body)
        except GSTLookupAuthenticationError:
            token = self.authenticate(force=True)
            try: self._post(self.OTP_PATH, self._session_headers(token), body)
            except GSTLookupAuthenticationError as exc:
                diagnostics["otp_http_status"] = getattr(exc, "http_status", None)
                raise SandboxOTPRequestFailure("OTP_REQUEST_REJECTED", "Sandbox rejected the GST portal username or taxpayer session. Check the GST portal username and try again.", diagnostics) from exc
        except GSTLookupTimeoutError as exc:
            raise SandboxOTPRequestFailure("OTP_REQUEST_TIMEOUT", "Sandbox OTP request timed out.", diagnostics) from exc
        except GSTLookupProviderError as exc:
            diagnostics["otp_http_status"] = self.last_http_status
            raise SandboxOTPRequestFailure("OTP_REQUEST_FAILED", "Sandbox OTP request failed.", diagnostics) from exc
        cache.delete(_session_key(company_gstin))
        diagnostics.update(otp_sent=True, otp_http_status=self.last_http_status)
        return {"otp_required": True, "code": "OTP_SENT", "message": "OTP sent successfully.", **diagnostics}

    def verify_otp(self, otp, username, company_gstin):
        otp = str(otp or "").strip()
        if not otp or not str(username or "").strip() or not str(company_gstin or "").strip():
            raise GSTLookupProviderError("OTP, taxpayer username and company GSTIN are required")
        query = f"?otp={quote(otp, safe='')}"
        body = self._taxpayer_body(username, company_gstin)
        try: payload = self._post(self.OTP_VERIFY_PATH, self._session_headers(self.authenticate()), body, query)
        except GSTLookupAuthenticationError:
            payload = self._post(self.OTP_VERIFY_PATH, self._session_headers(self.authenticate(force=True)), body, query)
        taxpayer_token = _nested_value(payload, {"taxpayer_token", "taxpayertoken", "session_token", "sessiontoken"},
            lambda key, value: isinstance(value, str) and value.strip() and "token" in key and "access" not in key)
        if not taxpayer_token: raise GSTLookupAuthenticationError("Sandbox taxpayer session token was missing")
        ttl = _ttl(payload, self.config["session_ttl"]); expires_at = timezone.now() + timedelta(seconds=ttl)
        cache.set(_session_key(company_gstin), {"token": taxpayer_token, "expires_at": expires_at.isoformat(),
            "gstin": str(company_gstin).strip().upper(), "username": str(username).strip()}, ttl)
        return {"verified": True, "session_active": True, "code": "TAXPAYER_SESSION_ACTIVE"}

    def taxpayer_session_state(self, company_gstin):
        session = cache.get(_session_key(company_gstin))
        expires_at = parse_datetime(str(session.get("expires_at") or "")) if isinstance(session, dict) else None
        expired = bool(expires_at and expires_at <= timezone.now())
        if expired:
            cache.delete(_session_key(company_gstin))
            session = None
        return session, expired

    def get_taxpayer_session(self, company_gstin): return self.taxpayer_session_state(company_gstin)[0]

    def safe_status(self, company_gstin=""):
        """Status for the public GSTIN search lookup used by normal Party Details.

        Readiness (`lookup_ready`) depends only on application-level Sandbox
        configuration, never on a taxpayer OTP/session — the public search
        endpoint only needs SANDBOX_API_KEY/SANDBOX_API_SECRET. The
        `taxpayer_session_active`/`session_active`/`requires_username` fields
        remain informational for the separate taxpayer OTP flow.
        """
        issues = self.configuration_issues()
        configured = not issues
        session, session_expired = self.taxpayer_session_state(company_gstin) if company_gstin else (None, False)
        session_active = bool(session)
        return {"provider": "sandbox", "configured": configured, "provider_configured": configured,
                "configuration_code": "" if configured else "SANDBOX_NOT_CONFIGURED",
                "base_url_configured": bool(self.config.get("base_url")), "api_key_configured": bool(self.config.get("api_key")),
                "api_secret_configured": bool(self.config.get("api_secret")), "missing": issues,
                "authenticated": bool(cache.get(ACCESS_CACHE_KEY)),
                "taxpayer_session_active": session_active, "session_active": session_active,
                "session_required": False, "session_expired": session_expired,
                "otp_required": False, "lookup_ready": configured,
                "lookup_failed": False, "requires_username": not session_active,
                "company_gstin": str(company_gstin or "").strip().upper(),
                "gst_details_endpoint_configured": True,
                "party_lookup_available": not issues,
                "party_lookup_requires_taxpayer_session": False}

    def lookup(self, gstin, company_gstin=""):
        """Normal Party Details lookup: public GSTIN search using application auth only.

        No taxpayer OTP/session is requested or required here. `company_gstin`
        is accepted for signature compatibility but unused by this endpoint.
        """
        gstin = str(gstin or "").strip().upper()
        self.last_lookup_attempted = False
        self.lookup_request_count = 0
        self.last_http_status = None
        self.last_response_body = None
        token = self.authenticate()
        try:
            self.last_lookup_attempted = True
            self.lookup_request_count += 1
            payload = self._post(self.PUBLIC_GSTIN_SEARCH_PATH, self._public_headers(token), {"gstin": gstin})
        except GSTLookupAuthenticationError:
            token = self.authenticate(force=True)
            self.last_lookup_attempted = True
            self.lookup_request_count += 1
            payload = self._post(self.PUBLIC_GSTIN_SEARCH_PATH, self._public_headers(token), {"gstin": gstin})
        status_cd, data = _extract_public_search_data(payload)
        if not data:
            raise GSTLookupProviderError("Sandbox public GSTIN payload is empty")
        if not status_cd:
            raise GSTLookupProviderError("Sandbox public GSTIN payload schema is invalid")
        if status_cd != "1":
            raise GSTLookupNotFoundError(gstin)
        returned_gstin = str(data.get("gstin") or "").strip().upper()
        if returned_gstin != gstin:
            raise GSTLookupProviderError("Sandbox GSTIN response did not match the requested GSTIN")
        address = data.get("pradr", {}).get("addr", {}) if isinstance(data.get("pradr"), dict) else {}
        address_parts = [address.get(key) for key in ("flno", "bno", "bnm", "st", "landMark", "loc", "locality", "dst")]
        principal_address = ", ".join(str(value).strip() for value in address_parts if str(value or "").strip()) or None
        return GSTLookupResult(
            gstin=returned_gstin,
            legal_name=_first_text(data, "lgnm", "legal_name", "legalName"),
            trade_name=_first_text(data, "tradeNam", "trade_name", "tradeName"),
            status=data.get("sts"),
            principal_address=principal_address, state=address.get("stcd"), state_code=gstin[:2],
            registration_date=data.get("rgdt"),
            taxpayer_type=data.get("dty"), pincode=str(address.get("pncd") or "") or None,
            cancellation_date=data.get("cxdt"), constitution_of_business=data.get("ctb"),
            einvoice_status=data.get("einvoiceStatus"), nature_of_business=data.get("nba") or [],
            state_jurisdiction=data.get("stj"), centre_jurisdiction=data.get("ctj"),
            state_jurisdiction_code=data.get("stjCd"), centre_jurisdiction_code=data.get("ctjCd"),
            last_updated_date=data.get("lstupdt"), principal_address_details=address,
            principal_business_nature=data.get("pradr", {}).get("ntr") or [], additional_places=data.get("adadr") or [])
