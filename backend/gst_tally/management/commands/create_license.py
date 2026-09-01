from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from gst_tally.auth_service import secret_hash
from gst_tally.models import License


class Command(BaseCommand):
    help = "Create a product license with a securely hashed activation key"

    def add_arguments(self, parser):
        parser.add_argument("--serial", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--key", required=True)
        parser.add_argument("--status", choices=License.Status.values, default=License.Status.ACTIVE)
        parser.add_argument("--max-devices", type=int, default=1)
        parser.add_argument("--expiry")

    def handle(self, *args, **options):
        if options["max_devices"] < 1: raise CommandError("--max-devices must be at least 1")
        expiry = parse_date(options["expiry"]) if options["expiry"] else None
        if options["expiry"] and not expiry: raise CommandError("--expiry must use YYYY-MM-DD")
        serial = options["serial"].strip().upper()
        email = options["email"].strip().lower()
        key_hash = secret_hash(options["key"])
        if License.objects.filter(serial_number__iexact=serial).exists():
            raise CommandError(f"License serial {serial} already exists.")
        if License.objects.filter(activation_key_hash=key_hash).exists():
            raise CommandError("Activation key is already in use.")
        license_obj = License.objects.create(
            serial_number=serial, registered_email=email, activation_key_hash=key_hash,
            status=options["status"], max_devices=options["max_devices"], expiry_date=expiry,
        )
        self.stdout.write(self.style.SUCCESS(f"License Created Successfully (ID {license_obj.id}, serial {serial}); raw key was not stored."))
