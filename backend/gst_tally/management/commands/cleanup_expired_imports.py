from django.core.management.base import BaseCommand
from gst_tally.services.import_service import cleanup_expired_processing_rows


class Command(BaseCommand):
    help = "Remove expired temporary import data; permanent file summaries are retained."

    def handle(self, *args, **options):
        self.stdout.write(str(cleanup_expired_processing_rows()))
