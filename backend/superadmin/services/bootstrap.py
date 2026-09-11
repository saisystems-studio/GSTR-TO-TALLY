from django.contrib.auth import get_user_model

from ..models import SuperAdminProfile

User = get_user_model()

DEFAULT_USERNAME = "Superadmin"
DEFAULT_PASSWORD = "123"


def create_default_superadmin():
    """Idempotent (spec section 64): safe to call on every deploy. Never
    creates a duplicate -- `get_or_create` on the username, and the profile
    is only ever created once alongside it. Returns (user, created)."""
    user, created = User.objects.get_or_create(
        username=DEFAULT_USERNAME,
        defaults={"email": "", "is_staff": True, "is_superuser": True, "is_active": True},
    )
    if created:
        user.set_password(DEFAULT_PASSWORD)
        user.save(update_fields=["password"])
    SuperAdminProfile.objects.get_or_create(
        user=user, defaults={"display_name": "Super Admin", "must_change_password": True},
    )
    return user, created
