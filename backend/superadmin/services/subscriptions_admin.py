from subscriptions.models import Subscription

from ..models import CustomerEntitlement, SubscriptionPlan
from .audit import log_admin_action


def renew_subscription(user, performed_by, period_months=None, period_years=None,
                        custom_expiry_date=None, amount=None, payment_status=None, notes=""):
    subscription, _ = Subscription.objects.get_or_create(user=user)
    old_expiry = subscription.expiry_date
    kwargs = {"performed_by": performed_by, "amount": amount, "payment_status": payment_status, "notes": notes}
    if custom_expiry_date is not None:
        kwargs["custom_expiry_date"] = custom_expiry_date
    elif period_months is not None:
        kwargs["period_months"] = period_months
    else:
        kwargs["period_years"] = period_years or 1
    subscription.renew(**kwargs)
    log_admin_action(performed_by, "SUBSCRIPTION_RENEWED", "Subscription", subscription.id, customer=user,
                      old_value={"expiry_date": old_expiry.isoformat() if old_expiry else None},
                      new_value={"expiry_date": subscription.expiry_date.isoformat()}, reason=notes)
    return subscription


def change_plan(user, plan_code, performed_by, reason=""):
    """Spec section 36: updates the customer's effective plan + limits.
    `subscriptions.Subscription.plan` is a plain label field -- set to the
    new plan's code for display consistency -- while the actual
    company/device limits live on `CustomerEntitlement` (this app), never
    silently dropping the customer's existing registered companies/devices
    even if the new plan's limit is lower (spec section 21: used > allowed
    is prevented at the point new companies/devices are added, not by
    deleting existing ones)."""
    plan = SubscriptionPlan.objects.get(code=plan_code)
    subscription, _ = Subscription.objects.get_or_create(user=user)
    entitlement, _ = CustomerEntitlement.objects.get_or_create(user=user)

    old_plan = subscription.plan
    old_limits = {"company_limit": entitlement.company_limit, "device_limit": entitlement.device_limit}
    subscription.plan = plan.code
    subscription.save(update_fields=["plan", "updated_at"])
    entitlement.plan = plan
    entitlement.company_limit = plan.company_limit
    entitlement.device_limit = plan.device_limit
    entitlement.save(update_fields=["plan", "company_limit", "device_limit", "updated_at"])

    log_admin_action(performed_by, "PLAN_CHANGED", "Subscription", subscription.id, customer=user,
                      old_value={"plan": old_plan, **old_limits},
                      new_value={"plan": plan.code, "company_limit": plan.company_limit, "device_limit": plan.device_limit},
                      reason=reason)
    return subscription, entitlement
