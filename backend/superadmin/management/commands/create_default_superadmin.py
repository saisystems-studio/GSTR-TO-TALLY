from django.core.management.base import BaseCommand

from superadmin.services.bootstrap import DEFAULT_PASSWORD, DEFAULT_USERNAME, create_default_superadmin


class Command(BaseCommand):
    help = "Idempotently creates the default Super Admin bootstrap account (Superadmin/123, forced password change on first login)."

    def handle(self, *args, **options):
        _user, created = create_default_superadmin()
        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created default Super Admin '{DEFAULT_USERNAME}' with the initial password '{DEFAULT_PASSWORD}'. "
                "A password change will be forced on first login."
            ))
        else:
            self.stdout.write(f"Super Admin '{DEFAULT_USERNAME}' already exists -- nothing to do.")
