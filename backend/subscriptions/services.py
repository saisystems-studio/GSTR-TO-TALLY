from collections import namedtuple

from django.utils import timezone

from .models import Subscription

Block = namedtuple("Block", ["code", "detail", "expiry_date"])


def get_or_create_subscription(user):
    """Used by the customer-facing `/me/` endpoint only -- NOT by the
    enforcement middleware. Creates a plain TRIAL row (no dates, not
    activated) the first time a user's own status is ever checked, purely so
    the account shows up consistently in `/me/` responses and the Super
    Admin list. This never grants or revokes access by itself: a bare TRIAL
    row is not in the middleware's blocked-status set, so creating it here
    is side-effect-free with respect to what the user can do.
    """
    subscription, _created = Subscription.objects.get_or_create(user=user)
    return subscription


def check_request_block(user):
    """The enforcement middleware's only decision point (spec sections 4, 6,
    22). Deliberately does NOT auto-create a Subscription row: an account
    with no row at all has never been brought under subscription control
    (e.g. every pre-existing customer at the time this feature shipped) and
    must never be blocked by its mere absence -- that would silently lock
    out real, paying customers who were never given an activation. Only an
    explicit Super Admin activation (or a real purchase flow, later) starts
    enforcement for an account.

    Returns None to allow the request through, or a `Block` describing the
    exact JSON body + reason to return.
    """
    try:
        subscription = Subscription.objects.get(user=user)
    except Subscription.DoesNotExist:
        return None
    status = subscription.refresh_status()
    if status == Subscription.SUSPENDED:
        return Block("SUBSCRIPTION_SUSPENDED", "Your GSTR 2 Tally subscription has been suspended.", subscription.expiry_date)
    if status == Subscription.EXPIRED:
        return Block("SUBSCRIPTION_EXPIRED", "Your GSTR 2 Tally subscription has expired.", subscription.expiry_date)
    return None


def serialize_status(subscription):
    today = timezone.localdate()
    status = subscription.refresh_status(today=today)
    return {
        "is_activated": subscription.is_activated,
        "plan": subscription.plan,
        "purchase_date": subscription.purchase_date.isoformat() if subscription.purchase_date else None,
        "activation_date": subscription.activation_date.isoformat() if subscription.activation_date else None,
        "expiry_date": subscription.expiry_date.isoformat() if subscription.expiry_date else None,
        "days_remaining": subscription.days_remaining(today),
        "subscription_status": status,
        "is_expired": status == Subscription.EXPIRED,
        "is_suspended": subscription.is_suspended,
    }
