"""Thin client for the documented Nexus Tally SL v1 API (docs/NEXUS-API-INTEGRATION.md).
The product key lives only in the backend environment; it is never sent to the browser."""
import json
import logging
import ssl
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


class NexusError(Exception):
    def __init__(self, status, code, message, errors=None):
        super().__init__(message)
        self.status, self.code, self.message, self.errors = status, code, message, errors or {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def base_url():
    root = settings.NEXUS_BASE_URL.rstrip("/")
    return f"{root}/api/v1/tally-sl/{settings.NEXUS_PRODUCT_ID}/"


def call(method, path, payload=None, token=None, sign=False):
    headers = {"Content-Type": "application/json", "X-Product-Key": settings.NEXUS_PRODUCT_KEY}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    if sign:  # backend-to-backend events: HMAC integrity + timestamp (see Nexus apps/tallysl/events.py)
        import hashlib, hmac, time
        ts = str(int(time.time()))
        headers["X-Nexus-Timestamp"] = ts
        headers["X-Nexus-Signature"] = hmac.new(settings.NEXUS_EVENT_SIGNING_SECRET.encode(), ts.encode() + b"." + (data or b""), hashlib.sha256).hexdigest()
    req = urllib.request.Request(base_url() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=settings.NEXUS_TIMEOUT) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode() or "{}")
        except ValueError:
            body = {}
        errors = body.get("errors") or {}
        if body.get("code") == "validation_error" and not errors:
            errors = {k: v for k, v in body.items() if k not in ("code", "message", "detail")}
        message = body.get("message") or body.get("detail")
        if not message and errors:
            message = " ".join(f"{key}: {'; '.join(map(str, value)) if isinstance(value, list) else value}" for key, value in errors.items())
        code, message = body.get("code", "nexus_error"), message or "Nexus request failed."
        if exc.code == 404 and not body:
            code, message = "nexus_endpoint_missing", "The licensing service does not provide this operation. Contact support."
        elif code in ("unauthenticated", "forbidden") and not token:
            # Our product key was refused (wrong/unknown key or key for another product) -- not the customer's credentials.
            code, message = "nexus_product_key_invalid", "The licensing service rejected this application's product key. Contact support."
        elif exc.code >= 500:
            code, message = "nexus_server_error", "The licensing service reported an internal error. Please try again later."
        logger.warning("Nexus %s %s -> HTTP %s code=%s", method, path, exc.code, code)
        raise NexusError(502 if exc.code >= 500 else exc.code, code, message, errors) from None
    except ssl.SSLError:
        raise NexusError(503, "nexus_tls_error", "Secure connection to the licensing service failed (certificate/TLS problem).") from None
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ssl.SSLError):
            raise NexusError(503, "nexus_tls_error", "Secure connection to the licensing service failed (certificate/TLS problem).") from None
        logger.error("Nexus unreachable at %s", base_url())
        raise NexusError(503, "nexus_unreachable", "Nexus server is unreachable. Please try again.") from None
    except (OSError, TimeoutError):
        logger.error("Nexus unreachable at %s", base_url())
        raise NexusError(503, "nexus_unreachable", "Nexus server is unreachable. Please try again.") from None
    except ValueError:
        raise NexusError(502, "nexus_bad_response", "The licensing service returned an unexpected response. Contact support.") from None
