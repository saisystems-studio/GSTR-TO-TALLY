from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from subscriptions.models import Subscription

from ..models import CompanyLimitHistory, CustomerEntitlement, CustomerProfile, RegisteredCompany
from .audit import log_admin_action

User = get_user_model()

STATUS_FILTER_MAP = {
    "active": Subscription.ACTIVE, "expiring_soon": Subscription.EXPIRING_SOON,
    "expired": Subscription.EXPIRED, "suspended": Subscription.SUSPENDED, "trial": Subscription.TRIAL,
}


def customer_queryset(search="", status=None, plan=None, state=None):
    """Server-side search/filter (spec sections 12/13) -- always a DB query,
    never a frontend loop over an entire loaded list."""
    qs = Subscription.objects.select_related("user", "user__customer_profile", "user__gst_profile")
    if search:
        qs = qs.filter(
            Q(user__username__icontains=search) | Q(user__email__icontains=search)
            | Q(user__customer_profile__business_name__icontains=search)
            | Q(user__gst_profile__phone_number__icontains=search)
            | Q(user__registered_companies__gstin__icontains=search)
        ).distinct()
    if status and status in STATUS_FILTER_MAP:
        qs = qs.filter(subscription_status=STATUS_FILTER_MAP[status])
    if plan:
        qs = qs.filter(plan=plan)
    if state:
        qs = qs.filter(user__customer_profile__state__icontains=state)
    return qs


def get_or_create_customer_profile(user):
    profile, _ = CustomerProfile.objects.get_or_create(user=user)
    return profile


def get_or_create_entitlement(user):
    entitlement, created = CustomerEntitlement.objects.get_or_create(user=user)
    return entitlement


def suspend_customer(user, performed_by, reason=""):
    subscription, _ = Subscription.objects.get_or_create(user=user)
    subscription.suspend(performed_by=performed_by)
    log_admin_action(performed_by, "CUSTOMER_SUSPENDED", "Customer", user.id, customer=user, reason=reason)
    return subscription


def reactivate_customer(user, performed_by, reason=""):
    subscription, _ = Subscription.objects.get_or_create(user=user)
    subscription.reactivate(performed_by=performed_by)
    log_admin_action(performed_by, "CUSTOMER_REACTIVATED", "Customer", user.id, customer=user, reason=reason)
    return subscription


def change_company_limit(user, new_limit, performed_by, reason=""):
    entitlement = get_or_create_entitlement(user)
    old_limit = entitlement.company_limit
    entitlement.company_limit = new_limit
    entitlement.save(update_fields=["company_limit", "updated_at"])
    CompanyLimitHistory.objects.create(user=user, old_limit=old_limit, new_limit=new_limit, changed_by=performed_by, reason=reason)
    log_admin_action(performed_by, "COMPANY_LIMIT_CHANGED", "CustomerEntitlement", entitlement.id, customer=user,
                      old_value={"company_limit": old_limit}, new_value={"company_limit": new_limit}, reason=reason)
    return entitlement


def touch_last_active(user):
    """Best-effort 'Last Active' update -- called from the customer-facing
    subscription `/me/` read path only via a thin hook, never from any
    accounting/import/Tally code path."""
    CustomerProfile.objects.filter(user=user).update(last_active_at=timezone.now())
