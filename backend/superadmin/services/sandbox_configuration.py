import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.cache import cache

from ..models import SandboxAPIConfiguration

ACTIVE_CONFIGURATION_CACHE_KEY = "superadmin:sandbox:active-configuration"
ACTIVE_CONFIGURATION_CACHE_SECONDS = 300


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()))


def encrypt(value):
    return _fernet().encrypt(str(value).encode()).decode()


def decrypt(value):
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, AttributeError):
        return ""


def get_active_configuration():
    # Read the database every time: process-local cache must never select old credentials.
    return SandboxAPIConfiguration.objects.filter(provider="sandbox", is_active=True).order_by("-updated_at").first()


def provider_config(configuration=None):
    configuration = configuration or get_active_configuration()
    if not configuration:
        return None
    return {"api_key": decrypt(configuration.api_key_encrypted), "api_secret": decrypt(configuration.api_secret_encrypted),
            "api_version": configuration.api_version, "environment": configuration.environment,
            "configuration_id": configuration.pk, "credential_revision": str(configuration.credential_revision),
            "credentials_status": configuration.credentials_status}


def cache_scope(config):
    import json
    from django.utils.crypto import salted_hmac
    identity = [config.get(key, "") for key in (
        "base_url", "environment", "api_version", "api_key", "api_secret", "credential_revision")]
    return salted_hmac("sandbox-session", json.dumps(identity), algorithm="sha256").hexdigest()


def clear_cached_access_token(configuration=None):
    cache.delete(ACTIVE_CONFIGURATION_CACHE_KEY)
    cache.delete("gst:sandbox:access-token")
    cache.delete("gst:sandbox:provider-block")
    if configuration:
        from gst_tally.services.gst_lookup.providers.sandbox import SandboxGSTProvider
        provider = SandboxGSTProvider(SandboxGSTProvider.config_for_credentials(provider_config(configuration)))
        provider.clear_session_cache()


def safe_status(configuration=None):
    from django.utils import timezone
    configuration = configuration or get_active_configuration()
    configured = bool(configuration)
    valid = configured and configuration.credentials_status == "valid"
    connected = configured and configuration.connection_status == "connected"
    expires = configuration.session_expires_at if configuration else None
    expired = bool(expires and expires <= timezone.now())
    return {"provider": "sandbox", "environment": configuration.environment if configuration else "test",
            "configured": configured, "provider_configured": configured,
            "authenticated": bool(valid and connected), "lookup_ready": bool(valid and connected),
            "credentials_status": configuration.credentials_status if configuration else "unverified",
            "connection_status": configuration.connection_status if configuration else "unverified",
            "session_required": True, "session_active": bool(valid and expires and not expired),
            "session_expired": expired, "otp_required": False,
            "lookup_failed": bool(configuration and configuration.last_error),
            "last_verified_at": configuration.last_verified_at if configuration else None,
            "last_error": configuration.last_error if configuration else "Provider configuration is not configured.",
            "last_error_code": configuration.last_error_code if configuration else "",
            "expires_at": configuration.expires_at if configuration else None}
