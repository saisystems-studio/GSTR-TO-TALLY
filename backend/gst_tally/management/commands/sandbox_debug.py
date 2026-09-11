import json

from django.core.management.base import BaseCommand, CommandError

from gst_tally.services.gst_lookup.base import (GSTLookupAuthenticationError, GSTLookupNotFoundError,
                                                 GSTLookupProviderError, GSTLookupRateLimitError,
                                                 GSTLookupTimeoutError)
from gst_tally.services.gst_lookup.providers.sandbox import SandboxGSTProvider
from gst_tally.services.party_lookup import normalize_gstin, valid_gstin


class Command(BaseCommand):
    help = ("Call the Sandbox GST taxpayer lookup directly (no DB writes, no fallback/master logic) "
            "and print the raw HTTP result for one GSTIN.")

    def add_arguments(self, parser):
        parser.add_argument("gstin", nargs="?", default="33AABCT7933K1Z4")

    def handle(self, *args, **options):
        gstin = normalize_gstin(options["gstin"])
        if not valid_gstin(gstin):
            raise CommandError("The supplied GSTIN has an invalid format")

        provider = SandboxGSTProvider.from_settings()
        configured = not provider.configuration_issues()
        safe_status = provider.safe_status(gstin) if configured else {}

        access_token_present = False
        exception_type = ""
        exception_message = ""
        result = None
        if configured:
            try:
                token = provider.authenticate()
                access_token_present = bool(token)
                result = provider.lookup(gstin)
            except GSTLookupAuthenticationError as exc:
                exception_type, exception_message = type(exc).__name__, str(exc)
            except GSTLookupRateLimitError as exc:
                exception_type, exception_message = type(exc).__name__, str(exc)
            except GSTLookupTimeoutError as exc:
                exception_type, exception_message = type(exc).__name__, str(exc)
            except GSTLookupNotFoundError as exc:
                exception_type, exception_message = type(exc).__name__, str(exc)
            except GSTLookupProviderError as exc:
                exception_type, exception_message = type(exc).__name__, str(exc)
            except Exception as exc:  # deliberately not swallowed into a generic message
                exception_type, exception_message = type(exc).__name__, str(exc)

        json_parse_success = False
        if provider.last_response_body:
            try:
                json.loads(provider.last_response_body)
                json_parse_success = True
            except (TypeError, ValueError):
                json_parse_success = False

        meta = provider.last_request_metadata or {}

        self.stdout.write("========== SANDBOX DEBUG ==========")
        self.stdout.write(f"GSTIN: {gstin}")
        self.stdout.write(f"SANDBOX CONFIGURED: {configured}")
        self.stdout.write(f"ENDPOINT: {meta.get('url') or provider._url(provider.PUBLIC_GSTIN_SEARCH_PATH)}")
        self.stdout.write("HTTP METHOD: POST")
        self.stdout.write(f"CLIENT/AUTH HEADER PRESENT: {bool(meta.get('auth_token_attached') or meta.get('api_key_attached'))}")
        self.stdout.write(f"ACCESS TOKEN PRESENT: {access_token_present}")
        self.stdout.write(f"SESSION REQUIRED: {bool(safe_status.get('session_required'))}")
        self.stdout.write(f"SESSION ACTIVE: {bool(safe_status.get('session_active'))}")
        self.stdout.write(f"REQUEST ATTEMPTED: {bool(provider.last_lookup_attempted)}")
        self.stdout.write(f"HTTP STATUS: {provider.last_http_status}")
        self.stdout.write(f"RAW RESPONSE BODY: {provider.last_response_body}")
        self.stdout.write(f"RESPONSE HEADERS / REQUEST ID: {meta.get('request_id') or '(none returned)'}")
        self.stdout.write(f"EXCEPTION TYPE: {exception_type or '(none)'}")
        self.stdout.write(f"EXCEPTION MESSAGE: {exception_message or '(none)'}")
        self.stdout.write(f"JSON PARSE SUCCESS: {json_parse_success}")
        self.stdout.write("===================================")

        if result is not None:
            self.stdout.write("")
            self.stdout.write("HTTP 200 -- parsed taxpayer fields:")
            self.stdout.write(f"  trade_name: {result.trade_name}")
            self.stdout.write(f"  legal_name: {result.legal_name}")
            self.stdout.write(f"  gstin: {result.gstin}")
            self.stdout.write(f"  gst_status: {result.status}")
            self.stdout.write(f"  taxpayer_type: {result.taxpayer_type}")
            self.stdout.write(f"  registration_date: {result.registration_date}")
            self.stdout.write(f"  principal_place_of_business: {result.principal_address}")
            self.stdout.write(f"  state: {result.state}")
            self.stdout.write(f"  pincode: {result.pincode}")
