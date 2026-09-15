from django.contrib import admin, messages

from .models import RenewalHistory, Subscription, SubscriptionAuditLog


class RenewalHistoryInline(admin.TabularInline):
    model = RenewalHistory
    extra = 0
    can_delete = False
    readonly_fields = ("previous_activation_date", "previous_expiry_date", "renewal_date",
                        "new_expiry_date", "renewal_period", "amount", "payment_status", "changed_by", "created_at")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


class SubscriptionAuditLogInline(admin.TabularInline):
    model = SubscriptionAuditLog
    extra = 0
    can_delete = False
    readonly_fields = ("action", "old_expiry", "new_expiry", "performed_by", "created_at")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    # Super Admin customer profile + dashboard (spec sections 12-14): list
    # view already gives the Active/Expiring Soon/Expired counts for free
    # via list_filter's own per-value counts, and every column here is
    # computed live off real dates -- nothing hardcoded.
    list_display = ("user", "plan", "computed_status", "purchase_date", "activation_date",
                     "expiry_date", "days_remaining_display", "is_activated", "is_suspended")
    list_filter = ("subscription_status", "is_activated", "is_suspended", "plan")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("is_activated", "activation_date", "expiry_date", "activated_at", "expired_at",
                       "subscription_status", "last_status_checked_at", "created_at", "updated_at")
    fields = ("user", "plan", "allowed_products", "purchase_date", "is_activated", "activation_date", "expiry_date",
              "subscription_status", "is_suspended", "activated_at", "expired_at",
              "last_status_checked_at", "created_at", "updated_at")
    inlines = [RenewalHistoryInline, SubscriptionAuditLogInline]
    actions = ["action_activate", "action_renew_one_year", "action_suspend", "action_reactivate"]

    @admin.display(description="Status")
    def computed_status(self, obj):
        return obj.refresh_status()

    @admin.display(description="Days Remaining")
    def days_remaining_display(self, obj):
        return obj.days_remaining()

    # Every action below is an explicit Super Admin decision (spec section
    # 33: only system/Super Admin may change these fields) and is fully
    # idempotent/audited via the model methods themselves, never by editing
    # activation_date/expiry_date directly in this admin.
    @admin.action(description="Activate selected (first activation only, idempotent)")
    def action_activate(self, request, queryset):
        activated = 0
        for subscription in queryset:
            if not subscription.is_activated:
                subscription.activate(performed_by=request.user)
                activated += 1
        self.message_user(request, f"Activated {activated} subscription(s). Already-active rows were left unchanged.", messages.SUCCESS)

    @admin.action(description="Renew +1 year (extends current expiry if still active)")
    def action_renew_one_year(self, request, queryset):
        for subscription in queryset:
            subscription.renew(performed_by=request.user)
        self.message_user(request, f"Renewed {queryset.count()} subscription(s) by 1 year.", messages.SUCCESS)

    @admin.action(description="Suspend selected")
    def action_suspend(self, request, queryset):
        for subscription in queryset:
            subscription.suspend(performed_by=request.user)
        self.message_user(request, f"Suspended {queryset.count()} subscription(s).", messages.WARNING)

    @admin.action(description="Reactivate selected (clears suspension)")
    def action_reactivate(self, request, queryset):
        for subscription in queryset:
            subscription.reactivate(performed_by=request.user)
        self.message_user(request, f"Reactivated {queryset.count()} subscription(s).", messages.SUCCESS)


@admin.register(RenewalHistory)
class RenewalHistoryAdmin(admin.ModelAdmin):
    list_display = ("subscription", "renewal_date", "previous_expiry_date", "new_expiry_date",
                     "renewal_period", "amount", "payment_status", "changed_by", "created_at")
    list_filter = ("payment_status",)
    search_fields = ("subscription__user__username", "subscription__user__email")
    readonly_fields = [f.name for f in RenewalHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SubscriptionAuditLog)
class SubscriptionAuditLogAdmin(admin.ModelAdmin):
    list_display = ("subscription", "action", "old_expiry", "new_expiry", "performed_by", "created_at")
    list_filter = ("action",)
    search_fields = ("subscription__user__username", "subscription__user__email")
    readonly_fields = [f.name for f in SubscriptionAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
