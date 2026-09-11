from rest_framework import serializers

from .models import RenewalHistory, Subscription, SubscriptionAuditLog


class RenewalHistorySerializer(serializers.ModelSerializer):
    changed_by = serializers.SerializerMethodField()

    class Meta:
        model = RenewalHistory
        fields = ["id", "previous_activation_date", "previous_expiry_date", "renewal_date",
                  "new_expiry_date", "renewal_period", "amount", "payment_status", "changed_by", "created_at"]

    def get_changed_by(self, obj):
        return obj.changed_by.get_username() if obj.changed_by else "system"


class SubscriptionAuditLogSerializer(serializers.ModelSerializer):
    performed_by = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionAuditLog
        fields = ["id", "action", "old_expiry", "new_expiry", "performed_by", "created_at"]

    def get_performed_by(self, obj):
        return obj.performed_by.get_username() if obj.performed_by else "system"


class AdminSubscriptionSerializer(serializers.ModelSerializer):
    """Super Admin customer-profile view (spec section 12)."""

    username = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = ["id", "username", "email", "plan", "purchase_date", "activation_date", "expiry_date",
                  "days_remaining", "status", "is_activated", "is_suspended", "activated_at", "expired_at"]

    def get_username(self, obj):
        return obj.user.get_username()

    def get_email(self, obj):
        return obj.user.email

    def get_days_remaining(self, obj):
        return obj.days_remaining()

    def get_status(self, obj):
        return obj.refresh_status()
