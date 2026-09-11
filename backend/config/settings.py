import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env(name, default=""):
    return os.environ.get(name, default)

def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}

SECRET_KEY = env("DJANGO_SECRET_KEY", "5p!!e4hr8to5&b#&lo6hflix1mcii!drg3&y*qhu-xx-##q-jx")
DEBUG = env("DJANGO_DEBUG", "True").lower() == "true"
APP_VERSION = env("APP_VERSION")
TERMS_LAST_UPDATED = env("TERMS_LAST_UPDATED", "2026-09-09")
ALLOWED_HOSTS = [x.strip() for x in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "corsheaders", "rest_framework", "gst_tally", "subscriptions",
    "superadmin",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Additive only -- gates just the /api/gst-tally/ and /api/gst/lookup|sandbox/
    # prefixes (see subscriptions/middleware.py's PROTECTED_PREFIXES); every
    # other existing route (auth, admin, this app's own endpoints) is
    # completely untouched by it.
    "subscriptions.middleware.SubscriptionEnforcementMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": {
    "ENGINE": "mssql",
    "NAME": env("DB_NAME"),
    "USER": env("DB_USER"),
    "PASSWORD": env("DB_PASSWORD"),
    "HOST": env("DB_HOST"),
    "PORT": env("DB_PORT"),
    "Trusted_Connection": env("DB_TRUSTED_CONNECTION", "yes"),
    "OPTIONS": {
        "driver": env("DB_DRIVER", "ODBC Driver 17 for SQL Server"),
    },
}}
# Sandbox access/taxpayer sessions and the Tally import lock must survive the
# individual worker process that handled authentication. LocMemCache silently
# loses that state when the next request reaches another worker.
CACHES = {"default": {
    "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
    "LOCATION": env("DJANGO_CACHE_LOCATION", str(BASE_DIR / ".cache")),
    "TIMEOUT": 21600,
    "OPTIONS": {"MAX_ENTRIES": 1000},
}}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
CORS_ALLOWED_ORIGINS = [x.strip() for x in env("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if x.strip()]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser", "rest_framework.parsers.FormParser", "rest_framework.parsers.MultiPartParser"],
    "DEFAULT_RENDERER_CLASSES": ["gst_tally.renderers.UTF8JSONRenderer"],
    "EXCEPTION_HANDLER": "config.exceptions.exception_handler",
}
# Session-style JWT, no server-side blacklist: an access token authenticates
# requests for its own lifetime; while the refresh token is still valid the
# frontend silently exchanges it for a new access token (see
# authApi.js::authenticatedFetch/refreshAccess) so an active user is never
# interrupted. Each successful refresh issues a brand-new refresh token
# (ROTATE_REFRESH_TOKENS) without blacklisting the old one -- once the
# refresh token itself expires (30 days of no activity), the user must log
# in again and gets completely new tokens.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
}
# Defaults to printing emails to the console when no SMTP host is
# configured, so the forgot-password flow works out of the box in dev
# without requiring real mail credentials.
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_BACKEND = env("EMAIL_BACKEND") or ("django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST else "django.core.mail.backends.console.EmailBackend")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@gstr2tally.local")

DATA_UPLOAD_MAX_MEMORY_SIZE = int(env("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
TALLY_HOST = env("TALLY_HOST", "127.0.0.1").removeprefix("http://").removeprefix("https://").rstrip("/")
TALLY_PORT = int(env("TALLY_PORT", "9000"))
TALLY_BASE_URL = env("TALLY_BASE_URL", f"http://{TALLY_HOST}:{TALLY_PORT}")
TALLY_WRITE_FORMAT = env("TALLY_WRITE_FORMAT", "JSON").strip().upper()
TALLY_MIN_JSON_VERSION = env("TALLY_MIN_JSON_VERSION", "7.0")
TALLY_VERSION = env("TALLY_VERSION", "7.1").strip()
TALLY_TIMEOUT = int(env("TALLY_TIMEOUT", "30"))
TALLY_CONNECT_TIMEOUT = int(env("TALLY_CONNECT_TIMEOUT", "3"))
TALLY_READ_TIMEOUT = int(env("TALLY_READ_TIMEOUT", "12"))
TALLY_HTTP_KEEPALIVE = env("TALLY_HTTP_KEEPALIVE", "true").lower() in {"1", "true", "yes", "on"}
TALLY_IMPORT_PROGRESS_INTERVAL = float(env("TALLY_IMPORT_PROGRESS_INTERVAL", "0.5"))
TALLY_IMPORT_BATCH_SIZE = int(env("TALLY_IMPORT_BATCH_SIZE", "500"))
TALLY_IMPORT_MAX_XML_BYTES = int(env("TALLY_IMPORT_MAX_XML_BYTES", str(8 * 1024 * 1024)))
TALLY_ENABLED = env_bool("TALLY_ENABLED", True)
TALLY_DRY_RUN = env_bool("TALLY_DRY_RUN", True)
TALLY_EXPECTED_COMPANY = env("TALLY_EXPECTED_COMPANY")
TALLY_COMPANY_STATE = env("TALLY_COMPANY_STATE")
TALLY_COMPANY_GSTIN = env("TALLY_COMPANY_GSTIN")
TALLY_ODBC_ENABLED = env_bool("TALLY_ODBC_ENABLED", True)
TALLY_ODBC_DSN = env("TALLY_ODBC_DSN", "TallyODBC64_9000")
TALLY_ODBC_CONNECTION_STRING = env("TALLY_ODBC_CONNECTION_STRING")
TALLY_ODBC_TIMEOUT = int(env("TALLY_ODBC_TIMEOUT", "10"))
# Kept for compatibility with older deployments. It never simulates a successful import.
TALLY_MOCK = env_bool("TALLY_MOCK", False)
TALLY_SINGLE_VOUCHER_GATE = env_bool("TALLY_SINGLE_VOUCHER_GATE", True)
GST_LOOKUP_ENABLED = env("GST_LOOKUP_ENABLED", "false").lower() == "true"
GST_LOOKUP_PROVIDER = env("GST_LOOKUP_PROVIDER")
GST_LOOKUP_PRIMARY_PROVIDER = env("GST_LOOKUP_PRIMARY_PROVIDER", GST_LOOKUP_PROVIDER)
GST_LOOKUP_FALLBACK_PROVIDER = env("GST_LOOKUP_FALLBACK_PROVIDER")
GST_LOOKUP_BASE_URL = env("GST_LOOKUP_BASE_URL")
GST_LOOKUP_ENDPOINT = env("GST_LOOKUP_ENDPOINT")
GST_LOOKUP_API_KEY = env("GST_LOOKUP_API_KEY")
GST_LOOKUP_API_KEY_HEADER = env("GST_LOOKUP_API_KEY_HEADER", "X-API-Key")
GST_LOOKUP_CLIENT_ID = env("GST_LOOKUP_CLIENT_ID")
GST_LOOKUP_CLIENT_SECRET = env("GST_LOOKUP_CLIENT_SECRET")
GST_LOOKUP_USERNAME = env("GST_LOOKUP_USERNAME")
GST_LOOKUP_PASSWORD = env("GST_LOOKUP_PASSWORD")
GST_LOOKUP_AUTHORIZATION = env("GST_LOOKUP_AUTHORIZATION")
GST_LOOKUP_TIMEOUT = int(env("GST_LOOKUP_TIMEOUT", "20"))
GST_PARTY_FRESH_DAYS = int(env("GST_PARTY_FRESH_DAYS", "30"))
GST_LOOKUP_MAX_RETRY_DELAY = int(env("GST_LOOKUP_MAX_RETRY_DELAY", "30"))
SANDBOX_BASE_URL = env("SANDBOX_BASE_URL", "https://api.sandbox.co.in")
SANDBOX_API_KEY = env("SANDBOX_API_KEY")
SANDBOX_API_SECRET = env("SANDBOX_API_SECRET")
SANDBOX_API_VERSION = env("SANDBOX_API_VERSION", "1.0.0")
SANDBOX_ACCESS_TOKEN_TTL = int(env("SANDBOX_ACCESS_TOKEN_TTL", "300"))
SANDBOX_TAXPAYER_SESSION_TTL = int(env("SANDBOX_TAXPAYER_SESSION_TTL", "21600"))
# How long an account-level /authenticate rejection (403 -- quota/subscription/
# permission, never a per-request token issue) is remembered before the next
# GSTIN lookup is allowed to hit Sandbox again. Without this, a batch with N
# unique GSTINs makes N separate failing /authenticate calls once the account
# is blocked, instead of discovering the block once and reusing it.
SANDBOX_AUTH_FAILURE_COOLDOWN = int(env("SANDBOX_AUTH_FAILURE_COOLDOWN", "60"))
JAMKU_BASE_URL = env("JAMKU_BASE_URL", "https://gst-return-status.p.rapidapi.com")
JAMKU_GSTIN_ENDPOINT = env("JAMKU_GSTIN_ENDPOINT", "/free/gstin/{gstin}")
JAMKU_RAPIDAPI_HOST = env("JAMKU_RAPIDAPI_HOST", "gst-return-status.p.rapidapi.com")
JAMKU_RAPIDAPI_KEY = env("JAMKU_RAPIDAPI_KEY")
GSTINAPI_BASE_URL = env("GSTINAPI_BASE_URL", "https://www.gstinapi.in")
GSTINAPI_ENDPOINT = env("GSTINAPI_ENDPOINT", "/v1/gstin/{gstin}")
GSTINAPI_API_KEY = env("GSTINAPI_API_KEY")
GSTINAPI_API_KEY_HEADER = env("GSTINAPI_API_KEY_HEADER", "x-api-key")
VAYANA_BASE_URL = env("VAYANA_BASE_URL")
VAYANA_CLIENT_ID = env("VAYANA_CLIENT_ID")
VAYANA_CUST_ID = env("VAYANA_CUST_ID")
VAYANA_PRIVATE_KEY_PATH = env("VAYANA_PRIVATE_KEY_PATH")
VAYANA_AUTH_GSTIN = env("VAYANA_AUTH_GSTIN")
VAYANA_SIGNATURE_ALGORITHM = env("VAYANA_SIGNATURE_ALGORITHM")
GSTZEN_BASE_URL = env("GSTZEN_BASE_URL")
GSTZEN_API_ENDPOINT = env("GSTZEN_API_ENDPOINT")
GSTZEN_API_KEY = env("GSTZEN_API_KEY")
GSTZEN_CLIENT_ID = env("GSTZEN_CLIENT_ID")
GSTZEN_CLIENT_SECRET = env("GSTZEN_CLIENT_SECRET")
