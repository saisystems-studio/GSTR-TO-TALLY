import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from gst_tally.auth_service import secret_hash
from gst_tally.auth_views import tokens_for
from gst_tally.auth_service import secret_hash
from gst_tally.models import DeviceActivation, DeviceActivationRequest, LicensedDevice, LicenseAuditLog, ProductLicense
from gst_tally.services.product_license import record_license_audit
from subscriptions.models import RenewalHistory, Subscription, SubscriptionAuditLog

from .models import (AdminAuditLog, CompanyLimitHistory, CustomerNote, CustomerProfile,
                      PasswordResetToken, Payment, RegisteredCompany, SubscriptionPlan,
                      SandboxAPIConfiguration, SuperAdminProfile, SupportTicket)
from .pagination import paginate
from .permissions import IsSuperAdmin, IsSuperAdminAccount
from .serializers import (AdminAuditLogSerializer, CompanyDetailsSerializer, CompanyLimitHistorySerializer,
                           CustomerListSerializer, CustomerNoteSerializer, CustomerProfileSerializer,
                           DeviceActivationSerializer, DeviceActivationRequestSerializer, LicenseAuditLogSerializer,
                           LicensedDeviceSerializer, PaymentSerializer, ProductLicenseSerializer, RegisteredCompanySerializer,
                           RenewalHistorySerializer, SubscriptionAuditLogSerializer, SubscriptionPlanSerializer,
                           SuperAdminProfileSerializer, SuperAdminSettingsSerializer, SupportTicketSerializer,
                           TallyCompanyMappingSerializer)
from .services import companies as companies_service
from .services import customers as customers_service
from .services import payments as payments_service
from .services import reports as reports_service
from .services import settings as settings_service
from .services import subscriptions_admin as subscriptions_admin_service
from .services import support as support_service
from .services.audit import client_ip, log_admin_action
from .services.sandbox_configuration import clear_cached_access_token, encrypt, safe_status
from .services.bootstrap import DEFAULT_PASSWORD, DEFAULT_USERNAME, create_default_superadmin
from .services.dashboard import build_dashboard, build_recent_sections

User = get_user_model()
logger = logging.getLogger(__name__)

RESET_TOKEN_TTL_MINUTES = 30


# ---------------------------------------------------------------- Auth --

class SuperAdminLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))
        if not username or not password:
            return Response({"detail": "Username and password are required."}, status=400)
        if username == DEFAULT_USERNAME and password == DEFAULT_PASSWORD:
            create_default_superadmin()
        user = authenticate(request, username=username, password=password)
        if not user or not hasattr(user, "superadmin_profile"):
            return Response({"detail": "Invalid username or password."}, status=400)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        profile = user.superadmin_profile
        return Response({
            "user": SuperAdminProfileSerializer(profile).data,
            "must_change_password": profile.must_change_password,
            **tokens_for(user),
        })


class SuperAdminRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            refresh = RefreshToken(str(request.data.get("refresh", "")))
            user = User.objects.get(id=refresh["user_id"], is_active=True)
        except (TokenError, User.DoesNotExist, KeyError):
            return Response({"detail": "Your session has expired. Please login again."}, status=401)
        if not hasattr(user, "superadmin_profile"):
            return Response({"detail": "Your session has expired. Please login again."}, status=401)
        # No server-side blacklist: the old refresh token is simply left to
        # expire naturally. A new access + refresh pair is issued below.
        return Response(tokens_for(user))


class SuperAdminLogoutView(APIView):
    permission_classes = [IsSuperAdminAccount]

    def post(self, request):
        # Stateless logout, no blacklist DB table required -- the frontend
        # discards its tokens and the old refresh token is left to expire
        # naturally on its own.
        return Response(status=204)


class SuperAdminMeView(APIView):
    permission_classes = [IsSuperAdminAccount]

    def get(self, request):
        return Response(SuperAdminProfileSerializer(request.user.superadmin_profile).data)

    def patch(self, request):
        serializer = SuperAdminProfileSerializer(request.user.superadmin_profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SuperAdminChangePasswordView(APIView):
    """Spec section 4: current password must match, new/confirm must match
    and be non-blank, hashed via Django's own `set_password` -- never a raw
    field write. Clears `must_change_password` on success, which is what
    unblocks every other `IsSuperAdmin`-gated endpoint."""
    permission_classes = [IsSuperAdminAccount]

    def post(self, request):
        current_password = str(request.data.get("current_password", ""))
        new_password = str(request.data.get("new_password", ""))
        confirm_password = str(request.data.get("confirm_password", ""))
        user = request.user
        if not user.check_password(current_password):
            return Response({"detail": "Current password is incorrect."}, status=400)
        if not new_password:
            return Response({"detail": "New password cannot be blank."}, status=400)
        if new_password != confirm_password:
            return Response({"detail": "New password and confirm password must match."}, status=400)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        profile = user.superadmin_profile
        profile.must_change_password = False
        profile.password_changed_at = timezone.now()
        profile.save(update_fields=["must_change_password", "password_changed_at", "updated_at"])
        return Response({"detail": "Password updated successfully."})


class SuperAdminForgotPasswordView(APIView):
    """Spec section 5: backend-ready reset-token architecture. No email
    backend is configured in this project, so the raw token is logged
    server-side (visible in the Django console) instead of emailed, and is
    never returned in the response -- and the response is identical whether
    or not the account exists, so this endpoint can't be used to enumerate
    Super Admin usernames."""
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = str(request.data.get("username") or request.data.get("email") or "").strip()
        user = User.objects.filter(username__iexact=identifier).first() or User.objects.filter(email__iexact=identifier).first()
        if user and hasattr(user, "superadmin_profile"):
            raw_token = secrets.token_urlsafe(32)
            PasswordResetToken.objects.create(
                user=user, token_hash=secret_hash(raw_token),
                expires_at=timezone.now() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
            )
            logger.info("Super Admin password reset requested for %s -- reset token (dev-only log, never emailed): %s", user.username, raw_token)
        return Response({"detail": "If a Super Admin account matches, reset instructions have been generated."})


class SuperAdminResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        raw_token = str(request.data.get("token", ""))
        new_password = str(request.data.get("new_password", ""))
        confirm_password = str(request.data.get("confirm_password", ""))
        if not new_password or new_password != confirm_password:
            return Response({"detail": "New password and confirm password must match and cannot be blank."}, status=400)
        token_hash = secret_hash(raw_token)
        token = PasswordResetToken.objects.filter(token_hash=token_hash).order_by("-created_at").first()
        if not token or not token.is_valid():
            return Response({"detail": "This reset link is invalid or has expired."}, status=400)
        user = token.user
        user.set_password(new_password)
        user.save(update_fields=["password"])
        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])
        profile = user.superadmin_profile
        profile.must_change_password = False
        profile.password_changed_at = timezone.now()
        profile.save(update_fields=["must_change_password", "password_changed_at", "updated_at"])
        return Response({"detail": "Password updated successfully."})


# ----------------------------------------------------------- Dashboard --

class SuperAdminDashboardView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        data = build_dashboard()
        data["recent"] = build_recent_sections()
        return Response(data)


# ------------------------------------------------------------ Customers --

class CustomerListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = customers_service.customer_queryset(
            search=(request.query_params.get("search") or "").strip(),
            status=request.query_params.get("status"),
            plan=request.query_params.get("plan"),
            state=request.query_params.get("state"),
        )
        return Response(paginate(request, qs, CustomerListSerializer))


class CustomerDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        subscription, _ = Subscription.objects.get_or_create(user=user)
        subscription.refresh_status()
        entitlement = customers_service.get_or_create_entitlement(user)
        profile = customers_service.get_or_create_customer_profile(user)
        companies = [companies_service.enrich_with_tally_details(c) for c in user.registered_companies.all()]
        license_obj = ProductLicense.objects.filter(customer=user).prefetch_related("licensed_devices").order_by("-last_verified_at", "-created_at").first()
        active_device = license_obj.licensed_devices.filter(status=LicensedDevice.ACTIVE).order_by("-last_seen").first() if license_obj else None
        license_summary = ProductLicenseSerializer(license_obj).data if license_obj else None
        if license_summary:
            license_summary["registered_tally_serial"] = license_summary["licensed_tally_serial"]
        return Response({
            "customer": {
                "id": user.id, "username": user.username, "name": user.get_full_name() or user.username, "email": user.email,
                "phone": getattr(getattr(user, "gst_profile", None), "phone_number", ""),
                "profile": CustomerProfileSerializer(profile).data,
                "account_created": user.date_joined, "last_login": user.last_login,
            },
            "subscription": {
                "plan": subscription.plan, "purchase_date": subscription.purchase_date,
                "activation_date": subscription.activation_date, "expiry_date": subscription.expiry_date,
                "days_remaining": subscription.days_remaining(), "subscription_status": subscription.subscription_status,
                "is_suspended": subscription.is_suspended,
            },
            "entitlement": {
                "company_limit": entitlement.company_limit, "company_used": entitlement.used_companies(),
                "company_remaining": entitlement.remaining_companies(), "device_limit": entitlement.device_limit,
                "device_used": entitlement.used_devices(), "device_remaining": entitlement.remaining_devices(),
            },
            "registered_companies": [
                {**RegisteredCompanySerializer(c["registered_company"]).data,
                 "tally": CompanyDetailsSerializer(c["company_details"]).data if c["company_details"] else None,
                 "mapping": TallyCompanyMappingSerializer(c["tally_mapping"]).data if c["tally_mapping"] else None}
                for c in companies
            ],
            "devices": DeviceActivationSerializer(companies_service.device_queryset(user), many=True).data,
            "license_summary": license_summary,
            "device_summary": LicensedDeviceSerializer(active_device).data if active_device else None,
            "payments": PaymentSerializer(Payment.objects.filter(user=user).order_by("-created_at")[:20], many=True).data,
            "renewal_history": RenewalHistorySerializer(subscription.renewal_history.all(), many=True).data,
            "company_limit_history": CompanyLimitHistorySerializer(CompanyLimitHistory.objects.filter(user=user), many=True).data,
            "support_tickets": SupportTicketSerializer(SupportTicket.objects.filter(user=user).order_by("-created_at"), many=True).data,
            "notes": CustomerNoteSerializer(CustomerNote.objects.filter(user=user), many=True).data,
            "audit_history": AdminAuditLogSerializer(AdminAuditLog.objects.filter(customer=user)[:50], many=True).data,
        })


class CustomerSuspendView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        subscription = customers_service.suspend_customer(user, request.user, reason=request.data.get("reason", ""))
        return Response({"subscription_status": subscription.subscription_status})


class CustomerReactivateView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        subscription = customers_service.reactivate_customer(user, request.user, reason=request.data.get("reason", ""))
        return Response({"subscription_status": subscription.subscription_status})


class CustomerCompanyLimitView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        try:
            new_limit = int(request.data.get("new_limit"))
        except (TypeError, ValueError):
            return Response({"detail": "new_limit must be an integer."}, status=400)
        if new_limit < 0:
            return Response({"detail": "new_limit cannot be negative."}, status=400)
        entitlement = customers_service.change_company_limit(user, new_limit, request.user, reason=request.data.get("reason", ""))
        return Response({"company_limit": entitlement.company_limit})


class CustomerNotesView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, user_id):
        return Response(CustomerNoteSerializer(CustomerNote.objects.filter(user_id=user_id), many=True).data)

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        note = CustomerNote.objects.create(user=user, note=str(request.data.get("note", "")).strip(), created_by=request.user)
        return Response(CustomerNoteSerializer(note).data, status=201)


class CustomerCompaniesView(APIView):
    """POST /customers/<user_id>/companies/ -- register a new company under
    a customer, enforcing the plan's company limit (spec section 67)."""
    permission_classes = [IsSuperAdmin]

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        gstin = str(request.data.get("gstin", "")).strip()
        if not gstin:
            return Response({"detail": "gstin is required."}, status=400)
        entitlement = customers_service.get_or_create_entitlement(user)
        if entitlement.used_companies() >= entitlement.company_limit and not request.data.get("override"):
            return Response({"detail": "Company limit reached for this customer.", "code": "COMPANY_LIMIT_REACHED"}, status=400)
        company, created = companies_service.register_company(
            user, gstin, company_name=str(request.data.get("company_name", "")).strip(), performed_by=request.user,
        )
        return Response(RegisteredCompanySerializer(company).data, status=201 if created else 200)


# ------------------------------------------------------------- Companies --

class CompanyListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = companies_service.company_queryset(search=(request.query_params.get("search") or "").strip())
        return Response(paginate(request, qs, RegisteredCompanySerializer))


class CompanyDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, company_id):
        company = get_object_or_404(RegisteredCompany, pk=company_id)
        enriched = companies_service.enrich_with_tally_details(company)
        return Response({
            "registered_company": RegisteredCompanySerializer(company).data,
            "customer": {"id": company.user_id, "username": company.user.username},
            "company_details": CompanyDetailsSerializer(enriched["company_details"]).data if enriched["company_details"] else None,
            "tally_mapping": TallyCompanyMappingSerializer(enriched["tally_mapping"]).data if enriched["tally_mapping"] else None,
            "devices": DeviceActivationSerializer(companies_service.device_queryset(company.user), many=True).data,
        })

    def patch(self, request, company_id):
        company = get_object_or_404(RegisteredCompany, pk=company_id)
        new_status = request.data.get("status")
        if new_status and new_status in dict(RegisteredCompany.STATUS_CHOICES):
            companies_service.set_company_status(company, new_status, request.user, reason=request.data.get("reason", ""))
        if "admin_notes" in request.data:
            company.admin_notes = request.data["admin_notes"]
            company.save(update_fields=["admin_notes", "updated_at"])
        return Response(RegisteredCompanySerializer(company).data)


class CompanyDeviceResetView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, company_id, device_id):
        company = get_object_or_404(RegisteredCompany, pk=company_id)
        device = get_object_or_404(DeviceActivation, pk=device_id, user=company.user)
        companies_service.reset_device(device, request.user, reason=request.data.get("reason", ""))
        return Response({"is_active": device.is_active})


# ---------------------------------------------------------------- Plans --

class PlanListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response(SubscriptionPlanSerializer(SubscriptionPlan.objects.all(), many=True).data)

    def post(self, request):
        serializer = SubscriptionPlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.save()
        log_admin_action(request.user, "PLAN_CREATED", "SubscriptionPlan", plan.id, new_value=serializer.data)
        return Response(serializer.data, status=201)


class PlanDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def patch(self, request, plan_id):
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        old_value = SubscriptionPlanSerializer(plan).data
        serializer = SubscriptionPlanSerializer(plan, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_admin_action(request.user, "PLAN_UPDATED", "SubscriptionPlan", plan.id, old_value=old_value, new_value=serializer.data)
        return Response(serializer.data)

    def delete(self, request, plan_id):
        # Spec section 75: prefer Active/Inactive over destructive delete.
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        plan.is_active = False
        plan.save(update_fields=["is_active", "updated_at"])
        log_admin_action(request.user, "PLAN_DEACTIVATED", "SubscriptionPlan", plan.id)
        return Response(status=204)


# --------------------------------------------------------- Subscriptions --

class SubscriptionListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = customers_service.customer_queryset(search=(request.query_params.get("search") or "").strip())
        return Response(paginate(request, qs, CustomerListSerializer))


class SubscriptionRenewView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        data = request.data
        custom_expiry_date = data.get("custom_expiry_date") or None
        subscription = subscriptions_admin_service.renew_subscription(
            user, request.user,
            period_months=data.get("period_months"), period_years=data.get("period_years"),
            custom_expiry_date=custom_expiry_date, amount=data.get("amount"),
            payment_status=data.get("payment_status"), notes=data.get("notes", ""),
        )
        return Response({"expiry_date": subscription.expiry_date, "subscription_status": subscription.subscription_status})


class SubscriptionChangePlanView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        plan_code = str(request.data.get("plan_code", ""))
        try:
            subscription, entitlement = subscriptions_admin_service.change_plan(user, plan_code, request.user, reason=request.data.get("reason", ""))
        except SubscriptionPlan.DoesNotExist:
            return Response({"detail": "Unknown plan code."}, status=400)
        return Response({"plan": subscription.plan, "company_limit": entitlement.company_limit, "device_limit": entitlement.device_limit})


# ------------------------------------------------------------- Payments --

class PaymentListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = payments_service.payment_queryset(search=(request.query_params.get("search") or "").strip(), status=request.query_params.get("status"))
        return Response(paginate(request, qs, PaymentSerializer))

    def post(self, request):
        if not str(request.data.get("invoice_number", "")).strip():
            return Response({"detail": "invoice_number is required."}, status=400)
        user = get_object_or_404(User, pk=request.data.get("user_id"))
        payment = Payment.objects.create(
            user=user, plan_id=request.data.get("plan_id"), invoice_number=request.data["invoice_number"],
            order_number=request.data.get("order_number", ""), transaction_id=request.data.get("transaction_id", ""),
            gateway_reference=request.data.get("gateway_reference", ""), base_amount=request.data.get("base_amount", 0),
            discount=request.data.get("discount", 0), tax=request.data.get("tax", 0),
            final_amount=request.data.get("final_amount", 0), payment_method=request.data.get("payment_method", ""),
            payment_status=request.data.get("payment_status", Payment.PENDING), payment_date=request.data.get("payment_date"),
            billing_period_start=request.data.get("billing_period_start"), billing_period_end=request.data.get("billing_period_end"),
            notes=request.data.get("notes", ""), created_by=request.user,
        )
        log_admin_action(request.user, "PAYMENT_RECORDED", "Payment", payment.id, customer=user)
        return Response(PaymentSerializer(payment).data, status=201)


class PaymentDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, payment_id):
        payment = get_object_or_404(Payment, pk=payment_id)
        return Response(PaymentSerializer(payment).data)

    def patch(self, request, payment_id):
        payment = get_object_or_404(Payment, pk=payment_id)
        new_status = request.data.get("payment_status")
        if new_status:
            payments_service.update_payment_status(payment, new_status, request.user, reason=request.data.get("reason", ""))
        return Response(PaymentSerializer(payment).data)


# ---------------------------------------------------------------- Usage --

class UsageView(APIView):
    """Per-customer usage rollup (spec sections 37-39) -- built entirely
    from DB aggregate queries grouped by `uploaded_by`, never a Python loop
    over every invoice/voucher row."""
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        from django.db.models import Count, Max
        from gst_tally.models import GSTImportBatch, GSTInvoice, TallyVoucherMapping

        batches = (GSTImportBatch.objects.exclude(uploaded_by__isnull=True)
                   .values("uploaded_by", "uploaded_by__username")
                   .annotate(files_uploaded=Count("id"), last_upload=Max("uploaded_at")))
        by_user = {row["uploaded_by"]: row for row in batches}
        invoice_counts = dict(GSTInvoice.objects.filter(import_batch__uploaded_by__isnull=False)
                               .values_list("import_batch__uploaded_by").annotate(n=Count("id")))
        voucher_counts = dict(TallyVoucherMapping.objects.filter(batch__uploaded_by__isnull=False, imported_at__isnull=False)
                              .values_list("batch__uploaded_by").annotate(n=Count("id")))
        rows = []
        for user_id, row in by_user.items():
            rows.append({
                "username": row["uploaded_by__username"], "files_uploaded": row["files_uploaded"],
                "invoices_processed": invoice_counts.get(user_id, 0),
                "vouchers_imported": voucher_counts.get(user_id, 0),
                "last_upload": row["last_upload"],
            })
        return Response({"results": rows, "total": len(rows)})


# --------------------------------------------------------------- Support --

class SupportTicketListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = support_service.ticket_queryset(
            search=(request.query_params.get("search") or "").strip(),
            status=request.query_params.get("status"), priority=request.query_params.get("priority"),
        )
        return Response(paginate(request, qs, SupportTicketSerializer))

    def post(self, request):
        user = get_object_or_404(User, pk=request.data.get("user_id"))
        ticket = SupportTicket.objects.create(
            user=user, company_gstin=request.data.get("company_gstin", ""), subject=request.data.get("subject", ""),
            description=request.data.get("description", ""), category=request.data.get("category", ""),
            priority=request.data.get("priority", SupportTicket.MEDIUM),
        )
        return Response(SupportTicketSerializer(ticket).data, status=201)


class SupportTicketDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def patch(self, request, ticket_id):
        ticket = get_object_or_404(SupportTicket, pk=ticket_id)
        fields = {k: v for k, v in request.data.items() if k in ("status", "priority", "assigned_to_id")}
        if fields:
            support_service.update_ticket(ticket, request.user, **fields)
        return Response(SupportTicketSerializer(ticket).data)


# ------------------------------------------------------------ Audit logs --

class AuditLogListView(APIView):
    """Read-only (spec section 46) -- no POST/PATCH/DELETE exposed here at all."""
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        important_license_events = {
            "LICENSE_CREATED", "LICENSE_ACTIVATED", "LICENSE_EXTENDED", "LICENSE_EXPIRED",
            "LICENSE_SUSPENDED", "LICENSE_REACTIVATED", "LICENSE_REVOKED", "TALLY_SERIAL_CHANGED",
            "TALLY_SERIAL_MISMATCH", "DEVICE_REGISTERED", "DEVICE_REPLACED",
            "DEVICE_REVOKED", "EXPIRY_DATE_CHANGED", "GSTIN_MISMATCH",
        }
        action = request.query_params.get("action")
        if action:
            important_license_events = {action}

        license_rows = []
        for item in LicenseAuditLog.objects.select_related("license__customer", "created_by").filter(event_type__in=important_license_events)[:200]:
            license_rows.append({
                "id": f"license-{item.id}",
                "created_at": item.created_at,
                "customer": item.license.customer.username if item.license else "",
                "action": item.event_type,
                "old_value": item.old_value,
                "new_value": item.new_value,
                "changed_by": item.created_by.username if item.created_by else "System",
                "status": "SUCCESS",
            })

        admin_rows = []
        if not action:
            for item in AdminAuditLog.objects.select_related("admin_user", "customer")[:100]:
                admin_rows.append({
                    "id": f"admin-{item.id}",
                    "created_at": item.created_at,
                    "customer": item.customer.username if item.customer else "",
                    "action": item.action,
                    "old_value": item.old_value,
                    "new_value": item.new_value,
                    "changed_by": item.admin_user.username if item.admin_user else "System",
                    "status": "SUCCESS",
                })

        rows = sorted([*license_rows, *admin_rows], key=lambda row: row["created_at"], reverse=True)
        page_size = min(int(request.query_params.get("page_size") or 25), 200)
        page = max(int(request.query_params.get("page") or 1), 1)
        start = (page - 1) * page_size
        total = len(rows)
        return Response({
            "results": rows[start:start + page_size],
            "total": total,
            "page": page,
            "num_pages": max(1, (total + page_size - 1) // page_size),
        })


# ------------------------------------------------------ Product Licenses --

class ProductLicenseListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = ProductLicense.objects.select_related("customer").prefetch_related("licensed_devices").order_by("-created_at")
        return Response(paginate(request, qs, ProductLicenseSerializer))

    def post(self, request):
        user = get_object_or_404(User, pk=request.data.get("customer_id"))
        activation_key = str(request.data.get("activation_key") or secrets.token_urlsafe(16)).strip()
        tally_serial = str(request.data.get("licensed_tally_serial", "")).strip()
        gstin = str(request.data.get("licensed_gstin", "")).strip().upper()
        if not tally_serial:
            return Response({"detail": "licensed_tally_serial is required."}, status=400)
        if not gstin:
            return Response({"detail": "licensed_gstin is required."}, status=400)
        license_obj = ProductLicense.objects.create(
            customer=user,
            activation_key_hash=secret_hash(activation_key),
            display_activation_key=activation_key,
            display_activation_key_suffix=activation_key[-4:],
            licensed_gstin=gstin,
            licensed_tally_serial=tally_serial,
            plan=str(request.data.get("plan") or "Professional"),
            allowed_devices=int(request.data.get("allowed_devices") or 1),
            status=str(request.data.get("status") or ProductLicense.PENDING),
            purchase_date=request.data.get("purchase_date") or timezone.localdate(),
            expiry_date=request.data.get("expiry_date"),
        )
        record_license_audit(license_obj, "LICENSE_CREATED", new_value=ProductLicenseSerializer(license_obj).data,
                             ip_address=client_ip(request), created_by=request.user)
        return Response({**ProductLicenseSerializer(license_obj).data, "activation_key": activation_key}, status=201)


class ProductLicenseDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, license_id):
        license_obj = get_object_or_404(ProductLicense.objects.select_related("customer").prefetch_related("licensed_devices"), pk=license_id)
        return Response({
            "license": ProductLicenseSerializer(license_obj).data,
            "customer": {
                "id": license_obj.customer_id,
                "name": license_obj.customer.get_full_name() or license_obj.customer.username,
                "company_name": ProductLicenseSerializer(license_obj).data["company"],
                "gstin": license_obj.licensed_gstin,
                "phone": ProductLicenseSerializer(license_obj).data["phone"],
                "email": license_obj.customer.email,
            },
            "devices": LicensedDeviceSerializer(license_obj.licensed_devices.all(), many=True).data,
            "device": LicensedDeviceSerializer(license_obj.licensed_devices.filter(status=LicensedDevice.ACTIVE).order_by("-last_seen").first()).data if license_obj.licensed_devices.filter(status=LicensedDevice.ACTIVE).exists() else None,
            "tally": {
                "serial_number": license_obj.licensed_tally_serial,
                "edition": ProductLicenseSerializer(license_obj).data["tally_edition"],
                "tss_status": ProductLicenseSerializer(license_obj).data["tss_status"],
                "license_administrator": ProductLicenseSerializer(license_obj).data["license_administrator"],
            },
            "device_requests": DeviceActivationRequestSerializer(license_obj.device_requests.all().order_by("-requested_at"), many=True).data,
            "audit": LicenseAuditLogSerializer(license_obj.audit_logs.all()[:100], many=True).data,
        })


class ProductLicenseExtendView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, license_id):
        license_obj = get_object_or_404(ProductLicense, pk=license_id)
        old_expiry = license_obj.expiry_date
        months = int(request.data.get("months") or 12)
        base = old_expiry or timezone.localdate()
        import calendar
        month_index = base.month - 1 + months
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        license_obj.expiry_date = base.replace(year=year, month=month, day=min(base.day, calendar.monthrange(year, month)[1]))
        license_obj.status = ProductLicense.ACTIVE
        license_obj.save(update_fields=["expiry_date", "status", "updated_at"])
        record_license_audit(license_obj, "LICENSE_EXTENDED", old_value={"expiry_date": old_expiry},
                             new_value={"expiry_date": license_obj.expiry_date}, ip_address=client_ip(request), created_by=request.user)
        return Response(ProductLicenseSerializer(license_obj).data)


class ProductLicenseStatusView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, license_id, action):
        license_obj = get_object_or_404(ProductLicense, pk=license_id)
        status_map = {"suspend": ProductLicense.SUSPENDED, "revoke": ProductLicense.REVOKED, "reactivate": ProductLicense.ACTIVE}
        event_map = {"suspend": "LICENSE_SUSPENDED", "revoke": "LICENSE_REVOKED", "reactivate": "LICENSE_REACTIVATED"}
        if action not in status_map:
            return Response({"detail": "Unknown license action."}, status=400)
        if action == "reactivate" and (not license_obj.expiry_date or license_obj.expiry_date < timezone.localdate()):
            return Response({"detail": "License expired. Extend the expiry date before reactivation."}, status=400)
        old_status = license_obj.status
        license_obj.status = status_map[action]
        license_obj.save(update_fields=["status", "updated_at"])
        record_license_audit(license_obj, event_map[action], old_value={"status": old_status},
                             new_value={"status": license_obj.status}, ip_address=client_ip(request), created_by=request.user)
        return Response(ProductLicenseSerializer(license_obj).data)


class ProductLicenseChangeTallySerialView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, license_id):
        license_obj = get_object_or_404(ProductLicense, pk=license_id)
        new_serial = str(request.data.get("new_serial", "")).strip()
        reason = str(request.data.get("reason", "")).strip()
        if not new_serial:
            return Response({"detail": "new_serial is required."}, status=400)
        if not reason:
            return Response({"detail": "reason is required."}, status=400)
        old_serial = license_obj.licensed_tally_serial
        license_obj.licensed_tally_serial = new_serial
        license_obj.save(update_fields=["licensed_tally_serial", "updated_at"])
        record_license_audit(
            license_obj, "TALLY_SERIAL_CHANGED",
            old_value={"licensed_tally_serial": old_serial},
            new_value={"licensed_tally_serial": new_serial, "reason": reason},
            ip_address=client_ip(request),
            created_by=request.user,
        )
        return Response(ProductLicenseSerializer(license_obj).data)


class ProductLicenseDeviceRevokeView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, license_id, device_id):
        license_obj = get_object_or_404(ProductLicense, pk=license_id)
        device = get_object_or_404(LicensedDevice, pk=device_id, license=license_obj)
        old_status = device.status
        device.status = LicensedDevice.REVOKED
        device.save(update_fields=["status", "updated_at"])
        record_license_audit(license_obj, "DEVICE_REVOKED", old_value={"status": old_status, "device_fingerprint": device.device_fingerprint},
                             new_value={"status": device.status}, device_fingerprint=device.device_fingerprint,
                             ip_address=client_ip(request), created_by=request.user)
        return Response(LicensedDeviceSerializer(device).data)


class DeviceActivationRequestListView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = DeviceActivationRequest.objects.select_related("license__customer", "old_device").order_by("-requested_at")
        return Response(paginate(request, qs, DeviceActivationRequestSerializer))


class DeviceActivationRequestActionView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, request_id, action):
        device_request = get_object_or_404(DeviceActivationRequest.objects.select_related("license", "old_device"), pk=request_id)
        reason = str(request.data.get("reason", "")).strip()
        if action == "reject":
            device_request.status = DeviceActivationRequest.REJECTED
            device_request.admin_reason = reason
            device_request.approved_at = timezone.now()
            device_request.approved_by = request.user
            device_request.save(update_fields=["status", "admin_reason", "approved_at", "approved_by"])
            return Response(DeviceActivationRequestSerializer(device_request).data)

        if action == "approve":
            active_count = LicensedDevice.objects.filter(license=device_request.license, status=LicensedDevice.ACTIVE).count()
            if active_count >= device_request.license.allowed_devices:
                return Response({"detail": "No device slot is available. Use replace instead."}, status=400)
        elif action == "replace":
            if device_request.old_device:
                device_request.old_device.status = LicensedDevice.REPLACED
                device_request.old_device.save(update_fields=["status", "updated_at"])
        else:
            return Response({"detail": "Unknown device request action."}, status=400)

        device, _ = LicensedDevice.objects.update_or_create(
            license=device_request.license,
            device_fingerprint=device_request.requested_device_fingerprint,
            defaults={
                "device_name": device_request.requested_device_name,
                "windows_version": device_request.windows_version,
                "app_version": device_request.app_version,
                "status": LicensedDevice.ACTIVE,
                "last_seen": timezone.now(),
            },
        )
        device_request.status = DeviceActivationRequest.APPROVED
        device_request.admin_reason = reason
        device_request.approved_at = timezone.now()
        device_request.approved_by = request.user
        device_request.save(update_fields=["status", "admin_reason", "approved_at", "approved_by"])
        event = "DEVICE_REPLACED" if action == "replace" else "DEVICE_REGISTERED"
        record_license_audit(device_request.license, event,
                             old_value={"device_fingerprint": device_request.old_device.device_fingerprint if device_request.old_device else ""},
                             new_value={"device_fingerprint": device.device_fingerprint, "reason": reason},
                             device_fingerprint=device.device_fingerprint,
                             detected_tally_serial=device_request.detected_tally_serial,
                             current_company_gstin=device_request.current_company_gstin,
                             ip_address=client_ip(request), created_by=request.user)
        return Response(DeviceActivationRequestSerializer(device_request).data)


# -------------------------------------------------------------- Settings --

class SuperAdminSettingsView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response(SuperAdminSettingsSerializer(settings_service.get_settings()).data)

    def patch(self, request):
        settings_row = settings_service.update_settings(**request.data)
        log_admin_action(request.user, "SETTINGS_UPDATED", "SuperAdminSettings", 1, new_value=request.data)
        return Response(SuperAdminSettingsSerializer(settings_row).data)


def _sandbox_config_response(configuration):
    data = safe_status(configuration)
    data.update({"api_version": configuration.api_version if configuration else "1.0.0",
                 "api_key_masked": "" if not configuration else "••••••••"})
    return data


class SandboxConfigurationView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response(_sandbox_config_response(SandboxAPIConfiguration.objects.filter(provider="sandbox", is_active=True).first()))

    def post(self, request):
        configuration = SandboxAPIConfiguration.objects.filter(provider="sandbox", is_active=True).first()
        api_key = str(request.data.get("api_key") or "").strip()
        api_secret = str(request.data.get("api_secret") or "")
        environment = str(request.data.get("environment") or "test").strip().lower()
        api_version = str(request.data.get("api_version") or "1.0.0").strip()
        # A retry deliberately uses the encrypted active credentials on the
        # server. Secrets are never sent back to the browser just to retry.
        if configuration and not api_key and not api_secret:
            from .services.sandbox_configuration import provider_config
            saved = provider_config(configuration) or {}
            api_key, api_secret = saved.get("api_key", ""), saved.get("api_secret", "")
        if not api_key or not api_secret:
            return Response({"detail": "API Key and API Secret are required."}, status=400)
        from gst_tally.services.gst_lookup.providers.sandbox import SandboxGSTProvider, SandboxAuthenticationFailure
        try:
            provider = SandboxGSTProvider({"base_url": settings.SANDBOX_BASE_URL, "api_key": api_key, "api_secret": api_secret,
                                           "api_version": api_version, "timeout": settings.GST_LOOKUP_TIMEOUT,
                                           "access_ttl": settings.SANDBOX_ACCESS_TOKEN_TTL, "session_ttl": settings.SANDBOX_TAXPAYER_SESSION_TTL,
                                           "auth_failure_cooldown": settings.SANDBOX_AUTH_FAILURE_COOLDOWN})
            provider.authenticate(force=True, bypass_block=True)
        except SandboxAuthenticationFailure as exc:
            return Response({"authenticated": False, "code": "SANDBOX_AUTHENTICATION_FAILED", "detail": exc.safe_message}, status=502)
        except Exception:
            return Response({"authenticated": False, "code": "SANDBOX_AUTHENTICATION_FAILED", "detail": "Sandbox authentication failed."}, status=502)
        clear_cached_access_token()
        return Response({"authenticated": True, "lookup_ready": True, "message": "Sandbox connected."})

    def put(self, request):
        existing = SandboxAPIConfiguration.objects.filter(provider="sandbox", is_active=True).first()
        api_key = str(request.data.get("api_key") or "").strip()
        api_secret = str(request.data.get("api_secret") or "")
        environment = str(request.data.get("environment") or "test").strip().lower()
        api_version = str(request.data.get("api_version") or "1.0.0").strip()
        # Empty edit fields mean keep the encrypted active values; they must
        # never overwrite a working secret with an empty string.
        if existing and (not api_key or not api_secret):
            from .services.sandbox_configuration import provider_config
            saved = provider_config(existing) or {}
            api_key = api_key or saved.get("api_key", "")
            api_secret = api_secret or saved.get("api_secret", "")
        if not api_key or not api_secret:
            return Response({"detail": "Test the new API Key and API Secret before saving."}, status=400)
        from gst_tally.services.gst_lookup.providers.sandbox import SandboxGSTProvider, SandboxAuthenticationFailure
        try:
            provider = SandboxGSTProvider({"base_url": settings.SANDBOX_BASE_URL, "api_key": api_key, "api_secret": api_secret,
                                           "api_version": api_version, "timeout": settings.GST_LOOKUP_TIMEOUT,
                                           "access_ttl": settings.SANDBOX_ACCESS_TOKEN_TTL, "session_ttl": settings.SANDBOX_TAXPAYER_SESSION_TTL,
                                           "auth_failure_cooldown": settings.SANDBOX_AUTH_FAILURE_COOLDOWN})
            provider.authenticate(force=True, bypass_block=True)
        except SandboxAuthenticationFailure as exc:
            return Response({"authenticated": False, "code": "SANDBOX_AUTHENTICATION_FAILED", "detail": exc.safe_message}, status=502)
        except Exception:
            return Response({"authenticated": False, "code": "SANDBOX_AUTHENTICATION_FAILED", "detail": "Sandbox authentication failed."}, status=502)
        configuration, _ = SandboxAPIConfiguration.objects.update_or_create(
            provider="sandbox", environment=environment,
            defaults={"api_key_encrypted": encrypt(api_key), "api_secret_encrypted": encrypt(api_secret),
                      "api_version": api_version, "is_active": True, "last_verified_at": timezone.now(),
                      "last_error": "", "created_by": existing.created_by if existing else request.user,
                      "updated_by": request.user})
        SandboxAPIConfiguration.objects.filter(provider="sandbox").exclude(pk=configuration.pk).update(is_active=False)
        clear_cached_access_token()
        log_admin_action(request.user, "SANDBOX_CONFIGURATION_UPDATED", "SandboxAPIConfiguration", configuration.pk,
                         old_value={"environment": existing.environment} if existing else None,
                         new_value={"provider": "sandbox", "environment": environment, "result": "SUCCESS"}, ip_address=client_ip(request))
        return Response(_sandbox_config_response(configuration))


class CustomerSupportContactView(APIView):
    """The only slice of SuperAdminSettings a customer may ever read -- the
    configured support phone/email shown on the GSTR 2 Tally customer
    Profile page's "Need Help?" section. Any authenticated customer, not
    just Super Admin accounts."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        settings_row = settings_service.get_settings()
        return Response({"support_email": settings_row.support_email, "support_phone": settings_row.support_phone})


# --------------------------------------------------------------- Reports --

class ReportExportView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, kind):
        try:
            return reports_service.export_report(kind)
        except ValueError:
            return Response({"detail": "Unknown report kind."}, status=400)
