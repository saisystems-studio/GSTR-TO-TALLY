from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pathlib import Path

from gst_tally.services.gst_lookup.service import GSTLookupService
from gst_tally.services.party_lookup import normalize_gstin, process_gstin, valid_gstin


class Command(BaseCommand):
    help = "Safely check GST taxpayer provider configuration and optionally test one GSTIN."

    def add_arguments(self, parser):
        parser.add_argument("--gstin", help="GSTIN to fetch and persist in gst_party_tbl")

    def handle(self, *args, **options):
        self.stdout.write(f"GST_LOOKUP_ENABLED = {settings.GST_LOOKUP_ENABLED}")
        self.stdout.write(f"GST_LOOKUP_PROVIDER = {settings.GST_LOOKUP_PROVIDER or '(missing)'}")
        self.stdout.write(f"GST_LOOKUP_BASE_URL configured = {bool(settings.GST_LOOKUP_BASE_URL)}")
        self.stdout.write(f"GST_LOOKUP_API_KEY configured = {bool(settings.GST_LOOKUP_API_KEY)}")
        self.stdout.write(f"GST_LOOKUP_CLIENT_ID configured = {bool(settings.GST_LOOKUP_CLIENT_ID)}")
        self.stdout.write(f"GST_LOOKUP_CLIENT_SECRET configured = {bool(settings.GST_LOOKUP_CLIENT_SECRET)}")
        self.stdout.write(f"GST_LOOKUP_USERNAME configured = {bool(settings.GST_LOOKUP_USERNAME)}")
        self.stdout.write(f"GST_LOOKUP_PASSWORD configured = {bool(settings.GST_LOOKUP_PASSWORD)}")
        if settings.GST_LOOKUP_PROVIDER == "vayana":
            self.stdout.write(f"VAYANA_BASE_URL configured = {bool(settings.VAYANA_BASE_URL)}")
            self.stdout.write(f"VAYANA_CLIENT_ID configured = {bool(settings.VAYANA_CLIENT_ID)}")
            self.stdout.write(f"VAYANA_CUST_ID configured = {bool(settings.VAYANA_CUST_ID)}")
            self.stdout.write(f"VAYANA_PRIVATE_KEY configured = {bool(settings.VAYANA_PRIVATE_KEY_PATH and Path(settings.VAYANA_PRIVATE_KEY_PATH).is_file())}")
            self.stdout.write(f"VAYANA_AUTH_GSTIN configured = {bool(settings.VAYANA_AUTH_GSTIN)}")
            self.stdout.write(f"VAYANA_SIGNATURE_ALGORITHM configured = {bool(settings.VAYANA_SIGNATURE_ALGORITHM)}")
        if settings.GST_LOOKUP_PROVIDER == "gstzen":
            self.stdout.write(f"GSTZEN_BASE_URL configured = {bool(settings.GSTZEN_BASE_URL)}")
            self.stdout.write(f"GSTZEN_API_ENDPOINT configured = {bool(settings.GSTZEN_API_ENDPOINT)}")
            self.stdout.write(f"GSTZEN_API_KEY configured = {bool(settings.GSTZEN_API_KEY)}")
            self.stdout.write(f"GSTZEN_CLIENT_ID configured = {bool(settings.GSTZEN_CLIENT_ID)}")
            self.stdout.write(f"GSTZEN_CLIENT_SECRET configured = {bool(settings.GSTZEN_CLIENT_SECRET)}")
        issues = GSTLookupService.configuration_issues()
        self.stdout.write(f"Provider configured = {not issues}")
        if issues: self.stdout.write(self.style.WARNING("Configuration issues: " + ", ".join(issues)))

        if not options["gstin"]: return
        gstin = normalize_gstin(options["gstin"])
        if not valid_gstin(gstin): raise CommandError("The supplied GSTIN has an invalid format")
        if issues: raise CommandError("Cannot test GSTIN until provider configuration is complete")
        status, party = process_gstin(gstin, force=True)
        self.stdout.write(f"Lookup status = {status}")
        if status not in {"Fetched", "Existing"}: raise CommandError(f"Provider lookup did not succeed: {status}")
        self.stdout.write(f"gst_party_tbl updated = {party is not None}")
        self.stdout.write(f"Trade Name configured = {bool(party and party.trade_name)}")
        self.stdout.write(f"Principal Place of Business configured = {bool(party and party.principal_place_of_business)}")
