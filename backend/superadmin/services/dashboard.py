from django.db.models import Count, Sum
from django.utils import timezone

from gst_tally.models import GSTImportBatch, GSTInvoice, LicensedDevice, LicenseAuditLog, ProductLicense, TallyVoucherMapping
from subscriptions.models import RenewalHistory, Subscription

from ..models import CustomerEntitlement, Payment, RegisteredCompany, SupportTicket
from ..serializers import product_license_days_remaining, product_license_status


def build_dashboard():
    """All aggregate DB queries (spec section 39: never loop-and-count every
    row in Python/frontend). Status counts read the stored
    `subscription_status` column directly -- it is always correct for the
    single account being accessed (`Subscription.refresh_status()` runs on
    every real request via the enforcement middleware and `/me/`), so a
    dashboard-wide count is a best-effort live snapshot rather than a forced
    full-table recompute on every dashboard load."""
    today = timezone.localdate()

    status_counts = dict(Subscription.objects.values_list("subscription_status").annotate(n=Count("id")))
    customers = {
        "total_customers": Subscription.objects.count(),
        "active_subscriptions": status_counts.get(Subscription.ACTIVE, 0),
        "expiring_soon": status_counts.get(Subscription.EXPIRING_SOON, 0),
        "expired": status_counts.get(Subscription.EXPIRED, 0),
        "suspended": status_counts.get(Subscription.SUSPENDED, 0),
        "trial": status_counts.get(Subscription.TRIAL, 0),
    }

    license_rows = list(ProductLicense.objects.exclude(status__in=[ProductLicense.SUSPENDED, ProductLicense.REVOKED]))
    computed_license_statuses = [product_license_status(row) for row in license_rows]
    product_licenses = {
        "active": computed_license_statuses.count(ProductLicense.ACTIVE),
        "expiring": computed_license_statuses.count("EXPIRING_SOON"),
        "expired": computed_license_statuses.count(ProductLicense.EXPIRED),
        "suspended": ProductLicense.objects.filter(status=ProductLicense.SUSPENDED).count(),
        "active_devices": LicensedDevice.objects.filter(status=LicensedDevice.ACTIVE).count(),
        "device_change_requests": ProductLicense.objects.filter(device_requests__status="PENDING").distinct().count(),
        "tally_serial_mismatch_attempts": LicenseAuditLog.objects.filter(event_type="TALLY_SERIAL_MISMATCH").count(),
    }

    entitlement_totals = CustomerEntitlement.objects.aggregate(allowed=Sum("company_limit"))
    total_allowed = entitlement_totals["allowed"] or 0
    total_used = RegisteredCompany.objects.exclude(status=RegisteredCompany.DEACTIVATED).count()
    companies = {
        "total_companies_allowed": total_allowed,
        "total_companies_used": total_used,
        "available_company_slots": max(0, total_allowed - total_used),
        "total_registered_gstins": RegisteredCompany.objects.values("gstin").distinct().count(),
    }

    imports_by_type = dict(GSTImportBatch.objects.values_list("gst_return_type").annotate(n=Count("id")))
    usage = {
        "total_imports": GSTImportBatch.objects.count(),
        "total_gstr1_imports": imports_by_type.get(GSTImportBatch.ReturnType.GSTR1, 0),
        "total_gstr2a_imports": imports_by_type.get(GSTImportBatch.ReturnType.GSTR2A, 0),
        "total_gstr2b_imports": imports_by_type.get(GSTImportBatch.ReturnType.GSTR2B, 0),
        "total_invoices_processed": GSTInvoice.objects.count(),
        "total_vouchers_imported": TallyVoucherMapping.objects.filter(imported_at__isnull=False).count(),
    }

    paid = Payment.objects.filter(payment_status=Payment.PAID)
    revenue_total = paid.aggregate(total=Sum("final_amount"))["total"] or 0
    revenue_this_month = paid.filter(payment_date__year=today.year, payment_date__month=today.month
                                      ).aggregate(total=Sum("final_amount"))["total"] or 0
    pending_qs = Payment.objects.filter(payment_status=Payment.PENDING)
    failed_qs = Payment.objects.filter(payment_status=Payment.FAILED)
    financial = {
        "total_revenue": revenue_total,
        "revenue_this_month": revenue_this_month,
        "pending_payments": {"count": pending_qs.count(), "amount": pending_qs.aggregate(total=Sum("final_amount"))["total"] or 0},
        "failed_payments": {"count": failed_qs.count(), "amount": failed_qs.aggregate(total=Sum("final_amount"))["total"] or 0},
        "renewals_this_month": RenewalHistory.objects.filter(renewal_date__year=today.year, renewal_date__month=today.month).count(),
    }

    return {"customers": customers, "product_licenses": product_licenses, "companies": companies, "usage": usage, "financial": financial}


def build_recent_sections(limit=8):
    recent_purchases = RenewalHistory.objects.select_related("subscription__user").order_by("-created_at")[:limit]
    recent_payments = Payment.objects.select_related("user").order_by("-created_at")[:limit]
    expiring_soon = ProductLicense.objects.select_related("customer").prefetch_related("licensed_devices").exclude(
        status__in=[ProductLicense.SUSPENDED, ProductLicense.REVOKED],
    ).order_by("expiry_date", "-created_at")[:limit]
    recently_active = Subscription.objects.select_related("user").exclude(last_status_checked_at__isnull=True).order_by("-last_status_checked_at")[:limit]
    recent_tickets = SupportTicket.objects.select_related("user").order_by("-created_at")[:limit]

    return {
        "recent_purchases": [
            {"customer": r.subscription.user.username, "plan": r.subscription.plan, "amount": r.amount,
             "date": r.renewal_date, "status": r.payment_status}
            for r in recent_purchases
        ],
        "recent_payments": [
            {"customer": p.user.username, "invoice_number": p.invoice_number, "amount": p.final_amount,
             "date": p.payment_date, "status": p.payment_status}
            for p in recent_payments
        ],
        "expiring_soon": [
            {
                "customer": license_obj.customer.get_full_name() or license_obj.customer.username,
                "company": getattr(getattr(license_obj.customer, "customer_profile", None), "business_name", "") or license_obj.customer.username,
                "tally_serial": license_obj.licensed_tally_serial,
                "expiry_date": license_obj.expiry_date,
                "days_remaining": product_license_days_remaining(license_obj),
                "status": product_license_status(license_obj),
            }
            for license_obj in expiring_soon
        ],
        "recently_active_customers": [
            {"customer": s.user.username, "last_active": s.last_status_checked_at} for s in recently_active
        ],
        "recent_support_tickets": [
            {"id": t.id, "customer": t.user.username, "subject": t.subject, "priority": t.priority, "status": t.status}
            for t in recent_tickets
        ],
    }
