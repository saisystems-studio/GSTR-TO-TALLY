from datetime import date
from decimal import Decimal, InvalidOperation
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import GSTImportBatch, GSTParty
from .serializers import GSTImportBatchListSerializer, GSTImportBatchSerializer
from .services.import_service import import_file
from .services.import_identity import DuplicateImportError
from .services.company import resolve_batch_company
from .services.source_preview import preview_file
from .services.party_lookup import (lookup_configuration_message, lookup_is_configured,
                                    normalize_gstin, party_eligibility, party_is_complete, party_is_fresh, process_gstin, result_row,
                                    retry_allowed, valid_gstin)
from .services.party_ledger_name import resolve_party_ledger_name
from .tally.connection import connection_status, diagnostics, step3_connection_check
from .tally.odbc import odbc_company_status
from .services.tally_license import verify_batch_license
from .tally.service import (correct_invoice_value, save_voucher_correction, import_batch as import_tally_batch, prepare_master_results,
                            voucher_preview)
from .tally.import_lock import TallyImportLock
from .services.gst_lookup.base import (GSTLookupAuthenticationError, GSTLookupConfigurationError,
                                       GSTLookupNotFoundError, GSTLookupProviderError,
                                       GSTLookupRateLimitError, GSTLookupTimeoutError)
from .services.gst_lookup.service import GSTLookupService
from .services.gst_lookup.providers.sandbox import (SandboxAuthenticationFailure, SandboxGSTProvider,
                                                     SandboxOTPRequestFailure, SandboxOTPRequired)

class SourcePreviewView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    def post(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded: return Response({"detail": "File is required"}, status=400)
        try: return Response(preview_file(uploaded, request.data.get("return_type", ""), request.data.get("sheet_name")))
        except ValueError as exc: return Response({"detail": str(exc)}, status=400)

class ImportView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    def post(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded: return Response({"detail": "File is required"}, status=400)
        try:
            batch = import_file(uploaded, request.data.get("return_type", ""), request.data.get("return_period", ""), request.user)
            return Response(GSTImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)
        except DuplicateImportError as exc:
            return Response({"code": "DUPLICATE_IMPORT", "title": "⚠️ File Already Imported",
                             "detail": str(exc), "message": str(exc),
                             "duplicate_batch_id": exc.batch.id}, status=409)
        except ValueError as exc: return Response({"detail": str(exc)}, status=400)

class BatchListView(APIView):
    def get(self, request):
        return Response(GSTImportBatchListSerializer(GSTImportBatch.objects.order_by("-created_at")[:100], many=True).data)

class GSTLookupView(APIView):
    def get(self, request, gstin):
        gstin = normalize_gstin(gstin)
        if not valid_gstin(gstin):
            return Response({"success": False, "code": "INVALID_GSTIN", "message": "Enter a valid 15-character GSTIN."}, status=400)
        try:
            force_refresh = str(request.query_params.get("force_refresh", "")).lower() in {"1", "true", "yes"}
            source, data = GSTLookupService.lookup_cached(gstin, force_refresh=force_refresh)
            complete = data.get("party_data_status") == "Complete"
            stored_status = data.get("lookup_status")
            party = GSTParty.objects.filter(gstin=gstin).first()
            eligibility = party_eligibility(party)
            sandbox = str(GSTLookupService.status().get("provider") or "") == "sandbox"
            row_status = ("Existing" if source == "existing" else "Fetched" if sandbox and data.get("gstin") else
                          "Ready with Warning" if eligibility["tally_ready"] and eligibility.get("warning") else
                          "Fetched via Fallback" if stored_status == "Fetched via Fallback" and complete else "Fetched" if complete else "Incomplete")
            normalized = _normalized_lookup_data(data)
            return Response({"success": True, "gstin": gstin, "source": source,
                             "status": row_status, "lookup_status": row_status,
                             "party_data_status": data.get("party_data_status"),
                             "gst_status": normalized["gst_status"], "tally_ready": eligibility["tally_ready"],
                             "warning": eligibility.get("warning", ""), "warning_reason": "Address unavailable" if sandbox and not normalized["principal_address"] else "",
                             "reason": eligibility.get("reason", ""),
                             "data": normalized})
        except SandboxOTPRequired:
            return Response({"success": False, "code": "OTP_REQUIRED", "message": "GST taxpayer OTP session is required."}, status=401)
        except GSTLookupConfigurationError as exc:
            return Response({"success": False, "code": "GSTIN_LOOKUP_NOT_AVAILABLE", "message": "GSTIN lookup is not available for the configured provider."}, status=503)
        except GSTLookupNotFoundError:
            return Response({"success": False, "code": "GSTIN_NOT_FOUND", "message": "GSTIN was not found."}, status=404)
        except GSTLookupAuthenticationError:
            return Response({"success": False, "code": "SANDBOX_AUTH_FAILED", "message": "Sandbox application authentication failed."}, status=502)
        except GSTLookupRateLimitError:
            return Response({"success": False, "code": "PROVIDER_RATE_LIMITED", "message": "GST lookup limit was reached. Please try again later."}, status=429)
        except GSTLookupTimeoutError:
            return Response({"success": False, "code": "PROVIDER_TIMEOUT", "message": "GST lookup provider timed out."}, status=504)
        except GSTLookupProviderError:
            return Response({"success": False, "code": "GSTIN_LOOKUP_FAILED", "message": "Sandbox GSTIN lookup failed."}, status=502)

def _normalized_lookup_data(data):
    return {
        "gstin": data.get("gstin") or "", "trade_name": data.get("trade_name") or "",
        "legal_name": data.get("legal_name") or "",
        "principal_address": data.get("principal_address") or "",
        "gst_status": data.get("gst_status") or data.get("status") or "",
        "taxpayer_type": data.get("taxpayer_type") or "",
        "registration_date": data.get("registration_date") or "",
        "cancellation_date": data.get("cancellation_date") or "",
        "constitution_of_business": data.get("constitution_of_business") or "",
        "state": data.get("state") or "", "state_code": data.get("state_code") or "",
        "pincode": data.get("pincode") or "",
        "central_jurisdiction": data.get("central_jurisdiction") or data.get("centre_jurisdiction") or "",
        "state_jurisdiction": data.get("state_jurisdiction") or "",
    }

class GSTLookupBulkView(APIView):
    def post(self, request):
        supplied = request.data.get("gstins", [])
        if not isinstance(supplied, list):
            return Response({"detail": "gstins must be a list."}, status=400)
        gstins = list(dict.fromkeys(normalize_gstin(value) for value in supplied))
        force_refresh = bool(request.data.get("force_refresh"))
        rows = []
        counts = {name: 0 for name in ("Existing", "Fetched", "Fetched via Fallback", "Incomplete",
                                       "Not Found", "Invalid", "Rate Limited", "Failed", "Sandbox Lookup Failed", "OTP Required")}
        for gstin in gstins:
            if not valid_gstin(gstin):
                lookup_status, party = "Invalid", None
            else:
                try:
                    source, data = GSTLookupService.lookup_cached(gstin, force_refresh=force_refresh)
                    complete = data.get("party_data_status") == "Complete"
                    lookup_status = ("Existing" if source == "existing" else
                                     "Fetched via Fallback" if data.get("lookup_status") == "Fetched via Fallback" and complete else
                                     "Fetched" if complete else "Rate Limited" if data.get("lookup_status") == "Rate Limited" else "Incomplete")
                    party = GSTParty.objects.filter(gstin=gstin).first()
                except SandboxOTPRequired:
                    lookup_status, party = "OTP Required", None
                except GSTLookupNotFoundError:
                    lookup_status, party = ("Sandbox Lookup Failed" if str(GSTLookupService.status().get("provider")) == "sandbox" else "Not Found"), None
                except GSTLookupRateLimitError:
                    lookup_status, party = "Rate Limited", None
                except (GSTLookupTimeoutError, GSTLookupProviderError, GSTLookupAuthenticationError,
                        GSTLookupConfigurationError):
                    lookup_status, party = ("Sandbox Lookup Failed" if str(GSTLookupService.status().get("provider")) == "sandbox" else "Failed"), None
                except Exception:
                    lookup_status, party = "Failed", None
            counts[lookup_status] += 1
            data = GSTLookupService._party_data(party) if party else {"gstin": gstin}
            normalized = _normalized_lookup_data(data)
            eligibility = party_eligibility(party) if party else {"tally_ready": False, "reason": "Party name unavailable"}
            if str(GSTLookupService.status().get("provider")) != "sandbox" and eligibility["tally_ready"] and eligibility.get("warning"): lookup_status = "Ready with Warning"
            rows.append({"success": lookup_status not in {"Invalid", "Not Found", "Failed", "Sandbox Lookup Failed", "OTP Required"},
                         "gstin": gstin, "lookup_status": lookup_status,
                         "gst_status": normalized["gst_status"],
                         **eligibility,
                         "source": party.lookup_source if party else "", "data": normalized})
        response_counts = {key.lower().replace(" ", "_"): value for key, value in counts.items()}
        ready_count = sum(row["tally_ready"] for row in rows)
        return Response({"total": len(gstins), **response_counts, "pending": 0,
                         "tally_ready_count": ready_count,
                         "total_accounted_for": sum(counts.values()),
                         "all_tally_ready": all(row["tally_ready"] for row in rows), "results": rows})

class GSTLookupStatusView(APIView):
    def get(self, request):
        return Response(GSTLookupService.status())

def _sandbox_batch(raw_batch_id):
    if raw_batch_id in (None, ""):
        return None, Response({"code": "BATCH_ID_REQUIRED", "message": "batch_id is required."}, status=400)
    text = str(raw_batch_id).strip()
    if not text.isdigit():
        return None, Response({"code": "INVALID_BATCH_ID", "message": "batch_id must be a valid integer."}, status=400)
    batch = GSTImportBatch.objects.filter(pk=int(text)).first()
    if not batch:
        return None, Response({"code": "BATCH_NOT_FOUND", "message": "Batch not found."}, status=404)
    return batch, None

class SandboxStatusView(APIView):
    def get(self, request):
        provider = SandboxGSTProvider.from_settings()
        batch, error = _sandbox_batch(request.query_params.get("batch_id"))
        if error is not None: return error
        company_gstin = normalize_gstin(batch.company_gstin)
        result = provider.safe_status(company_gstin)
        if not valid_gstin(company_gstin): result.update(code="COMPANY_GSTIN_REQUIRED", message="The current batch company GSTIN is unavailable.")
        elif result["session_required"]:
            result.update(code="SANDBOX_SESSION_REQUIRED", message="GST Sandbox session required")
        return Response(result)

class SandboxAuthenticateView(APIView):
    def post(self, request):
        provider = SandboxGSTProvider.from_settings()
        issues = provider.configuration_issues()
        if issues:
            return Response({"authenticated": False, "authentication_attempted": False,
                             "code": "SANDBOX_NOT_CONFIGURED",
                             "message": "Sandbox GST authentication is not configured.",
                             "missing": issues}, status=503)
        batch, error = _sandbox_batch(request.data.get("batch_id"))
        if error is not None: return error
        company_gstin = normalize_gstin(batch.company_gstin)
        if not valid_gstin(company_gstin):
            return Response({"authenticated": False, "authentication_attempted": False,
                             "code": "COMPANY_GSTIN_REQUIRED",
                             "message": "Current batch company GSTIN is required."}, status=400)
        try:
            provider.authenticate(force=True)
            return Response({"provider_configured": True, "authenticated": True,
                             "authentication_attempted": True,
                             "authentication_http_status": provider.last_http_status,
                             "session_active": False, "session_required": True,
                             "session_expired": False, "otp_required": True,
                             "lookup_ready": False, "lookup_failed": False,
                             "code": "OTP_REQUIRED", "message": "OTP required"})
        except SandboxAuthenticationFailure as exc:
            return Response({"authenticated": False, "authentication_attempted": True,
                             "authentication_http_status": exc.diagnostics.get("authenticate_http_status"),
                             "session_active": False, "session_required": True,
                             "otp_required": False, "lookup_ready": False, "lookup_failed": True,
                             "code": exc.code, "message": exc.safe_message}, status=502)
        except GSTLookupAuthenticationError:
            return Response({"authenticated": False, "authentication_attempted": True,
                             "authentication_http_status": provider.last_http_status,
                             "code": "SANDBOX_AUTH_FAILED", "message": "Sandbox authentication failed."}, status=502)
        except GSTLookupTimeoutError:
            return Response({"authenticated": False, "authentication_attempted": True,
                             "authentication_http_status": provider.last_http_status,
                             "code": "SANDBOX_AUTH_TIMEOUT", "message": "Sandbox authentication timed out."}, status=504)
        except GSTLookupProviderError:
            return Response({"authenticated": False, "authentication_attempted": True,
                             "authentication_http_status": provider.last_http_status,
                             "code": "SANDBOX_AUTH_FAILED", "message": "Sandbox authentication failed."}, status=502)

class SandboxRequestOTPView(APIView):
    def post(self, request):
        provider = SandboxGSTProvider.from_settings()
        issues = provider.configuration_issues()
        if issues:
            return Response({"otp_required": False, "code": "SANDBOX_NOT_CONFIGURED",
                             "message": "Sandbox GST authentication is not configured.", "missing": issues}, status=503)
        batch, error = _sandbox_batch(request.data.get("batch_id"))
        if error is not None: return error
        company_gstin = normalize_gstin(batch.company_gstin)
        username = str(request.data.get("username") or "").strip()
        if not batch or not valid_gstin(company_gstin):
            return Response({"otp_required": False, "code": "COMPANY_GSTIN_REQUIRED", "message": "Current batch company GSTIN is required."}, status=400)
        if not username:
            return Response({"otp_required": True, "code": "GST_USERNAME_REQUIRED", "message": "Enter the GST portal username."}, status=400)
        try: return Response(provider.request_otp(username, company_gstin))
        except SandboxAuthenticationFailure as exc:
            return Response({"otp_required": False, "code": exc.code,
                             "message": exc.safe_message, **exc.diagnostics}, status=502)
        except SandboxOTPRequestFailure as exc:
            return Response({"otp_required": True, "code": exc.code,
                             "message": exc.safe_message, **exc.diagnostics}, status=502)
        except GSTLookupAuthenticationError:
            return Response({"otp_required": False, "code": "SANDBOX_AUTH_FAILED", "message": "Sandbox authentication failed."}, status=502)
        except GSTLookupTimeoutError:
            return Response({"otp_required": False, "code": "SANDBOX_TIMEOUT", "message": "Sandbox request timed out."}, status=504)
        except GSTLookupProviderError:
            return Response({"otp_required": False, "code": "OTP_REQUEST_FAILED", "message": "Unable to request GST taxpayer OTP."}, status=502)

class SandboxVerifyOTPView(APIView):
    def post(self, request):
        provider = SandboxGSTProvider.from_settings()
        issues = provider.configuration_issues()
        if issues:
            return Response({"verified": False, "session_active": False, "code": "SANDBOX_NOT_CONFIGURED"}, status=503)
        batch, error = _sandbox_batch(request.data.get("batch_id"))
        if error is not None: return error
        company_gstin = normalize_gstin(batch.company_gstin)
        username = str(request.data.get("username") or "").strip()
        if not batch or not valid_gstin(company_gstin):
            return Response({"verified": False, "session_active": False, "code": "COMPANY_GSTIN_REQUIRED"}, status=400)
        if not username:
            return Response({"verified": False, "session_active": False, "code": "USERNAME_REQUIRED"}, status=400)
        try:
            result = provider.verify_otp(request.data.get("otp"), username, company_gstin)
            if result.get("verified") and result.get("session_active"):
                result.update(provider_configured=True, session_required=False,
                              session_expired=False, otp_required=False,
                              lookup_ready=True, lookup_failed=False)
            return Response(result)
        except GSTLookupAuthenticationError:
            return Response({"verified": False, "session_active": False, "code": "OTP_INVALID", "message": "OTP verification failed."}, status=400)
        except GSTLookupTimeoutError:
            return Response({"verified": False, "session_active": False, "code": "SANDBOX_TIMEOUT", "message": "Sandbox request timed out."}, status=504)
        except GSTLookupProviderError:
            return Response({"verified": False, "session_active": False, "code": "OTP_INVALID", "message": "OTP verification failed."}, status=400)

class BatchDetailView(APIView):
    def get(self, request, pk):
        return Response(GSTImportBatchSerializer(get_object_or_404(GSTImportBatch, pk=pk)).data)

class BatchCompanyView(APIView):
    def get(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        return Response({"batch_id": batch.id, "company_gstin": batch.company_gstin,
                         "company_gstin_candidates": batch.company_gstin_candidates, "company": batch.company_details,
                         "status": batch.company_resolution_status, "error": batch.company_resolution_error})
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        try: company = resolve_batch_company(batch, request.data.get("company_gstin", ""), bool(request.data.get("force_refresh")))
        except ValueError as exc:
            return Response({"detail": str(exc), "company_gstin_candidates": batch.company_gstin_candidates}, status=400)
        return Response({"batch_id": batch.id, "company_gstin": batch.company_gstin,
                         "company_gstin_candidates": batch.company_gstin_candidates, "company": company,
                         "status": batch.company_resolution_status, "error": batch.company_resolution_error})

def _batch_gstins(batch):
    company_gstin = normalize_gstin(batch.company_gstin)
    return sorted({gstin for value in batch.invoices.values_list("customer_gstin", flat=True)
                   if (gstin := normalize_gstin(value)) and gstin != company_gstin and valid_gstin(gstin)})

def _batch_party_gstins(batch):
    company_gstin = normalize_gstin(batch.company_gstin)
    return sorted({gstin for value in batch.invoices.values_list("customer_gstin", flat=True)
                   if (gstin := normalize_gstin(value)) and gstin != company_gstin})

def _batch_gstin_diagnostics(batch):
    normalized = [normalize_gstin(value) for value in batch.invoices.values_list("customer_gstin", flat=True)]
    company_gstin = normalize_gstin(batch.company_gstin)
    customer_values = [value for value in normalized if value != company_gstin]
    valid = sorted({value for value in customer_values if valid_gstin(value)})
    invalid = sorted({value for value in customer_values if value and not valid_gstin(value)})
    return {"invoice_row_count": len(normalized), "raw_customer_gstin_count": sum(bool(value) for value in customer_values),
            "unique_customer_gstin_count": len(valid), "unique_customer_gstins": valid,
            "blank_customer_gstin_count": sum(not value for value in normalized),
            "invalid_customer_gstin_count": sum(bool(value) and not valid_gstin(value) for value in customer_values),
            "invalid_customer_gstins": invalid,
            "company_gstin_occurrences_excluded": sum(value == company_gstin for value in normalized if company_gstin)}

def _has_blank_gstin(batch):
    return batch.invoices.filter(customer_gstin="").exists()

def _apply_batch_eligibility(batch, rows):
    sandbox = str(GSTLookupService.status().get("provider") or "") == "sandbox"
    source_parties = batch.source_parties or {}
    for row in rows:
        if row["gstin"] == "-": continue
        party = GSTParty.objects.filter(gstin=row["gstin"]).first()
        source_name = resolve_party_ledger_name(source=source_parties.get(row["gstin"]) or {}, gstin="")
        eligibility = party_eligibility(party, source_name, row["gstin"])
        row.update({key: value for key, value in eligibility.items()
                    if key not in {"name", "name_source", "party_name"}})
        if row.get("name_source") == "GSTIN" and eligibility.get("party_name") and eligibility.get("party_name") != row["gstin"]:
            row["name"] = eligibility["party_name"]
            row["party_name"] = eligibility["party_name"]
            row["name_source"] = "SOURCE"
        if row.get("warning_reason") == "Sandbox party enrichment unavailable; source/GSTIN fallback used.":
            row["warning"] = row["warning_reason"]
            row["warning_code"] = "SANDBOX_PARTY_ENRICHMENT_UNAVAILABLE"
        controlled_failure = row["status"] in {"Invalid", "Incomplete", "Not Found", "Rate Limited", "Failed", "Sandbox Lookup Failed",
                                               "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}
        if not controlled_failure and not sandbox and eligibility["tally_ready"] and eligibility.get("name_source") == "GSTIN":
            row["status"] = "GSTIN Fallback"
            row["reason"] = "Lookup incomplete. GSTIN will be used as the Tally ledger name."
        elif not controlled_failure and not sandbox and eligibility["tally_ready"] and eligibility.get("warning"):
            row["status"] = "Ready with Warning"
            row["reason"] = "Principal Place of Business could not be fetched. Party can still be used for Tally import."
        elif not eligibility["tally_ready"] and row["status"] not in {"Invalid", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}:
            row["status"], row["reason"] = ("Sandbox Lookup Failed" if sandbox else "Incomplete"), eligibility["reason"]
        if sandbox and row["status"] in {"Fetched", "Existing"} and not row.get("principal_place_of_business"):
            row["warning_reason"] = "Address unavailable"
    return rows

class BatchPartiesView(APIView):
    def get(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        rows = []
        for gstin in _batch_party_gstins(batch):
            party = GSTParty.objects.filter(gstin=gstin).first()
            if not valid_gstin(gstin):
                rows.append(result_row(gstin, "Invalid"))
                continue
            if party_is_fresh(party): rows.append(result_row(gstin, "Completed Manually" if party.lookup_status == "Completed Manually" else "Existing", party))
            elif party and party.lookup_status in {"Rate Limited", "Failed", "Not Found", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}: rows.append(result_row(gstin, party.lookup_status, party))
            elif party and party.party_data_status == "Incomplete": rows.append(result_row(gstin, "Incomplete", party))
            else: rows.append(result_row(gstin, "Pending", party))
        if _has_blank_gstin(batch): rows.append(result_row("-", "Not Applicable"))
        _apply_batch_eligibility(batch, rows)
        configured = lookup_is_configured()
        ready = sum(bool(row.get("tally_ready")) for row in rows)
        warnings = sum(row.get("status") == "Ready with Warning" for row in rows)
        diagnostics = _batch_gstin_diagnostics(batch)
        returned_gstins = {row["gstin"] for row in rows if row["gstin"] != "-"}
        missing_gstins = sorted(set(diagnostics["unique_customer_gstins"]) - returned_gstins)
        return Response({"batch_id": batch.id, "lookup_configured": configured, "parties": rows,
                         **diagnostics, "missing_customer_gstins": missing_gstins,
                         "missing_customer_gstin_count": len(missing_gstins),
                         "total": len(_batch_party_gstins(batch)), "tally_ready_count": ready,
                         "ready_with_warning": warnings, "incomplete": len(_batch_party_gstins(batch)) - ready,
                         "eligibility_map": {row["gstin"]: {k: row[k] for k in ("tally_ready", "reason", "warning") if k in row} for row in rows if row["gstin"] != "-"},
                         "configuration_error": "" if configured else lookup_configuration_message()})

    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        configured = lookup_is_configured()
        rows = []
        input_gstins = _batch_party_gstins(batch)
        requested_gstins = request.data.get("gstins", [])
        if isinstance(requested_gstins, list) and requested_gstins:
            requested = {normalize_gstin(value) for value in requested_gstins}
            input_gstins = [gstin for gstin in input_gstins if gstin in requested]
        retry_incomplete_only = bool(request.data.get("retry_incomplete"))
        temporary_failures = {"Provider timeout", "Malformed provider response", "Provider request failed", "Processing failed"}
        retry_candidates = api_calls_made = completed_after_retry = skipped_complete = 0
        for gstin in input_gstins:
            try:
                party = GSTParty.objects.filter(gstin=gstin).first()
                if retry_incomplete_only and party_is_complete(party):
                    party.lookup_status = "Existing"
                    party.lookup_error = ""
                    party.party_data_status = "Complete"
                    party.retry_not_before = None
                    party.save(update_fields=["lookup_status", "lookup_error", "party_data_status", "retry_not_before", "updated_at"])
                    rows.append(result_row(gstin, "Existing", party))
                    skipped_complete += 1
                    continue
                retryable = bool(party and (party.lookup_status in {"Incomplete", "Rate Limited", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"} or
                                 (party.party_data_status == "Incomplete" and party.lookup_status in
                                  {"", "Fetched", "Fetched via Fallback"}) or
                                 (party.lookup_status == "Failed" and party.lookup_error in
                                  temporary_failures)))
                if retry_incomplete_only and not retryable:
                    if not valid_gstin(gstin): fetch_status = "Invalid"
                    elif party_is_fresh(party):
                        fetch_status = "Completed Manually" if party.lookup_status == "Completed Manually" else "Existing"
                        skipped_complete += 1
                    else: fetch_status = party.lookup_status if party and party.lookup_status in {"Not Found", "Rate Limited", "Failed"} else "Failed"
                elif retry_incomplete_only and not retry_allowed(party):
                    retry_candidates += 1
                    fetch_status = "Rate Limited"
                    party.lookup_error = f"Retry allowed after {party.retry_not_before.isoformat()}"
                else:
                    if retry_incomplete_only: retry_candidates += 1
                    fetch_status, party = process_gstin(gstin, batch=batch, force=True)
                    if retry_incomplete_only:
                        api_calls_made += getattr(party, "_lookup_diagnostics", {}).get("api_calls", 0) if party else 0
                        if fetch_status in {"Existing", "Fetched", "Fetched via Fallback", "Completed Manually"}: completed_after_retry += 1
                rows.append(result_row(gstin, fetch_status, party))
            except Exception:
                rows.append(result_row(gstin, "Failed", reason="Processing failed"))
        for row in rows:
            if row["status"] == "Pending":
                row["status"] = "Failed"
                row["reason"] = row["reason"] or "Processing incomplete"
        processed = {row["gstin"] for row in rows}
        for gstin in input_gstins:
            if gstin not in processed:
                rows.append(result_row(gstin, "Failed", reason="Processing incomplete"))
        processed = {row["gstin"] for row in rows}
        if _has_blank_gstin(batch): rows.append(result_row("-", "Not Applicable"))
        _apply_batch_eligibility(batch, rows)
        counts = {"fetched": 0, "fetched_via_fallback": 0, "existing": 0, "completed_manually": 0, "incomplete": 0, "not_found": 0, "invalid": 0,
                  "rate_limited": 0, "failed": 0, "pending": 0, "sandbox_lookup_failed": 0, "sandbox_not_configured": 0, "otp_required": 0,
                  "sandbox_session_required": 0, "sandbox_session_failed": 0}
        count_keys = {"Fetched": "fetched", "Fetched via Fallback": "fetched_via_fallback",
                      "Existing": "existing", "Completed Manually": "completed_manually",
                      "Incomplete": "incomplete", "Not Found": "not_found",
                      "Invalid": "invalid", "Rate Limited": "rate_limited", "Failed": "failed",
                      "Pending": "pending", "Sandbox Lookup Failed": "sandbox_lookup_failed", "Sandbox Not Configured": "sandbox_not_configured", "OTP Required": "otp_required",
                      "Sandbox Session Required": "sandbox_session_required", "Sandbox Session Failed": "sandbox_session_failed"}
        for row in rows:
            key = count_keys.get(row["status"])
            if key: counts[key] += 1
        retry_incomplete_count = sum(
            row["status"] in {"Incomplete", "Rate Limited", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"} or
            (row["status"] == "Failed" and row.get("reason") in temporary_failures)
            for row in rows
        )
        needs_provider = any(row["status"] == "Pending" for row in rows)
        fallback_configured = GSTLookupService.status()["fallback_configured"]
        sent_to_fallback = sum(
            row["status"] in {"Fetched via Fallback", "Incomplete"} for row in rows
        ) if fallback_configured else 0
        ready_count = sum(bool(row.get("tally_ready")) for row in rows)
        warning_count = sum(row.get("status") == "Ready with Warning" for row in rows)
        tally_ready = ready_count > 0
        jamku_429_count = sum(row.get("diagnostics", {}).get("jamku_429_count", 0) for row in rows)
        gstinapi_429_count = sum(row.get("diagnostics", {}).get("gstinapi_429_count", 0) for row in rows)
        fallback_after_429 = sum(bool(row.get("diagnostics", {}).get("fallback_attempted_after_429")) for row in rows)
        diagnostics = _batch_gstin_diagnostics(batch)
        returned_gstins = {row["gstin"] for row in rows if row["gstin"] != "-"}
        missing_gstins = sorted(set(diagnostics["unique_customer_gstins"]) - returned_gstins)
        return Response({"batch_id": batch.id, "total": len(input_gstins), **counts,
                         **diagnostics, "missing_customer_gstins": missing_gstins,
                         "missing_customer_gstin_count": len(missing_gstins),
                         "retry_candidates": retry_candidates, "api_calls_made": api_calls_made,
                         "completed_after_retry": completed_after_retry,
                         "still_incomplete_after_retry": counts["incomplete"] + counts["failed"] + counts["sandbox_lookup_failed"] + counts["sandbox_not_configured"] + counts["otp_required"] + counts["sandbox_session_required"] + counts["sandbox_session_failed"],
                         "still_rate_limited_after_retry": counts["rate_limited"],
                         "skipped_because_already_complete": skipped_complete,
                         "retry_incomplete_count": retry_incomplete_count,
                         "processed": len(processed & set(input_gstins)),
                         "missing_unprocessed": len(set(input_gstins) - processed),
                         "all_accounted_for": set(input_gstins).issubset(processed),
                         "complete_from_cache": counts["existing"] + counts["completed_manually"],
                         "complete_from_sandbox": counts["fetched"], "complete_from_jamku": 0,
                         "sent_to_fallback": sent_to_fallback,
                         "completed_by_fallback": counts["fetched_via_fallback"],
                         "still_incomplete": counts["incomplete"],
                         "tally_ready": tally_ready,
                         "tally_ready_count": ready_count, "ready_with_warning": warning_count,
                         "eligibility_map": {row["gstin"]: {k: row[k] for k in ("tally_ready", "reason", "warning") if k in row} for row in rows if row["gstin"] != "-"},
                         "jamku_429_count": jamku_429_count,
                         "gstinapi_429_count": gstinapi_429_count,
                         "current_concurrency": 1,
                         "retry_after_present": any(row.get("diagnostics", {}).get("retry_after_present") for row in rows),
                         "fallback_attempted_after_429": fallback_after_429,
                         "final_unresolved_rate_limited": counts["rate_limited"],
                         "lookup_configured": configured, "parties": rows,
                         "configuration_error": lookup_configuration_message() if not configured and needs_provider else "",
                         "message": "GSTIN lookup source is not configured." if not configured and needs_provider else "Party details lookup completed."})

class BatchPartyDetailView(APIView):
    def put(self, request, pk, gstin):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        gstin = normalize_gstin(gstin)
        if gstin not in _batch_party_gstins(batch):
            return Response({"detail": "GSTIN does not belong to this batch."}, status=404)
        if not valid_gstin(gstin):
            return Response({"detail": "Enter a valid GSTIN."}, status=400)
        existing = GSTParty.objects.filter(gstin=gstin).first()
        def supplied(name, old=""):
            value = str(request.data.get(name, old) or "").strip()
            return "" if value.lower() in {"-", "none", "null"} else value
        trade_name = supplied("trade_name", existing.trade_name if existing else "")
        legal_name = supplied("legal_name", existing.legal_name if existing else "")
        address = supplied("principal_place_of_business", existing.principal_place_of_business if existing else "")
        state = supplied("state", existing.state_name if existing else "")
        pincode = supplied("pincode", existing.pincode if existing else "")
        if not (trade_name or legal_name):
            return Response({"detail": "Trade Name or Legal Name is required."}, status=400)
        if pincode and (len(pincode) != 6 or not pincode.isdigit()):
            return Response({"detail": "Pincode must contain 6 digits."}, status=400)
        party, _ = GSTParty.objects.update_or_create(gstin=gstin, defaults={
            "trade_name": trade_name, "legal_name": legal_name,
            "principal_place_of_business": address, "state_name": state, "pincode": pincode,
            "lookup_source": "MANUAL", "lookup_status": "Completed Manually",
            "party_data_status": "Complete", "lookup_error": "", "last_fetched_at": timezone.now(),
        })
        return Response({"success": True, "party": result_row(gstin, "Completed Manually", party)})

class TallyMastersView(APIView):
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        selected = str(request.data.get("tally_company_name") or "").strip()
        if selected:
            details = dict(batch.company_details or {})
            details["selected_tally_company"] = selected
            batch.company_details = details
            batch.save(update_fields=["company_details"])
        return Response(prepare_master_results(batch))

class TallyConnectionView(APIView):
    def get(self, request): return Response(step3_connection_check())

class TallyLicenseView(APIView):
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        return Response(verify_batch_license(batch))

class TallyDiagnosticsView(APIView):
    def get(self, request): return Response(diagnostics())

class TallyVoucherPreviewView(APIView):
    def get(self, request, pk): return Response(voucher_preview(get_object_or_404(GSTImportBatch, pk=pk)))

class TallyVoucherCorrectionView(APIView):
    """Step 5 Review/Fix: set only the invoice-level Round Off and revalidate."""
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        party_gstin = normalize_gstin(request.data.get("party_gstin", ""))
        invoice_number = str(request.data.get("invoice_number", "")).strip()
        mode = str(request.data.get("mode", "")).strip().lower()
        if not (party_gstin and invoice_number and request.data.get("invoice_date")):
            return Response({"detail": "party_gstin, invoice_number and invoice_date are required."}, status=400)
        try:
            invoice_date = date.fromisoformat(str(request.data.get("invoice_date")))
        except ValueError:
            return Response({"detail": "invoice_date must be in YYYY-MM-DD format."}, status=400)
        try:
            if mode == "suggested":
                result = correct_invoice_value(batch, party_gstin, invoice_number, invoice_date, use_suggested=True)
            elif mode == "manual":
                try:
                    invoice_value = Decimal(str(request.data.get("round_off", request.data.get("invoice_value"))))
                except (InvalidOperation, TypeError):
                    return Response({"detail": "round_off must be a valid number."}, status=400)
                result = correct_invoice_value(batch, party_gstin, invoice_number, invoice_date, invoice_value=invoice_value)
            elif mode == "save_correction":
                try:
                    corrected_value = Decimal(str(request.data.get("value")))
                except (InvalidOperation, TypeError):
                    return Response({"detail": "value must be a valid number."}, status=400)
                result = save_voucher_correction(
                    batch, party_gstin, invoice_number, invoice_date,
                    str(request.data.get("field", "")).strip().lower(), corrected_value,
                    str(request.data.get("correction_source", "manual")).strip().lower(),
                )
            else:
                return Response({"detail": "mode must be 'suggested', 'manual', or 'save_correction'."}, status=400)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=404)
        return Response(result)

class TallyImportView(APIView):
    def post(self, request, pk):
        import_lock = TallyImportLock(pk)
        if not import_lock.acquire():
            return Response({"status": "failed", "code": "TALLY_IMPORT_IN_PROGRESS",
                             "detail": "A Tally import for this batch is already running. Wait for it to finish before retrying."}, status=409)
        try:
            return Response(import_tally_batch(get_object_or_404(GSTImportBatch, pk=pk)))
        finally:
            import_lock.release()
