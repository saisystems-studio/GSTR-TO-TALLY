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
    configuration_id = cache.get(ACTIVE_CONFIGURATION_CACHE_KEY)
    if configuration_id:
        configuration = SandboxAPIConfiguration.objects.filter(
            pk=configuration_id, provider="sandbox", is_active=True
        ).first()
        if configuration:
            return configuration
    configuration = SandboxAPIConfiguration.objects.filter(provider="sandbox", is_active=True).order_by("-updated_at").first()
    if configuration:
        cache.set(ACTIVE_CONFIGURATION_CACHE_KEY, configuration.pk, ACTIVE_CONFIGURATION_CACHE_SECONDS)
    return configuration


def provider_config(configuration=None):
    configuration = configuration or get_active_configuration()
    if not configuration:
        return None
    return {"api_key": decrypt(configuration.api_key_encrypted), "api_secret": decrypt(configuration.api_secret_encrypted),
            "api_version": configuration.api_version, "environment": configuration.environment}


def clear_cached_access_token():
    cache.delete(ACTIVE_CONFIGURATION_CACHE_KEY)
    cache.delete("gst:sandbox:access-token")
    cache.delete("gst:sandbox:provider-block")


def safe_status(configuration=None):
    configuration = configuration or get_active_configuration()
    if not configuration:
        return {"provider": "sandbox", "environment": "test", "configured": False, "authenticated": False,
                "lookup_ready": False, "last_verified_at": None, "last_error": "Sandbox configuration is not configured."}
    return {"provider": configuration.provider, "environment": configuration.environment, "configured": True,
            "authenticated": bool(configuration.last_verified_at and not configuration.last_error),
            "lookup_ready": bool(configuration.last_verified_at and not configuration.last_error),
            "last_verified_at": configuration.last_verified_at, "last_error": configuration.last_error}
