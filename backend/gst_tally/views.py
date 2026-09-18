from datetime import date
import hashlib
import secrets
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from .auth_views import public_user, tokens_for
from .models import CompanyDetails, GSTImportBatch, GSTParty, LicensedDevice, ProductLicense, TallyCompanyMapping, UserProfile, LocalTallyAgent, LocalTallyJob
from .serializers import GSTImportBatchListSerializer, GSTImportBatchSerializer
from .services.import_service import import_file
from .services.company import resolve_batch_company
from .services.company_verification import verify_and_store_company
from .services.source_preview import preview_batch, preview_file
from .services.preview_pdf import build_preview_pdf
from .services.system_info import collect_system_configuration
from .services.party_lookup import (lookup_configuration_message, lookup_is_configured,
                                    normalize_gstin, party_eligibility, party_is_complete, party_is_fresh, process_gstin, result_row,
                                    retry_allowed, valid_gstin)
from .services.party_ledger_name import resolve_party_ledger_name
from .tally.connection import connection_status, diagnostics, step3_connection_check
from .tally.odbc import odbc_company_status
from .services.product_license import pre_import_security_check
from .services.product_license import verify_license_snapshot
from .services.product_license import (
    product_license_activation_key,
    product_license_days_remaining,
    product_license_status,
)
from .tally.service import (correct_invoice_value, save_voucher_correction, import_batch as import_tally_batch, prepare_master_results,
                            voucher_preview)
from .tally.import_job import get_active_job_for_batch, get_job, request_pause, resume_job, run_job_in_background, serialize_job, start_import_job
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
        try:
            return Response(preview_file(
                uploaded,
                request.data.get("return_type", ""),
                request.data.get("sheet_name"),
                page=request.data.get("page", 1),
                page_size=request.data.get("page_size", 50),
            ))
        except ValueError as exc: return Response({"detail": str(exc)}, status=400)

class ImportView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    def post(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded: return Response({"detail": "File is required"}, status=400)
        try:
            batch = import_file(
                uploaded,
                request.data.get("return_type", ""),
                request.data.get("return_period", ""),
                request.user,
                selected_tally_company=request.data.get("selected_tally_company", ""),
                stable_tally_company_id=request.data.get("stable_tally_company_id", ""),
            )
            return Response(GSTImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)
        except ValueError as exc: return Response({"detail": str(exc)}, status=400)


def _agent_from_request(request):
    token = request.headers.get("X-Tally-Agent-Token", "")
    if not token:
        return None
    return LocalTallyAgent.objects.select_related("device__license").filter(
        token_hash=hashlib.sha256(token.encode()).hexdigest(), device__status=LicensedDevice.ACTIVE).first()


class LocalTallyAgentProvisionView(APIView):
    """User-authenticated one-time enrollment. The desktop installer stores
    the returned token in Windows Credential Manager, never in React."""
    def post(self, request):
        fingerprint = str(request.data.get("device_fingerprint", "")).strip()
        if not fingerprint:
            return Response({"detail": "device_fingerprint is required."}, status=400)
        device = LicensedDevice.objects.filter(device_fingerprint=fingerprint, license__customer=request.user,
                                              status=LicensedDevice.ACTIVE).select_related("license").first()
        if not device:
            return Response({"detail": "An active registered device is required."}, status=403)
        token = secrets.token_urlsafe(48)
        agent, _ = LocalTallyAgent.objects.update_or_create(device=device, defaults={
            "token_hash": hashlib.sha256(token.encode()).hexdigest()})
        return Response({"agent_id": agent.id, "agent_token": token})


class LocalTallyAgentHeartbeatView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        agent = _agent_from_request(request)
        if not agent:
            return Response({"detail": "Invalid agent token."}, status=401)
        license = agent.device.license
        serial = str(request.data.get("tally_serial", "")).strip()
        gstin = normalize_gstin(request.data.get("company_gstin", ""))
        verified = serial == license.licensed_tally_serial and gstin == license.licensed_gstin
        LocalTallyAgent.objects.filter(pk=agent.pk).update(
            detected_serial=serial, detected_company_gstin=gstin,
            detected_company_name=str(request.data.get("company_name", ""))[:255],
            tally_reachable=bool(request.data.get("tally_reachable")) and verified, last_seen_at=timezone.now())
        return Response({"status": "Company Verified" if verified else "Wrong Company Open",
                         "verified": verified, "tally_reachable": bool(request.data.get("tally_reachable"))})


class LocalTallyAgentStatusView(APIView):
    """Connection state for the signed-in customer's registered device.

    The browser never receives the agent credential and cannot select another
    customer's agent: both the license owner and active-device state are
    enforced by this lookup.
    """
    def get(self, request):
        fingerprint = str(request.headers.get("X-Device-Fingerprint", "") or request.query_params.get("device_fingerprint", "")).strip()
        devices = LicensedDevice.objects.filter(
            license__customer=request.user, status=LicensedDevice.ACTIVE
        )
        if fingerprint:
            devices = devices.filter(device_fingerprint=fingerprint)
        agent = (LocalTallyAgent.objects.select_related("device__license")
                 .filter(device__in=devices).order_by("-last_seen_at").first())
        if not agent:
            return Response({"status": "Agent Offline", "connected": False, "verified": False})
        online = bool(agent.last_seen_at and (timezone.now() - agent.last_seen_at).total_seconds() <= 90)
        verified = bool(online and agent.tally_reachable)
        if not online:
            label = "Agent Offline"
        elif not agent.detected_serial or not agent.detected_company_gstin:
            label = "Tally Not Running"
        elif not verified:
            label = "Wrong Company Open"
        else:
            label = "Ready to Import"
        return Response({
            "status": label, "connected": online, "verified": verified,
            "tally_reachable": bool(agent.tally_reachable),
            "company_name": agent.detected_company_name,
            "company_gstin": agent.detected_company_gstin,
            "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
        })


class LocalTallyAgentNextJobView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        agent = _agent_from_request(request)
        if not agent:
            return Response({"detail": "Invalid agent token."}, status=401)
        if not agent.tally_reachable:
            return Response({"detail": "Tally/company license is not verified."}, status=403)
        with transaction.atomic():
            job = (LocalTallyJob.objects.select_for_update(skip_locked=True)
                   .filter(agent=agent, status=LocalTallyJob.QUEUED).order_by("created_at").first())
            if not job:
                return Response(status=204)
            job.status, job.claimed_at = LocalTallyJob.SENDING, timezone.now()
            job.save(update_fields=["status", "claimed_at"])
        return Response({"job_id": str(job.job_id), "idempotency_key": job.idempotency_key, "payload": job.payload})


class LocalTallyAgentJobResultView(APIView):
    permission_classes = [AllowAny]
    def post(self, request, job_id):
        agent = _agent_from_request(request)
        if not agent:
            return Response({"detail": "Invalid agent token."}, status=401)
        job = get_object_or_404(LocalTallyJob, job_id=job_id, agent=agent)
        if job.status in (LocalTallyJob.SUCCESS, LocalTallyJob.FAILED):
            return Response({"accepted": True, "status": job.status})
        success = bool(request.data.get("success"))
        # The agent acknowledgement is retained; server-side registry mutation
        # is intentionally delegated to the existing verified Tally pipeline.
        job.status = LocalTallyJob.SUCCESS if success else LocalTallyJob.RETRYABLE
        job.acknowledgement = request.data.get("acknowledgement") or {}
        job.error_message = str(request.data.get("error", ""))
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "acknowledgement", "error_message", "completed_at"])
        return Response({"accepted": True, "status": job.status})

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
                             "session_active": True, "session_required": False,
                             "session_expired": False, "otp_required": False,
                             "lookup_ready": True, "lookup_failed": False,
                             "code": "SANDBOX_AUTHENTICATED", "message": "GST party lookup is ready."})
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

class BatchPreviewView(APIView):
    """Refresh-safe Step 2 recovery: rebuilds the same {columns, rows} preview
    shape SourcePreviewView returns for a fresh upload, from the invoices this
    batch already persisted -- so reloading the Invoice Preview page never has
    to (and, since the browser no longer has the original File object after a
    reload, cannot) re-parse the source file."""
    def get(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        try:
            return Response(preview_batch(batch, page=request.query_params.get("page", 1),
                                         page_size=request.query_params.get("page_size", 50),
                                         search=request.query_params.get("search", "")))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

class BatchPreviewPdfView(APIView):
    def get(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        response = HttpResponse(build_preview_pdf(batch), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="Invoice_Preview_Batch_{batch.id}.pdf"'
        return response

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

def _selected_party_gstin_field(return_type):
    normalized = str(return_type or "").upper().replace("-", "")
    if normalized in {"GSTR1"}:
        return "customer_gstin"
    if normalized in {"GSTR2A", "GSTR2B"}:
        return "supplier_gstin"
    return "customer_gstin"


def _source_line_candidates(source_line):
    values = []
    if isinstance(source_line, dict):
        for key, value in source_line.items():
            if value in (None, ""):
                continue
            values.append(value)
            if isinstance(value, (list, tuple, set)):
                values.extend(value)
    elif isinstance(source_line, list):
        values.extend(source_line)
    return values


def _extract_invoice_party_gstin(invoice, return_type):
    field_name = _selected_party_gstin_field(return_type)
    source = getattr(invoice, "source_line", {}) if hasattr(invoice, "source_line") else {}
    source_values = _source_line_candidates(source)
    aliases = {
        "customer_gstin": ["customer_gstin", "recipient_gstin", "ctin", "gstin", "seller_gstin", "buyer_gstin"],
        "supplier_gstin": ["supplier_gstin", "gstin_of_supplier", "gstin", "ctin", "customer_gstin", "recipient_gstin"],
    }
    for alias in aliases.get(field_name, aliases["customer_gstin"]):
        if isinstance(source, dict):
            literal = source.get(alias)
            if literal not in (None, ""):
                return normalize_gstin(literal)
            for key, value in source.items():
                normalized_key = str(key or "").strip().lower().replace(" ", "_").replace("-", "_")
                if normalized_key == alias:
                    return normalize_gstin(value)
        value = getattr(invoice, alias, None)
        if value not in (None, ""):
            return normalize_gstin(value)
    for value in source_values:
        gstin = normalize_gstin(value)
        if gstin and valid_gstin(gstin):
            return gstin
    return normalize_gstin(getattr(invoice, "customer_gstin", ""))


def _batch_gstins(batch):
    company_gstin = normalize_gstin(batch.company_gstin)
    return sorted({gstin for invoice in batch.invoices.all() if (gstin := _extract_invoice_party_gstin(invoice, batch.gst_return_type)) and gstin != company_gstin and valid_gstin(gstin)})


def _batch_party_gstins(batch):
    company_gstin = normalize_gstin(batch.company_gstin)
    return sorted({gstin for invoice in batch.invoices.all() if (gstin := _extract_invoice_party_gstin(invoice, batch.gst_return_type)) and gstin != company_gstin})


def _batch_gstin_diagnostics(batch):
    selected_field = _selected_party_gstin_field(batch.gst_return_type)
    company_gstin = normalize_gstin(batch.company_gstin)
    values = []
    for invoice in batch.invoices.all():
        gstin = _extract_invoice_party_gstin(invoice, batch.gst_return_type)
        if gstin != company_gstin:
            values.append(gstin)
    raw = [value for value in values if value]
    valid = sorted({value for value in raw if valid_gstin(value)})
    invalid = sorted({value for value in raw if value and not valid_gstin(value)})
    blank_count = sum(1 for invoice in batch.invoices.all() if not _extract_invoice_party_gstin(invoice, batch.gst_return_type))
    return {"return_type": str(batch.gst_return_type or "").upper(), "selected_party_gstin_field": selected_field,
            "invoice_row_count": batch.invoices.count(),
            "raw_party_gstin_count": len(raw), "unique_party_gstin_count": len(valid), "unique_party_gstins": valid,
            "blank_party_gstin_count": blank_count,
            "valid_party_gstin_count": sum(valid_gstin(value) for value in raw),
            "invalid_party_gstin_count": sum(bool(value) and not valid_gstin(value) for value in raw),
            "invalid_party_gstins": invalid,
            "company_gstin_occurrences_excluded": sum(1 for invoice in batch.invoices.all() if normalize_gstin(_extract_invoice_party_gstin(invoice, batch.gst_return_type)) == company_gstin and company_gstin),
            "raw_customer_gstin_count": len(raw), "unique_customer_gstin_count": len(valid),
            "blank_customer_gstin_count": blank_count, "invalid_customer_gstin_count": sum(bool(value) and not valid_gstin(value) for value in raw)}


def _has_blank_gstin(batch):
    return not _batch_party_gstins(batch)

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
        elif not eligibility["tally_ready"] and row["status"] not in {"Invalid", "Pending", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"}:
            row["status"], row["reason"] = ("Sandbox Lookup Failed" if sandbox else "Incomplete"), eligibility["reason"]
        if sandbox and row["status"] in {"Fetched", "Existing"} and not row.get("principal_place_of_business"):
            row["warning_reason"] = "Address unavailable"
    return rows

def _batch_sandbox_provider_status(rows):
    """Batch-level Sandbox account state, derived from whatever every row's
    own result_row()/sandbox_lookup already carries -- one account-level
    condition (e.g. quota exhaustion) applies to every GSTIN in the batch at
    once, so this is reported once here instead of only being visible by
    scanning all N rows for the same repeated code."""
    codes = {(row.get("sandbox_lookup") or {}).get("sandbox_error_code") for row in rows}
    codes.discard("")
    codes.discard(None)
    if "SANDBOX_QUOTA_EXHAUSTED" in codes:
        return "QUOTA_EXHAUSTED", "Sandbox API quota exhausted. Taxpayer enrichment is temporarily unavailable."
    if "SANDBOX_NOT_CONFIGURED" in codes:
        return "NOT_CONFIGURED", "Sandbox is not configured."
    if codes:
        message = ""
        for row in rows:
            lookup = row.get("sandbox_lookup") or {}
            message = lookup.get("provider_message") or lookup.get("sandbox_error_message") or ""
            if message:
                break
        return "ERROR", message or "Sandbox taxpayer lookup is currently unavailable."
    return "OK", ""


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
        _apply_batch_eligibility(batch, rows)
        configured = lookup_is_configured()
        ready = sum(bool(row.get("tally_ready")) for row in rows)
        warnings = sum(row.get("status") == "Ready with Warning" for row in rows)
        diagnostics = _batch_gstin_diagnostics(batch)
        returned_gstins = {row["gstin"] for row in rows if row["gstin"] != "-"}
        missing_gstins = sorted(set(diagnostics["unique_party_gstins"]) - returned_gstins)
        sandbox_provider_status, sandbox_provider_message = _batch_sandbox_provider_status(rows)
        return Response({"batch_id": batch.id, "lookup_configured": configured, "parties": rows,
                         **diagnostics, "missing_customer_gstins": missing_gstins,
                         "missing_customer_gstin_count": len(missing_gstins),
                         "total_parties": diagnostics["unique_party_gstin_count"],
                         "total": len(_batch_party_gstins(batch)), "tally_ready_count": ready,
                         "ready_with_warning": warnings, "incomplete": len(_batch_party_gstins(batch)) - ready,
                         "eligibility_map": {row["gstin"]: {k: row[k] for k in ("tally_ready", "reason", "warning") if k in row} for row in rows if row["gstin"] != "-"},
                         "configuration_error": "" if configured else lookup_configuration_message(),
                         "sandbox_provider_status": sandbox_provider_status, "sandbox_provider_message": sandbox_provider_message})

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
        def process_one(gstin, threaded=False):
            # Each worker owns its Django DB connection. Provider calls are
            # independent per GSTIN, so a single slow/failed lookup cannot
            # serialize or abort the rest of this batch.
            if threaded:
                close_old_connections()
            try:
                party = GSTParty.objects.filter(gstin=gstin).first()
                # A source/import fallback can look complete to Tally while
                # still having no real Sandbox taxpayer response. Explicit
                # retry must re-enrich those records as well; only a genuine
                # Sandbox result (or a manual completion) may be skipped.
                sandbox_fallback = (
                    str(GSTLookupService.status().get("provider") or "").lower() == "sandbox"
                    and party
                    and str(party.lookup_source or "").lower() not in {"sandbox", "completed manually"}
                )
                if retry_incomplete_only and party_is_complete(party) and not sandbox_fallback:
                    party.lookup_status = "Existing"
                    party.lookup_error = ""
                    party.party_data_status = "Complete"
                    party.retry_not_before = None
                    party.save(update_fields=["lookup_status", "lookup_error", "party_data_status", "retry_not_before", "updated_at"])
                    return gstin, "Existing", party, False
                retryable = bool(party and (party.lookup_status in {"Incomplete", "Rate Limited", "Sandbox Lookup Failed", "Sandbox Not Configured", "OTP Required", "Sandbox Session Required", "Sandbox Session Failed"} or
                                 (party.party_data_status == "Incomplete" and party.lookup_status in
                                  {"", "Fetched", "Fetched via Fallback"}) or
                                 (party.lookup_status == "Failed" and party.lookup_error in
                                  temporary_failures)))
                if retry_incomplete_only:
                    # An explicit retry is the recovery boundary for the
                    # current batch. Never let a stale failure, retry window,
                    # or old provider/session result suppress the real lookup
                    # after Super Admin credentials have changed.
                    fetch_status, party = process_gstin(gstin, batch=batch, force=True)
                else:
                    fetch_status, party = process_gstin(gstin, batch=batch, force=True)
                return gstin, fetch_status, party, bool(retry_incomplete_only)
            except Exception:
                return gstin, "Failed", None, False
            finally:
                if threaded:
                    close_old_connections()

        # Read fresh records locally first, then only submit unresolved GSTINs
        # to the provider. Ten workers keeps provider traffic bounded without
        # turning a normal 50-party batch into fifty sequential requests.
        pending_gstins = []
        for gstin in input_gstins:
            party = GSTParty.objects.filter(gstin=gstin).first()
            if not retry_incomplete_only and party_is_fresh(party):
                status_name = "Completed Manually" if party.lookup_status == "Completed Manually" else "Existing"
                rows.append(result_row(gstin, status_name, party))
                skipped_complete += 1
            else:
                pending_gstins.append(gstin)

        # Django's TestCase and any caller-owned atomic transaction must stay
        # on one connection; production requests are not wrapped this way and
        # use the controlled provider concurrency above.
        if connection.in_atomic_block or not connection.get_autocommit():
            results = [process_one(gstin) for gstin in pending_gstins]
        else:
            worker_count = min(10, max(1, len(pending_gstins)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                results = [future.result() for future in as_completed(
                    executor.submit(process_one, gstin, True) for gstin in pending_gstins
                )]
        for gstin, fetch_status, party, retry_candidate in results:
            if retry_incomplete_only:
                retry_candidates += int(retry_candidate)
                api_calls_made += getattr(party, "_lookup_diagnostics", {}).get("api_calls", 0) if party else 0
                if fetch_status in {"Existing", "Fetched", "Fetched via Fallback", "Completed Manually"}:
                    completed_after_retry += 1
            rows.append(result_row(gstin, fetch_status, party, reason="Processing failed" if fetch_status == "Failed" and party is None else ""))
        rows.sort(key=lambda row: input_gstins.index(row["gstin"]) if row["gstin"] in input_gstins else len(input_gstins))
        for row in rows:
            if row["status"] == "Pending":
                row["status"] = "Failed"
                row["reason"] = row["reason"] or "Processing incomplete"
        processed = {row["gstin"] for row in rows}
        for gstin in input_gstins:
            if gstin not in processed:
                rows.append(result_row(gstin, "Failed", reason="Processing incomplete"))
        processed = {row["gstin"] for row in rows}
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
        missing_gstins = sorted(set(diagnostics["unique_party_gstins"]) - returned_gstins)
        sandbox_retry = str(GSTLookupService.status().get("provider") or "").lower() == "sandbox"
        unresolved = sum(
            row["status"] in {"Incomplete", "Rate Limited", "Sandbox Lookup Failed",
                              "Sandbox Not Configured", "OTP Required",
                              "Sandbox Session Required", "Sandbox Session Failed",
                              "Failed", "Pending"}
            or (sandbox_retry and row["status"] == "Fetched via Fallback")
            for row in rows
        )
        enriched = sum(
            row["status"] in {"Fetched", "Existing", "Completed Manually",
                              "Fetched via Fallback"}
            and not (sandbox_retry and row["status"] == "Fetched via Fallback")
            for row in rows
        )
        sandbox_provider_status, sandbox_provider_message = _batch_sandbox_provider_status(rows)
        sandbox_failure_rows = {"Sandbox Lookup Failed", "Sandbox Not Configured", "Sandbox Session Failed"}
        sandbox_lookup_failed = bool(sandbox_retry and input_gstins and enriched == 0 and
                                     len(rows) == len(input_gstins) and
                                     all(row.get("status") in sandbox_failure_rows for row in rows) and
                                     any((row.get("sandbox_lookup") or {}).get("sandbox_lookup_attempted") for row in rows))
        first_lookup = next(((row.get("sandbox_lookup") or {}) for row in rows if row.get("sandbox_lookup")), {})
        sandbox_rows = [row.get("sandbox_lookup") or {} for row in rows if row.get("sandbox_lookup")]
        batch_party_details_complete = not sandbox_rows or all(bool(item.get("party_details_complete")) for item in sandbox_rows)
        response_message = (f"Sandbox lookup failed: {sandbox_provider_message}" if sandbox_lookup_failed and sandbox_provider_message
                            else "GSTIN lookup source is not configured." if not configured and needs_provider
                            else "Party details lookup completed.")
        return Response({"success": not sandbox_lookup_failed, "lookup_status": "FAILED" if sandbox_lookup_failed else "COMPLETED",
                         "lookup_failed": sandbox_lookup_failed or any(bool(item.get("lookup_failed")) for item in sandbox_rows),
                         "party_details_complete": batch_party_details_complete,
                         "batch_id": batch.id, "unique_gstins": len(set(input_gstins)),
                         "attempted": len(pending_gstins),
                         "enriched": enriched, "failed": unresolved,
                         "remaining": unresolved, "enrichment_complete": unresolved == 0,
                         "total": len(input_gstins),
                         "total_parties": diagnostics["unique_party_gstin_count"], **counts,
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
                         "message": response_message,
                         "environment": first_lookup.get("environment", ""),
                         "base_url": first_lookup.get("base_url", ""),
                         "credential_type": first_lookup.get("credential_type", ""),
                         "authentication_status": first_lookup.get("authentication_status", ""),
                         "taxpayer_lookup_http_status": first_lookup.get("taxpayer_lookup_http_status"),
                         "provider_message": first_lookup.get("provider_message", ""),
                         "token_refreshed": bool(first_lookup.get("token_refreshed")),
                         "sandbox_provider_status": sandbox_provider_status,
                         "sandbox_provider_message": sandbox_provider_message})

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

class CompanyVerifyView(APIView):
    """Company Verification -> DB Storage step: verify the entered company by
    NAME ONLY against the company currently open in Tally, and -- only on
    success -- fetch and persist the company + license identity to
    companydetails_tbl. Returns only minimal verification flags; see
    services.company_verification.verify_and_store_company."""
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        entered_company = str(request.data.get("tally_company_name") or "").strip()
        if entered_company:
            details = dict(batch.company_details or {})
            details["selected_tally_company"] = entered_company
            batch.company_details = details
            batch.save(update_fields=["company_details"])
        return Response(verify_and_store_company(entered_company, batch.company_gstin, batch=batch))

class TallyConnectionView(APIView):
    def get(self, request):
        # On the VPS, localhost is the VPS itself.  In agent-required mode the
        # connection screen must therefore reflect the customer's outbound
        # agent heartbeat, never attempt a misleading VPS localhost probe.
        if getattr(settings, "TALLY_LOCAL_AGENT_REQUIRED", False):
            fingerprint = request.headers.get("X-Device-ID", "")
            agent = (LocalTallyAgent.objects.filter(
                device__license__customer=request.user, device__status=LicensedDevice.ACTIVE,
                device__device_fingerprint=fingerprint,
            ).order_by("-last_seen_at").first())
            online = bool(agent and agent.last_seen_at and (timezone.now() - agent.last_seen_at).total_seconds() <= 90)
            ready = bool(online and agent.tally_reachable)
            label = "Ready to Import" if ready else "Agent Offline" if not online else "Wrong Company Open"
            return Response({"agent_status": label, "can_import": ready, "company_open": ready,
                             "read_connected": ready, "http_connected": ready, "tally_connected": ready,
                             "company_name": agent.detected_company_name if agent else "",
                             "company_gstin": agent.detected_company_gstin if agent else "",
                             "message": label})
        return Response(step3_connection_check())

class TallyLicenseView(APIView):
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        return Response(pre_import_security_check(
            batch,
            request.user,
            device_fingerprint=request.headers.get("X-Device-Fingerprint", "") or request.headers.get("X-Device-ID", ""),
            device_name=request.headers.get("X-Device-Name", "") or request.data.get("device_name", ""),
            ip_address=request.META.get("REMOTE_ADDR", ""),
        ))

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
    """Starts (or attaches to) a Tally import job -- never blocks the request
    for the actual import. The real work runs on a background thread inside
    this process (see tally.import_job); the frontend polls
    TallyImportJobStatusView instead of waiting on this response.

    Idempotent for an already-active batch: a second POST while a healthy job
    is running returns that same job_id (status "already_running") rather
    than an error, and never starts a second overlapping job for the batch.
    """
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        license_check = pre_import_security_check(
            batch,
            request.user,
            device_fingerprint=request.headers.get("X-Device-Fingerprint", "") or request.headers.get("X-Device-ID", ""),
            device_name=request.headers.get("X-Device-Name", "") or request.data.get("device_name", ""),
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
        if not license_check.get("ready"):
            return Response(
                {
                    **license_check,
                    "code": "PRE_IMPORT_LICENSE_FAILED",
                    "message": "No vouchers were sent to Tally.",
                },
                status=403,
            )
        request_id = request.headers.get("X-Request-ID", "") or str(request.data.get("request_id", ""))
        fingerprint = request.headers.get("X-Device-Fingerprint", "") or request.headers.get("X-Device-ID", "")
        agent = (LocalTallyAgent.objects.select_related("device__license").filter(
            device__license=batch.product_license, device__status=LicensedDevice.ACTIVE,
            tally_reachable=True, detected_company_gstin=batch.company_gstin,
            device__device_fingerprint=fingerprint or "__no_matching_device__",
        ).first())
        if getattr(settings, "TALLY_LOCAL_AGENT_REQUIRED", False) and not agent:
            return Response({"code": "TALLY_AGENT_OFFLINE", "message": "Local Tally Agent is offline or the registered company is not open."}, status=409)
        job, outcome = start_import_job(batch, request_id=request_id, local_agent=agent)
        # serialize_job() always carries the job row's OWN status (RUNNING,
        # PENDING, ...) under the same "status" key -- it must be spread
        # first so the outcome literal below (what the frontend actually
        # branches on: "already_running"/"interrupted"/"running") is the one
        # that survives, not silently overwritten by it.
        if outcome == "already_running":
            return Response({**serialize_job(job), "status": "already_running"}, status=200)
        if outcome == "paused":
            return Response(serialize_job(job), status=200)
        if outcome == "interrupted":
            return Response({"status": "interrupted", "message": "Previous import was interrupted. Reconcile before resuming.",
                             "previous_job": serialize_job(job)}, status=200)
        run_job_in_background(job.job_id)
        return Response({**serialize_job(job), "status": "running"}, status=202)


class TallyImportJobStatusView(APIView):
    def get(self, request, job_id):
        job = get_job(job_id)
        if job is None:
            return Response({"detail": "Import job not found."}, status=404)
        return Response(serialize_job(job))


class TallyImportJobPauseView(APIView):
    """Requests a safe pause -- never kills the voucher/batch currently in
    flight to Tally (see tally.import_job.request_pause / the
    should_pause_callback wired into import_batch's loop). The job's status
    only actually becomes PAUSED once the worker thread's own next poll of
    this flag lands, which the frontend observes by continuing to poll
    TallyImportJobStatusView -- pause_requested is exposed there too so the
    UI can show a brief "pausing" state in between."""
    def post(self, request, job_id):
        job = get_job(job_id)
        if job is None:
            return Response({"detail": "Import job not found."}, status=404)
        request_pause(job_id)
        job.refresh_from_db()
        return Response(serialize_job(job))


class TallyImportJobResumeView(APIView):
    """Resumes a PAUSED job on a fresh background thread. Safe by
    construction, not by tracking a resume position: import_batch() always
    re-verifies against Tally before writing, so every voucher already
    successfully imported before the pause comes back "Already Imported"
    and is never re-sent (see tally.import_job.resume_job)."""
    def post(self, request, job_id):
        job = get_job(job_id)
        if job is None:
            return Response({"detail": "Import job not found."}, status=404)
        if job.status != "PAUSED":
            return Response(serialize_job(job), status=200)
        license_check = pre_import_security_check(
            job.batch,
            request.user,
            device_fingerprint=request.headers.get("X-Device-Fingerprint", "") or request.headers.get("X-Device-ID", ""),
            device_name=request.headers.get("X-Device-Name", "") or request.data.get("device_name", ""),
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
        if not license_check.get("ready"):
            return Response(
                {
                    **license_check,
                    "code": "PRE_IMPORT_LICENSE_FAILED",
                    "message": "No vouchers were sent to Tally.",
                },
                status=403,
            )
        resume_job(job_id)
        job.refresh_from_db()
        return Response(serialize_job(job))


class TallyImportActiveJobView(APIView):
    """Page-reload recovery (task spec acceptance test 23): what Step 6
    should restore to before the user does anything -- an active job to
    reattach to, a just-detected INTERRUPTED one to reconcile/offer Resume
    for, or nothing at all if this batch has never had one."""
    def get(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        job = get_active_job_for_batch(batch)
        if job is None:
            return Response({"status": "none"})
        return Response({"status": job.status, **serialize_job(job)})


class ProductLicenseActivateView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        result = verify_license_snapshot(
            activation_key=request.data.get("activation_key", ""),
            device_fingerprint=request.data.get("device_fingerprint", ""),
            device_name=request.data.get("device_name", ""),
            windows_version=request.data.get("windows_version", ""),
            app_version=request.data.get("app_version", ""),
            detected_tally_serial=request.data.get("detected_tally_serial", ""),
            tally_edition=request.data.get("tally_edition", ""),
            tss_status=request.data.get("tss_status", ""),
            license_administrator=request.data.get("license_administrator", ""),
            current_company_name=request.data.get("current_company_name", ""),
            current_company_gstin=request.data.get("current_company_gstin", ""),
            state=request.data.get("state", ""),
            financial_year=request.data.get("financial_year", ""),
            ip_address=request.META.get("REMOTE_ADDR", ""),
            source="activation",
        )
        if not result.get("ready"):
            return Response(result, status=403)
        from .models import ProductLicense
        license_obj = ProductLicense.objects.select_related("customer").get(pk=result["license_id"])
        user = license_obj.customer
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return Response({"user": public_user(user), **tokens_for(user), **result})


class ProductLicenseVerifyView(APIView):
    def post(self, request):
        result = verify_license_snapshot(
            license_id=request.data.get("license_id"),
            user=request.user,
            device_fingerprint=request.data.get("device_fingerprint", "") or request.headers.get("X-Device-Fingerprint", ""),
            device_name=request.data.get("device_name", ""),
            windows_version=request.data.get("windows_version", ""),
            app_version=request.data.get("app_version", ""),
            detected_tally_serial=request.data.get("detected_tally_serial", ""),
            tally_edition=request.data.get("tally_edition", ""),
            tss_status=request.data.get("tss_status", ""),
            license_administrator=request.data.get("license_administrator", ""),
            current_company_name=request.data.get("current_company_name", ""),
            current_company_gstin=request.data.get("current_company_gstin", ""),
            state=request.data.get("state", ""),
            financial_year=request.data.get("financial_year", ""),
            ip_address=request.META.get("REMOTE_ADDR", ""),
            source="startup",
        )
        return Response(result, status=200 if result.get("ready") else 403)


class ProductLicenseHeartbeatView(APIView):
    def post(self, request):
        result = verify_license_snapshot(
            license_id=request.data.get("license_id"),
            user=request.user,
            device_fingerprint=request.data.get("device_fingerprint", "") or request.headers.get("X-Device-Fingerprint", ""),
            detected_tally_serial=request.data.get("detected_tally_serial", ""),
            current_company_gstin=request.data.get("current_company_gstin", ""),
            ip_address=request.META.get("REMOTE_ADDR", ""),
            source="heartbeat",
        )
        return Response(result, status=200 if result.get("ready") else 403)


class ProductLicensePreImportCheckView(APIView):
    def post(self, request, pk):
        batch = get_object_or_404(GSTImportBatch, pk=pk)
        result = pre_import_security_check(
            batch,
            request.user,
            device_fingerprint=request.data.get("device_fingerprint", "") or request.headers.get("X-Device-Fingerprint", "") or request.headers.get("X-Device-ID", ""),
            device_name=request.data.get("device_name", "") or request.headers.get("X-Device-Name", ""),
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
        status_code = 200 if result.get("ready") else 403
        return Response(result, status=status_code)


class MyProfileView(APIView):
    """Read-only customer-side profile: identity, company, license, device --
    the single place a customer can see everything about their own license.
    Every value here is either a stored field or one of the same
    status/days-remaining helpers the Super Admin console uses (see
    services/product_license.py) -- nothing is computed or faked client-side."""

    def get(self, request):
        user = request.user
        profile = getattr(user, "gst_profile", None)

        license_obj = (ProductLicense.objects.filter(customer=user)
                       .exclude(status=ProductLicense.REVOKED)
                       .order_by("-last_verified_at", "-created_at").first())

        company = None
        device = None
        if license_obj:
            company = CompanyDetails.objects.filter(
                gstin=license_obj.licensed_gstin,
                tally_serial_number=license_obj.licensed_tally_serial,
                company_verified=True,
            ).order_by("-verified_at", "-updated_at").first()

            requested_device_id = (
                request.headers.get("X-Device-Fingerprint", "")
                or request.headers.get("X-Device-ID", "")
            ).strip()
            devices = LicensedDevice.objects.filter(license=license_obj, status=LicensedDevice.ACTIVE)
            device = (
                devices.filter(device_fingerprint=requested_device_id).first()
                if requested_device_id else devices.order_by("-last_seen").first()
            )
        system_configuration = collect_system_configuration()
        requested_device_id = (
            request.headers.get("X-Device-Fingerprint", "")
            or request.headers.get("X-Device-ID", "")
        ).strip()
        device_authentication = {
            "device_name": system_configuration["device_name"],
            "device_id": device.device_fingerprint if device else requested_device_id,
            "first_seen": device.first_seen if device else None,
            "last_seen": device.last_seen if device else None,
            "status": "AUTHORIZED" if device else "NOT_AUTHORIZED",
        }

        return Response({
            "user": {
                "name": user.get_full_name() or user.username,
                "email": user.email,
                "phone": profile.phone_number if profile else "",
                "role": "Customer",
                "has_password": user.has_usable_password(),
            },
            "company": {
                "company_name": company.company_name,
                "gstin": company.gstin,
                "state": company.state,
                "state_code": company.gstin[:2] if len(company.gstin or "") >= 2 else "",
                "financial_year": company.financial_year,
                "verified": company.company_verified,
            } if company else None,
            "license": {
                "activation_key": product_license_activation_key(license_obj),
                "registered_tally_serial": license_obj.licensed_tally_serial,
                "plan": license_obj.plan,
                "activated_at": license_obj.activated_at,
                "start_date": license_obj.purchase_date,
                "expiry_date": license_obj.expiry_date,
                "days_remaining": product_license_days_remaining(license_obj),
                "status": product_license_status(license_obj),
            } if license_obj else None,
            "device_authentication": device_authentication,
            "terms": {
                "last_updated": getattr(settings, "TERMS_LAST_UPDATED", ""),
                "items": [
                    "Use this application only on the authorized registered device.",
                    "Keep your account and device credentials secure.",
                    "The same successfully imported source file cannot be imported again as a duplicate.",
                    "Company and GSTIN information must match the verified business records.",
                    "Device changes may require administrator approval.",
                ],
            },
            "system_configuration": system_configuration,
        })

    def patch(self, request):
        """Editable identity fields only -- Name, Email, Phone. Everything
        else on the profile (license, company, device, Tally details) is
        derived server-side and was never accepted here."""
        user = request.user
        data = request.data
        name = str(data.get("name", "")).strip() if "name" in data else None
        email = str(data.get("email", "")).strip().lower() if "email" in data else None
        phone = str(data.get("phone", "")).strip() if "phone" in data else None

        errors = {}
        if name is not None and not name:
            errors["name"] = "Name is required."
        if email is not None:
            try:
                validate_email(email)
            except ValidationError:
                errors["email"] = "Enter a valid email address."
            else:
                if get_user_model().objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                    errors["email"] = "Email is already registered to another account."
        if phone is not None:
            if len(phone) != 10 or not phone.isdigit():
                errors["phone"] = "Phone number must contain exactly 10 digits."
            elif UserProfile.objects.filter(phone_number=phone).exclude(user=user).exists():
                errors["phone"] = "Phone number is already registered to another account."
        if errors:
            return Response({"detail": next(iter(errors.values())), "errors": errors}, status=400)

        update_fields = []
        if name is not None:
            user.first_name = name
            user.last_name = ""
            update_fields += ["first_name", "last_name"]
        if email is not None:
            user.email = email
            update_fields.append("email")
        if update_fields:
            user.save(update_fields=update_fields)
        if phone is not None:
            UserProfile.objects.update_or_create(user=user, defaults={"phone_number": phone})

        return self.get(request)


class ChangePasswordView(APIView):
    """A password only ever needs verifying against the current one once the
    account actually has a usable one -- the first time a customer sets a
    password (having only ever used the device-trust login), there is
    nothing to check it against."""

    def post(self, request):
        user = request.user
        current_password = str(request.data.get("current_password", ""))
        new_password = str(request.data.get("new_password", ""))
        if len(new_password) < 8:
            return Response({"detail": "New password must be at least 8 characters."}, status=400)
        if user.has_usable_password() and not user.check_password(current_password):
            return Response({"detail": "Current password is incorrect."}, status=400)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        return Response({"detail": "Password changed successfully."})
