from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from nexus_auth.models import NexusLink


class Command(BaseCommand):
    help = ("READ-ONLY report used before turning NEXUS_ENFORCE on: which non-staff accounts would lose access "
            "because they have no Nexus link. Changes nothing.")

    def handle(self, *args, **options):
        User = get_user_model()
        users = User.objects.filter(is_active=True, is_staff=False, is_superuser=False).exclude(
            superadmin_profile__isnull=False)
        linked = users.filter(nexus_links__isnull=False).distinct()
        legacy = users.exclude(pk__in=linked.values("pk"))
        now = timezone.now()
        healthy = NexusLink.objects.filter(revoked_reason="").exclude(lease_until__lt=now).values("user").distinct().count()
        self.stdout.write("active customer accounts: %d" % users.count())
        self.stdout.write("  Nexus-linked: %d (with a valid, unexpired link: %d)" % (linked.count(), healthy))
        self.stdout.write("  legacy (no Nexus link, WOULD BE BLOCKED by NEXUS_ENFORCE=True): %d" % legacy.count())
        for u in legacy.order_by("id")[:50]:
            self.stdout.write("    id=%s username=%s last_login=%s" % (u.id, u.username, u.last_login))
        self.stdout.write("Safe to enforce only when the legacy count is 0 or each legacy account has been migrated/accepted.")
