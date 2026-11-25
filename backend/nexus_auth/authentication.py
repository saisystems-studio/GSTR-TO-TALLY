from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import NexusLink


class NexusAwareJWTAuthentication(JWTAuthentication):
    """Existing JWT validation plus Nexus revocation. A Nexus-linked user is
    rejected once the link is revoked or the offline lease has run out (a
    successful online check extends it). Users without a link are unaffected
    unless NEXUS_ENFORCE is on (staff stay exempt)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if not result:
            return result
        user, validated = result
        link_id = validated.get("nexus_link")
        link = NexusLink.objects.filter(pk=link_id, user=user).first() if link_id else None
        if link_id and link is None:
            raise AuthenticationFailed("This session has ended. Please sign in again.", code="session_revoked")
        if link is None and user.nexus_links.exists():
            raise AuthenticationFailed("Nexus sign-in required.", code="nexus_required")  # Nexus accounts never fall back to unbound tokens
        if link is None:
            if getattr(settings, "NEXUS_ENFORCE", False) and not user.is_staff:
                raise AuthenticationFailed("Nexus sign-in required.", code="nexus_required")
            return result
        if link.revoked_reason:
            raise AuthenticationFailed("This session has ended. Please sign in again.", code="session_revoked")
        if link.lease_until and link.lease_until < timezone.now():
            raise AuthenticationFailed("License check required. Connect to the internet and sign in again.", code="lease_expired")
        return result
