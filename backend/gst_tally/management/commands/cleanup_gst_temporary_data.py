from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from gst_tally.models import GSTImportBatch
from gst_tally.services.import_service import cleanup_expired_processing_rows


class Command(BaseCommand):
    help = "Delete expired temporary GST session data; retain permanent file summaries."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-hours", type=int, default=24)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # A session owns its exact expiry boundary.  The option remains for
        # command compatibility, but the 24-hour retention decision is the
        # persisted `expires_at <= now` predicate, never `created_at`.
        cutoff = timezone.now()
        counts = {"expired_sessions": GSTImportBatch.objects.filter(expires_at__lte=cutoff).count()}
        if options["dry_run"]:
            self.stdout.write(str(counts))
            return
        self.stdout.write(str(cleanup_expired_processing_rows(now=cutoff)))
