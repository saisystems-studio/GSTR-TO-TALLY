from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Subscription
from .serializers import AdminSubscriptionSerializer, RenewalHistorySerializer, SubscriptionAuditLogSerializer
from .services import get_or_create_subscription, serialize_status

User = get_user_model()


class MySubscriptionView(APIView):
    """GET /api/subscriptions/me/ -- the exact response shape from spec
    section 19. Never accepts writes here: activation_date/expiry_date/
    subscription_status can only change via the admin-only actions below
    (spec section 33) -- a normal customer can read their own status but
    never edit it through this or any other endpoint.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        subscription = get_or_create_subscription(request.user)
        from superadmin.services.customers import touch_last_active
        touch_last_active(request.user)
        return Response(serialize_status(subscription))


class AdminSubscriptionListView(APIView):
    """GET /api/subscriptions/admin/ -- Super Admin dashboard (spec section
    13): every customer's current subscription plus live counts by status,
    always derived from real dates, never a hardcoded/cached number."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        qs = Subscription.objects.select_related("user")
        if search:
            qs = qs.filter(Q(user__username__icontains=search) | Q(user__email__icontains=search))
        rows = list(qs)
        for subscription in rows:
            subscription.refresh_status()
        counts = {"active": 0, "expiring_soon": 0, "expired": 0, "trial": 0, "suspended": 0}
        key_by_status = {
            Subscription.ACTIVE: "active", Subscription.EXPIRING_SOON: "expiring_soon",
            Subscription.EXPIRED: "expired", Subscription.TRIAL: "trial", Subscription.SUSPENDED: "suspended",
        }
        for subscription in rows:
            counts[key_by_status[subscription.subscription_status]] += 1
        return Response({"results": AdminSubscriptionSerializer(rows, many=True).data, "counts": counts, "total": len(rows)})


class AdminSubscriptionDetailView(APIView):
    """GET /api/subscriptions/admin/<user_id>/ -- one customer's full
    profile (spec section 12) plus renewal history and audit trail."""
    permission_classes = [IsAdminUser]

    def get(self, request, user_id):
        subscription = get_object_or_404(Subscription.objects.select_related("user"), user_id=user_id)
        subscription.refresh_status()
        return Response({
            "subscription": AdminSubscriptionSerializer(subscription).data,
            "renewal_history": RenewalHistorySerializer(subscription.renewal_history.all(), many=True).data,
            "audit_log": SubscriptionAuditLogSerializer(subscription.audit_log.all()[:50], many=True).data,
        })


class AdminActivateView(APIView):
    """POST /api/subscriptions/admin/<user_id>/activate/ -- the only
    activation trigger (spec section 11/33): a Super Admin confirming a
    successful purchase. Idempotent -- see Subscription.activate()."""
    permission_classes = [IsAdminUser]

    def post(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        subscription, _created = Subscription.objects.get_or_create(user=user)
        was_activated = subscription.is_activated
        subscription.activate(performed_by=request.user, purchase_date=request.data.get("purchase_date") or None)
        return Response({
            "already_activated": was_activated,
            "subscription": AdminSubscriptionSerializer(subscription).data,
        })


class AdminRenewView(APIView):
    """POST /api/subscriptions/admin/<user_id>/renew/ -- spec sections 14/15."""
    permission_classes = [IsAdminUser]

    def post(self, request, user_id):
        subscription = get_object_or_404(Subscription, user_id=user_id)
        subscription.renew(
            performed_by=request.user,
            amount=request.data.get("amount") or None,
            payment_status=request.data.get("payment_status") or "",
        )
        return Response({"subscription": AdminSubscriptionSerializer(subscription).data})


class AdminSuspendView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, user_id):
        subscription = get_object_or_404(Subscription, user_id=user_id)
        subscription.suspend(performed_by=request.user)
        return Response({"subscription": AdminSubscriptionSerializer(subscription).data})


class AdminReactivateView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, user_id):
        subscription = get_object_or_404(Subscription, user_id=user_id)
        subscription.reactivate(performed_by=request.user)
        return Response({"subscription": AdminSubscriptionSerializer(subscription).data})
