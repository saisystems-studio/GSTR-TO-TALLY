from rest_framework.permissions import BasePermission


class IsSuperAdminAccount(BasePermission):
    """Identity-only gate (spec sections 7, 63): presence of a
    `SuperAdminProfile` row, not `is_staff`/`is_superuser` alone and never
    anything from the frontend/URL. A normal customer -- even an
    authenticated one -- has no such row and is refused with 403. Used
    directly only by change-password/me/logout, which must stay reachable
    even while the forced first-login password change is still pending."""

    message = "Super Admin access is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and hasattr(user, "superadmin_profile"))


class IsSuperAdmin(IsSuperAdminAccount):
    """The gate on /api/superadmin/* endpoints: identity comes from the
    `SuperAdminProfile` row. Password changes stay available from Profile but
    are not required before opening the dashboard."""

    pass
