from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from gst_tally.models import GSTImportBatch, GSTSyncLog, TallyImportJob, TallyVoucherMapping


class Command(BaseCommand):
    help = "Delete temporary GST import working data older than the retention window. Permanent summaries and registry rows are preserved."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-hours", type=int, default=24)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options["older_than_hours"])
        batches = GSTImportBatch.objects.filter(created_at__lt=cutoff)
        batch_ids = list(batches.values_list("id", flat=True))
        counts = {
            "batches": len(batch_ids),
            "voucher_mappings": TallyVoucherMapping.objects.filter(batch_id__in=batch_ids).count(),
            "sync_logs": GSTSyncLog.objects.filter(batch_id__in=batch_ids).count(),
            "jobs": TallyImportJob.objects.filter(batch_id__in=batch_ids).count(),
        }
        if options["dry_run"]:
            self.stdout.write(str(counts))
            return
        with transaction.atomic():
            TallyVoucherMapping.objects.filter(batch_id__in=batch_ids).delete()
            GSTSyncLog.objects.filter(batch_id__in=batch_ids).delete()
            TallyImportJob.objects.filter(batch_id__in=batch_ids).delete()
            deleted_batches, _ = batches.delete()
        self.stdout.write(f"Deleted temporary GST data older than {options['older_than_hours']} hours: {deleted_batches} rows")
