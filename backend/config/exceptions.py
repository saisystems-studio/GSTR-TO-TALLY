from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """Wraps DRF's default handler to surface an APIException's `.code` (e.g.
    SimpleJWT's `AuthenticationFailed(..., code="user_not_found")`) as a top
    level `code` field on the JSON body. DRF's own JSON encoding otherwise
    drops it -- ErrorDetail is a str subclass, so it serializes as a plain
    string and its `.code` attribute never reaches the response.

    Frontend auth handling depends on this to tell "user row no longer
    exists" apart from an ordinary expired-access-token 401, which looks
    identical without it.
    """
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and "code" not in response.data:
        code = getattr(getattr(exc, "detail", None), "code", None)
        if code:
            response.data["code"] = code
    return response
