from django.conf import settings
from django.core.management.base import BaseCommand

from gst_tally.models import NexusOutboxEvent
from gst_tally.services.device_transfer import deliver_due


class Command(BaseCommand):
    help = "Retry delivery of pending Nexus outbox events (schedule every few minutes). Prints config status, never secrets."

    def handle(self, *args, **options):
        self.stdout.write("signing secret configured: %s | events path configured: %s | nexus enabled: %s" % (
            "yes" if getattr(settings, "NEXUS_EVENT_SIGNING_SECRET", "") else "NO",
            "yes" if getattr(settings, "NEXUS_EVENTS_PATH", "") else "NO",
            bool(getattr(settings, "NEXUS_ENABLED", False))))
        results = deliver_due()
        self.stdout.write("processed %d event(s): %s" % (len(results), results))
        for status in (NexusOutboxEvent.PENDING, NexusOutboxEvent.SENT, NexusOutboxEvent.FAILED):
            self.stdout.write("%s: %d" % (status, NexusOutboxEvent.objects.filter(status=status).count()))
