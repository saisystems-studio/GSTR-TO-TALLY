from django.core.management.base import BaseCommand, CommandError

from gst_tally.services.party_lookup import normalize_gstin, process_gstin, result_row, valid_gstin


class Command(BaseCommand):
    help = "Run one real Sandbox GST taxpayer lookup and print the full diagnostic trail (no swallowed errors)."

    def add_arguments(self, parser):
        parser.add_argument("gstin", nargs="?", default="33AABCT7933K1Z4")

    def handle(self, *args, **options):
        gstin = normalize_gstin(options["gstin"])
        if not valid_gstin(gstin):
            raise CommandError("The supplied GSTIN has an invalid format")

        status, party = process_gstin(gstin, force=True)
        row = result_row(gstin, status, party)
        sl = row.get("sandbox_lookup") or {}

        self.stdout.write("=== SANDBOX GST LOOKUP TEST ===")
        self.stdout.write(f"GSTIN: {gstin}")
        self.stdout.write(f"REQUEST ATTEMPTED: {sl.get('attempted')}")
        self.stdout.write(f"ENDPOINT: {sl.get('endpoint')}")
        self.stdout.write(
            f"AUTH PRESENT: auth_token={sl.get('auth_token_attached')} api_key={sl.get('api_key_attached')}"
        )
        self.stdout.write(f"HTTP STATUS: {sl.get('http_status')}")
        self.stdout.write(f"RESPONSE BODY: {sl.get('response_body')}")
        self.stdout.write(f"PARSE SUCCESS: {sl.get('success')}")
        self.stdout.write(f"TRADE NAME: {row.get('trade_name')}")
        self.stdout.write(f"LEGAL NAME: {row.get('legal_name')}")
        self.stdout.write(f"ADDRESS: {row.get('principal_place_of_business')}")
        self.stdout.write(f"STATE: {row.get('state')}")
        self.stdout.write(f"PINCODE: {row.get('pincode')}")
        self.stdout.write(f"ERROR CODE: {sl.get('error_code')}")
        self.stdout.write(f"ERROR MESSAGE: {sl.get('error_message')}")
        self.stdout.write("=== END SANDBOX TEST ===")
