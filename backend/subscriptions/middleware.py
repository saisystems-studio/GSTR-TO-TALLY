from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication

from .services import check_request_block

# Only the actual GSTR 2 Tally paid pipeline is gated -- everything else
# (auth, this app's own subscription endpoints, Django admin, GST lookup
# status/diagnostic-only reads used before a company is even chosen) must
# keep working for an expired/suspended account so they can see why they're
# blocked and renew (spec section 23). Deliberately a prefix allowlist of
# what's PROTECTED, not a blocklist of what's exempt -- new endpoints added
# outside these prefixes stay ungated by default, which is the safer
# direction for a subscription check to fail into.
PROTECTED_PREFIXES = ("/api/gst-tally/", "/api/gst/lookup/", "/api/gst/sandbox/")


class SubscriptionEnforcementMiddleware:
    """Blocks paid GSTR 2 Tally operations once an account's subscription is
    EXPIRED or SUSPENDED (spec sections 4, 6, 22). Resolves the requesting
    user directly via SimpleJWT rather than relying on `request.user` --
    JWTAuthentication normally only runs inside DRF's own APIView.dispatch(),
    which happens *after* Django's middleware stack, so `request.user` here
    would still be Django's own AnonymousUser even for an authenticated API
    call. Doing the same JWT lookup here, read-only, means zero existing
    views/permissions/URLs need to change to get this enforcement -- the
    entire feature is additive at the settings.py level (one new app, one
    new middleware line).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._jwt_auth = JWTAuthentication()

    def __call__(self, request):
        if request.path.startswith(PROTECTED_PREFIXES):
            user = self._resolve_user(request)
            if user is not None:
                blocked = check_request_block(user)
                if blocked is not None:
                    return JsonResponse(
                        {"code": blocked.code, "detail": blocked.detail,
                         "expiry_date": blocked.expiry_date.isoformat() if blocked.expiry_date else None},
                        status=402,
                    )
        return self.get_response(request)

    def _resolve_user(self, request):
        # Any auth failure here (missing/expired/malformed token) just means
        # "can't identify this caller" -- fall through as anonymous and let
        # DRF's own authentication give the real 401 further down the stack;
        # this middleware only ever adds a block, never an auth decision.
        try:
            result = self._jwt_auth.authenticate(request)
        except Exception:
            return None
        if result is None:
            return None
        user, _validated_token = result
        return user
