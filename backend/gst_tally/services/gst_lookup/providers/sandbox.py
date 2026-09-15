import json
import socket
import hashlib
import logging
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
                    GSTLookupRateLimitError, GSTLookupResult, GSTLookupTimeoutError)
from gst_tally.tally.validators import state_name

ACCESS_CACHE_KEY = "gst:sandbox:access-token"
SESSION_CACHE_KEY = "gst:sandbox:taxpayer-session"
# An account-level /authenticate rejection (403 -- quota exhausted, subscription
# lapsed, permission revoked) applies to every GSTIN, not just the one being
# looked up when it was first discovered. Caching it here is what turns "13
# unique GSTINs -> 13 failing /authenticate calls" into one real call plus 12
# free, instant, correctly-diagnosed cache hits.
PROVIDER_BLOCK_CACHE_KEY = "gst:sandbox:provider-block"
logger = logging.getLogger(__name__)

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
    def find(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).lower() in {"expires_in", "expiresin"} and isinstance(value, (str, int, float)):
                    return value
            for value in data.values():
                found = find(value)
                if found is not None:
                    return found
        return None
    value = find(payload)
    try: return max(1, int(float(value)))
    except (TypeError, ValueError): return default

def _first_text(data, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

def _first_value(data, *keys):
    if not isinstance(data, dict): return None
    lowered = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        value = data.get(key)
        if value not in (None, ""): return value
        value = lowered.get(str(key).lower())
        if value not in (None, ""): return value
    return None

def _text(value):
    text = str(value or "").strip()
    return text if text and text.lower() not in {"none", "null", "-"} else None

def _first_any_text(data, *keys):
    return _text(_first_value(data, *keys))

def _extract_public_search_data(payload):
    """Normalize the real Sandbox public GSTIN search envelope, which may nest
    the taxpayer fields under data.data with a status_cd (like the taxpayer
    detail schema), or return them flat under data. Also reports which shape
    was actually found, for diagnostics -- so a future unrecognized shape is
    visible instead of silently collapsing to the same "invalid" reason."""
    envelope = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    nested = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    if nested is not None:
        return str(_first_value(envelope, "status_cd", "status", "Status") or ""), nested, "nested"
    erp_nested = envelope.get("Data") if isinstance(envelope.get("Data"), dict) else None
    if erp_nested is not None:
        return str(_first_value(envelope, "Status", "status", "status_cd") or ""), erp_nested, "erp_nested"
    if _first_value(envelope, "gstin", "Gstin"):
        return "1", envelope, "flat"
    return "", {}, "empty" if not envelope else "unrecognized"


def _safe_json_object(text):
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None

def _regular_taxpayer_type(value):
    text = _text(value)
    if not text: return None
    return {"reg": "Regular"}.get(text.casefold(), text)

def _extract_address(data):
    principal = _first_value(data, "pradr", "principal_place_of_business", "principalPlaceOfBusiness", "principal_address")
    if isinstance(principal, dict):
        address = principal.get("addr") if isinstance(principal.get("addr"), dict) else principal
    elif principal:
        address = {"address": principal}
    else:
        address = {}
    flat = {
        "bno": _first_value(data, "AddrBno", "address1", "address_line_1"),
        "bnm": _first_value(data, "AddrBnm", "address2", "address_line_2"),
        "flno": _first_value(data, "AddrFlno"),
        "st": _first_value(data, "AddrSt", "street"),
        "loc": _first_value(data, "AddrLoc", "location", "city"),
        "dst": _first_value(data, "AddrDst", "district"),
        "stcd": _first_value(data, "State", "state", "stateName"),
        "pncd": _first_value(data, "AddrPncd", "pinCode", "pincode", "pin"),
    }
    if not isinstance(address, dict): address = {}
    merged = {**{key: value for key, value in flat.items() if value not in (None, "")}, **address}
    address_text = _first_any_text(merged, "completeAddress", "complete_address", "address")
    if not address_text:
        address_parts = [_first_value(merged, key) for key in ("bno", "bnm", "flno", "st", "landMark", "loc", "locality", "dst")]
        seen = set()
        cleaned = []
        for value in address_parts:
            text = _text(value)
            key = text.casefold() if text else ""
            if text and key not in seen:
                seen.add(key)
                cleaned.append(text)
        address_text = ", ".join(cleaned) or None
    pincode = _text(_first_value(merged, "pncd", "pinCode", "pincode", "pin"))
    if pincode:
        pincode = "".join(ch for ch in pincode if ch.isdigit())[:6] or pincode
    return merged, address_text, _first_any_text(merged, "stcd", "state", "stateName"), pincode

class SandboxGSTProvider(GSTLookupProvider):
    AUTH_PATH = "/authenticate"
    PUBLIC_GSTIN_SEARCH_PATH = "/gst/compliance/public/gstin/search"
    OTP_PATH = "/gst/compliance/tax-payer/otp"
    OTP_VERIFY_PATH = "/gst/compliance/tax-payer/otp/verify"

    def __init__(self, config=None, opener=urlopen):
        self.config, self.opener = config or self.settings_config(), opener
        self.runtime_configuration = config is None
        self.last_http_status, self.last_response_keys = None, []
        self.last_response_body, self.last_request_sent = None, False
        self.last_lookup_attempted = False
        self.lookup_request_count = 0
        self.last_provider_code, self.last_provider_message = None, None
        self.last_provider_transaction_id, self.last_response_shape = None, None
        self.last_request_metadata = {}

    @staticmethod
    def config_for_credentials(configured=None):
        # Environment values are a bootstrap fallback only when no DB row exists.
        credentials = configured if configured is not None else {
            "api_key": settings.SANDBOX_API_KEY, "api_secret": settings.SANDBOX_API_SECRET,
            "api_version": settings.SANDBOX_API_VERSION,
        }
        base_url = ({"test": "https://test-api.sandbox.co.in", "production": "https://api.sandbox.co.in"}
                    .get(credentials.get("environment"), settings.SANDBOX_BASE_URL))
        return {"base_url": base_url, **credentials, "timeout": settings.GST_LOOKUP_TIMEOUT,
                "access_ttl": settings.SANDBOX_ACCESS_TOKEN_TTL, "session_ttl": settings.SANDBOX_TAXPAYER_SESSION_TTL,
                "auth_failure_cooldown": settings.SANDBOX_AUTH_FAILURE_COOLDOWN}

    @classmethod
    def settings_config(cls):
        from superadmin.services.sandbox_configuration import provider_config
        return cls.config_for_credentials(provider_config())

    @classmethod
    def from_settings(cls):
        provider = cls(cls.settings_config())
        provider.runtime_configuration = True
        return provider

    def _reload_configuration(self):
        if getattr(self, "runtime_configuration", False):
            self.config = self.settings_config()

    def cache_key(self, kind):
        from superadmin.services.sandbox_configuration import cache_scope
        return f"gst:sandbox:{kind}:{cache_scope(self.config)}"

    def session_key(self, company_gstin):
        return self.cache_key("taxpayer-session") + ":" + hashlib.sha256(str(company_gstin).strip().upper().encode()).hexdigest()

    def clear_session_cache(self):
        keys = cache.get(self.cache_key("session-index"), [])
        cache.delete_many([self.cache_key("access-token"), self.cache_key("provider-block"), self.cache_key("session-index"), *keys])

    def _record_runtime_status(self, **values):
        if not self.config.get("configuration_id"):
            return
        from superadmin.models import SandboxAPIConfiguration
        # An in-flight response from an old revision cannot overwrite new status.
        SandboxAPIConfiguration.objects.filter(pk=self.config["configuration_id"], is_active=True,
            credential_revision=self.config["credential_revision"]).update(**values)

    def _redact(self, value):
        text = str(value or "")
        try:
            payload = json.loads(text)
            def scrub(item):
                if isinstance(item, dict):
                    return {key: "[REDACTED]" if any(part in str(key).lower() for part in ("token", "secret", "api_key", "api-key", "authorization")) else scrub(value) for key, value in item.items()}
                if isinstance(item, list):
                    return [scrub(value) for value in item]
                return item
            text = json.dumps(scrub(payload))
        except (ValueError, TypeError):
            pass
        for secret in (self.config.get("api_key"), self.config.get("api_secret"), getattr(self, "_last_token", None)):
            if secret:
                text = text.replace(str(secret), "[REDACTED]")
        return text

    @classmethod
    def configuration_issues(cls):
        config = cls.settings_config()
        names = {"base_url": "SANDBOX_BASE_URL", "api_key": "SANDBOX_API_KEY", "api_secret": "SANDBOX_API_SECRET"}
        return [name for key, name in names.items() if not str(config.get(key) or "").strip()]

    def _url(self, path): return f'{self.config["base_url"].rstrip("/")}/{path.lstrip("/")}'

    def _post(self, path, headers, body=None, query=""):
        url = self._url(path) + query
        request = Request(url, data=json.dumps(body).encode() if body is not None else None,   headers=headers, method="POST")
        self.last_request_sent = True
        self.last_provider_code = self.last_provider_message = self.last_provider_transaction_id = None
        self.last_request_metadata = {
            "endpoint": path,
            "url": self._url(path),
            "method": "POST",
            "body_keys": sorted(str(key) for key in body.keys()) if isinstance(body, dict) else [],
            "auth_token_attached": bool(headers.get("authorization")),
            "api_key_attached": bool(headers.get("x-api-key")),
            "taxpayer_session_attached": bool(headers.get("authorization")) and not headers.get("x-api-key"),
        }
        try:
            with self.opener(request, timeout=self.config["timeout"]) as response:
                self.last_http_status = getattr(response, "status", 200)
                response_headers = getattr(response, "headers", None)
                self.last_request_metadata["request_id"] = (
                    (response_headers.get("x-request-id") or response_headers.get("request-id") or "")
                    if response_headers else ""
                )
                raw_body = response.read().decode("utf-8")
                self.last_response_body = "" if path == self.AUTH_PATH else self._redact(raw_body)
                payload = json.loads(raw_body)
        except HTTPError as exc:
            self.last_http_status = exc.code
            try: self.last_response_body = self._redact(exc.read().decode("utf-8", "replace"))
            except Exception: self.last_response_body = ""
            # Sandbox's own error envelope (distinct from an unhandled crash)
            # carries {code, message, transaction_id} -- surface it instead of
            # collapsing every HTTP failure into an undiagnosable bare status.
            error_body = _safe_json_object(self.last_response_body)
            if error_body:
                self.last_provider_code = error_body.get("code")
                self.last_provider_message = _first_text(error_body, "message")
                self.last_provider_transaction_id = _first_text(error_body, "transaction_id")
            detail = f": {self.last_provider_message}" if self.last_provider_message else ""
            request_id = ""
            try:
                request_id = (exc.headers or {}).get("x-request-id") or (exc.headers or {}).get("request-id") or ""
            except AttributeError:
                request_id = ""
            self.last_request_metadata["request_id"] = request_id
            logger.warning("Sandbox GST request endpoint=%s status=%s request_id=%s", path, exc.code, request_id)
            if exc.code in (401, 403):
                error = GSTLookupAuthenticationError(f"Sandbox authentication failed (HTTP {exc.code}){detail}"); error.http_status = exc.code; raise error from exc
            if exc.code == 429:
                raise GSTLookupRateLimitError("sandbox", getattr(exc, "headers", {}).get("Retry-After")) from exc
            raise GSTLookupProviderError(f"Sandbox request failed (HTTP {exc.code}){detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            self.last_http_status = 0; raise GSTLookupTimeoutError() from exc
        except URLError as exc:
            self.last_http_status = 0; raise SandboxNetworkError("Sandbox network request failed") from exc
        except (UnicodeError, json.JSONDecodeError) as exc: raise GSTLookupProviderError("Sandbox returned an invalid response") from exc
        if not isinstance(payload, dict): raise GSTLookupProviderError("Sandbox returned an invalid response")
        self.last_response_keys = sorted(str(key) for key in payload.keys())
        return payload

    def authenticate(self, force=False, bypass_block=False):
        self._reload_configuration()
        if self.config.get("credentials_status") == "invalid" and not (force or bypass_block):
            raise SandboxAuthenticationFailure("SANDBOX_AUTH_401", "GST party lookup credentials require an update. Contact the administrator.")
        try:
            return self._authenticate(force=force, bypass_block=bypass_block)
        except SandboxAuthenticationFailure as exc:
            invalid = exc.code in {"SANDBOX_AUTH_401", "SANDBOX_AUTH_403"}
            # A candidate config being tested by Super Admin's Test Connection
            # (no configuration_id -- see _record_runtime_status) has no
            # persisted row to update and, more importantly, is a diagnostic
            # call: the admin needs Sandbox's real reason ("Invalid API key"),
            # not the generic end-user message a live GST-import lookup
            # failure shows. Only a real, persisted configuration gets that
            # substitution and the DB write.
            if self.config.get("configuration_id"):
                values = {"last_error_code": exc.code, "last_error": self._redact(exc.safe_message)[:255],
                          "connection_status": "authentication_failed" if invalid else "unavailable"}
                if invalid:
                    self.clear_session_cache()
                    values["credentials_status"] = "invalid"
                    exc.safe_message = "GST party lookup credentials require an update. Contact the administrator."
                    exc.args = (exc.safe_message,)
                    values["last_error"] = exc.safe_message
                self._record_runtime_status(**values)
            raise

    def _authenticate(self, force=False, bypass_block=False):
        if not force:
            cached = cache.get(self.cache_key("access-token"))
            if cached:
                from superadmin.services.sandbox_configuration import decrypt
                token = decrypt(cached)
                if token:
                    self._last_token = token
                    return token
        diagnostics = {"sandbox_configured": all(self.config.get(key) for key in ("base_url", "api_key", "api_secret")), "api_key_configured": bool(self.config.get("api_key")),
            "api_secret_configured": bool(self.config.get("api_secret")), "authenticate_attempted": True,
            "authenticate_http_status": None, "access_token_received": False, "response_keys": []}
        if not force and not bypass_block:
            # A previously-discovered account-level block (see the 403 branch
            # below) applies to every GSTIN and every batch, not just the one
            # that first hit it -- reuse it instead of making Sandbox reject
            # the exact same request again. A user-triggered retry passes
            # bypass_block=True (see lookup() below) to genuinely re-check --
            # but, unlike `force`, it must NOT also discard an otherwise-valid
            # cached access token (already handled above); those are
            # independent concerns and conflating them wasted a real
            # authenticate() call, and a real Opener response, on every retry
            # of a GSTIN that failed for a reason having nothing to do with
            # the access token at all.
            blocked = cache.get(self.cache_key("provider-block"))
            if blocked:
                self.last_http_status = blocked.get("http_status")
                self.last_provider_message = blocked.get("message")
                self.last_lookup_attempted = False
                diagnostics.update(authenticate_http_status=blocked.get("http_status"), provider_blocked=True)
                raise SandboxAuthenticationFailure(blocked.get("code", "SANDBOX_FORBIDDEN"),
                    blocked.get("message") or "Sandbox rejected the request.", diagnostics)
        try:
            payload = self._post(self.AUTH_PATH, {"accept": "application/json", "x-api-key": self.config["api_key"],
                "x-api-secret": self.config["api_secret"], "x-api-version": self.config["api_version"]})
        except GSTLookupAuthenticationError as exc:
            status_code = getattr(exc, "http_status", 401)
            diagnostics["authenticate_http_status"] = status_code
            # A 403 here is Sandbox rejecting the *account* (quota/subscription/
            # permission), not the credentials themselves -- conflating it with
            # a 401 as "Invalid Sandbox API credentials" hides the real reason
            # (e.g. Sandbox's own "Usage quota exhausted") behind a message that
            # actively points the caller at the wrong fix.
            if status_code == 403:
                message = self.last_provider_message or "Sandbox rejected the request (account/subscription issue)."
                code = "SANDBOX_QUOTA_EXHAUSTED" if "quota" in message.casefold() else "SANDBOX_FORBIDDEN"
                cooldown = self.config.get("auth_failure_cooldown", 60)
                if cooldown:
                    cache.set(self.cache_key("provider-block"), {"code": code, "message": message, "http_status": 403}, cooldown)
                raise SandboxAuthenticationFailure(code, message, diagnostics) from exc
            code = f"SANDBOX_AUTH_{status_code}"
            # Surface Sandbox's own error message (e.g. "Invalid API key")
            # when it gave one -- it is already sanitized (comes from the
            # provider's own JSON error body, never from our request), and a
            # hardcoded "Invalid Sandbox API credentials." hides the real,
            # more specific reason from the admin.
            message = self.last_provider_message or "Invalid Sandbox API credentials."
            raise SandboxAuthenticationFailure(code, message, diagnostics) from exc
        except GSTLookupRateLimitError as exc:
            diagnostics["authenticate_http_status"] = 429
            raise SandboxAuthenticationFailure("SANDBOX_AUTH_429", "Sandbox rate limit reached during authentication.", diagnostics) from exc
        except GSTLookupTimeoutError as exc:
            raise SandboxAuthenticationFailure("SANDBOX_AUTH_TIMEOUT", "Sandbox authentication timed out.", diagnostics) from exc
        except SandboxNetworkError as exc:
            raise SandboxAuthenticationFailure("SANDBOX_AUTH_NETWORK_ERROR", "Sandbox authentication network request failed.", diagnostics) from exc
        except GSTLookupProviderError as exc:
            diagnostics.update(authenticate_http_status=self.last_http_status, response_keys=self.last_response_keys)
            # A generic GSTLookupProviderError covers every non-401/403/429
            # HTTPError plus a malformed/unparsable body -- distinguish "wrong
            # endpoint" (404) and "Sandbox is down" (5xx) from a genuinely
            # unrecognized response shape instead of collapsing all three.
            status = self.last_http_status or 0
            if status == 404:
                code, message = "SANDBOX_AUTH_404", "Sandbox authentication endpoint was not found (check the configured base URL/environment)."
            elif 500 <= status < 600:
                code, message = "SANDBOX_AUTH_5XX", f"Sandbox authentication service error (HTTP {status})."
            else:
                code, message = "SANDBOX_AUTH_INVALID_RESPONSE", "Sandbox authentication returned an invalid response."
            raise SandboxAuthenticationFailure(code, message, diagnostics) from exc
        diagnostics.update(authenticate_http_status=self.last_http_status, response_keys=self.last_response_keys)
        token = _nested_value(payload, {"access_token", "accesstoken"})
        if not token: raise SandboxAuthenticationFailure("SANDBOX_ACCESS_TOKEN_MISSING", "Sandbox access token was missing.", diagnostics)
        diagnostics["access_token_received"] = True; self.last_auth_diagnostics = diagnostics
        from superadmin.services.sandbox_configuration import encrypt
        ttl = _ttl(payload, self.config["access_ttl"])
        self._last_token = token
        cache.set(self.cache_key("access-token"), encrypt(token), ttl)
        self._record_runtime_status(credentials_status="valid", connection_status="connected", last_error="",
                                    last_error_code="", last_verified_at=timezone.now(),
                                    session_expires_at=timezone.now() + timedelta(seconds=ttl))
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
        cache.delete(self.session_key(company_gstin))
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
        from superadmin.services.sandbox_configuration import encrypt
        index_key = self.cache_key("session-index")
        cache.set(index_key, list(set(cache.get(index_key, []) + [self.session_key(company_gstin)])), self.config["session_ttl"])
        cache.set(self.session_key(company_gstin), {"token": encrypt(taxpayer_token), "expires_at": expires_at.isoformat(),
            "gstin": str(company_gstin).strip().upper(), "username": str(username).strip()}, ttl)
        return {"verified": True, "session_active": True, "code": "TAXPAYER_SESSION_ACTIVE"}

    def taxpayer_session_state(self, company_gstin):
        session = cache.get(self.session_key(company_gstin))
        expires_at = parse_datetime(str(session.get("expires_at") or "")) if isinstance(session, dict) else None
        expired = bool(expires_at and expires_at <= timezone.now())
        if expired:
            cache.delete(self.session_key(company_gstin))
            session = None
        if session:
            from superadmin.services.sandbox_configuration import decrypt
            session = {**session, "token": decrypt(session["token"])}
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
        self._reload_configuration()
        issues = [key for key in ("base_url", "api_key", "api_secret") if not self.config.get(key)]
        configured = not issues
        invalid = self.config.get("credentials_status") == "invalid"
        session, session_expired = self.taxpayer_session_state(company_gstin) if company_gstin else (None, False)
        session_active = bool(session)
        return {"provider": "sandbox", "configured": configured, "provider_configured": configured,
                "configuration_code": "" if configured else "SANDBOX_NOT_CONFIGURED",
                "base_url_configured": bool(self.config.get("base_url")), "api_key_configured": bool(self.config.get("api_key")),
                "api_secret_configured": bool(self.config.get("api_secret")), "missing": issues,
                "authenticated": bool(cache.get(self.cache_key("access-token"))),
                "taxpayer_session_active": session_active, "session_active": session_active,
                "session_required": False, "session_expired": session_expired,
                "otp_required": False, "lookup_ready": configured and not invalid,
                "lookup_failed": invalid, "requires_username": False,
                "company_gstin": str(company_gstin or "").strip().upper(),
                "gst_details_endpoint_configured": True,
                "party_lookup_available": configured and not invalid,
                "party_lookup_requires_taxpayer_session": False}

    def lookup(self, gstin, company_gstin="", force=False):
        try:
            result = self._lookup(gstin, company_gstin, force)
            self._record_runtime_status(connection_status="connected", last_error="", last_error_code="")
            return result
        except GSTLookupAuthenticationError:
            self._record_runtime_status(last_error="GST party lookup credentials require an update. Contact the administrator.",
                                        last_error_code="SANDBOX_AUTH_FAILED", connection_status="authentication_failed")
            raise
        except (GSTLookupProviderError, GSTLookupTimeoutError):
            self._record_runtime_status(last_error="GST party lookup is temporarily unavailable.",
                                        last_error_code="SANDBOX_PROVIDER_UNAVAILABLE", connection_status="unavailable")
            raise

    def _lookup(self, gstin, company_gstin="", force=False):
        """Normal Party Details lookup: public GSTIN search using application auth only.

        No taxpayer OTP/session is requested or required here. `company_gstin`
        is accepted for signature compatibility but unused by this endpoint.
        `force` (an explicit user-triggered retry, see BatchPartiesView's
        retry_incomplete) bypasses a cached account-level block, so a retry
        always genuinely re-checks Sandbox instead of replaying a stale
        "quota exhausted" verdict -- it deliberately does NOT also discard an
        otherwise-valid cached access token, since retrying one GSTIN is not
        evidence that token itself is bad.
        """
        gstin = str(gstin or "").strip().upper()
        self.last_lookup_attempted = False
        self.lookup_request_count = 0
        self.last_http_status = None
        self.last_response_body = None
        self.last_response_shape = None
        token = self.authenticate(bypass_block=force)
        try:
            self.last_lookup_attempted = True
            self.lookup_request_count += 1
            payload = self._post(self.PUBLIC_GSTIN_SEARCH_PATH, self._public_headers(token), {"gstin": gstin})
        except GSTLookupAuthenticationError as exc:
            # Only a 401 means "this token is bad, get a fresh one and retry
            # once" -- a 403 is Sandbox rejecting the account/subscription
            # itself, which a new token cannot fix, so retrying would just
            # burn a second call against an already-exhausted quota.
            if getattr(exc, "http_status", None) == 403:
                raise
            token = self.authenticate(force=True)
            self.last_lookup_attempted = True
            self.lookup_request_count += 1
            payload = self._post(self.PUBLIC_GSTIN_SEARCH_PATH, self._public_headers(token), {"gstin": gstin})
        logger.info("Sandbox GST lookup gstin=%s status=%s", gstin, self.last_http_status)
        status_cd, data, self.last_response_shape = _extract_public_search_data(payload)
        if not data:
            raise GSTLookupProviderError("Sandbox public GSTIN payload is empty")
        if not status_cd:
            raise GSTLookupProviderError("Sandbox public GSTIN payload schema is invalid")
        if status_cd != "1":
            raise GSTLookupNotFoundError(gstin)
        returned_gstin = str(_first_value(data, "gstin", "Gstin") or "").strip().upper()
        if returned_gstin != gstin:
            raise GSTLookupProviderError("Sandbox GSTIN response did not match the requested GSTIN")
        address, principal_address, sandbox_state, pincode = _extract_address(data)
        state_code = _first_any_text(data, "state_code", "stateCode", "StateCode") or gstin[:2]
        state = sandbox_state or state_name(state_code) or state_name(gstin[:2])
        return GSTLookupResult(
            gstin=returned_gstin,
            legal_name=_first_any_text(data, "lgnm", "legal_name", "legalName"),
            trade_name=_first_any_text(data, "tradeNam", "trade_name", "tradeName"),
            status=_first_any_text(data, "sts", "status", "Status"),
            principal_address=principal_address, state=state, state_code=state_code or gstin[:2],
            registration_date=_first_any_text(data, "rgdt", "registration_date", "DtReg"),
            taxpayer_type=_regular_taxpayer_type(_first_value(data, "dty", "taxpayer_type", "taxpayerType", "TxpType")),
            pincode=pincode,
            cancellation_date=_first_any_text(data, "cxdt", "cancellation_date", "DtDReg"),
            constitution_of_business=_first_any_text(data, "ctb", "constitution_of_business"),
            einvoice_status=_first_any_text(data, "einvoiceStatus"),
            nature_of_business=_first_value(data, "nba") or [],
            state_jurisdiction=_first_any_text(data, "stj"), centre_jurisdiction=_first_any_text(data, "ctj"),
            state_jurisdiction_code=_first_any_text(data, "stjCd"), centre_jurisdiction_code=_first_any_text(data, "ctjCd"),
            last_updated_date=_first_any_text(data, "lstupdt", "lstupddt"), principal_address_details=address,
            principal_business_nature=(data.get("pradr", {}).get("ntr") if isinstance(data.get("pradr"), dict) else []) or [],
            additional_places=_first_value(data, "adadr") or [])
