from django.contrib import admin

from .models import (AdminAuditLog, CompanyLimitHistory, CustomerEntitlement, CustomerNote, CustomerProfile,
                      Payment, PasswordResetToken, RegisteredCompany, SubscriptionPlan, SuperAdminProfile,
                      SuperAdminSettings, SupportTicket)


@admin.register(SuperAdminProfile)
class SuperAdminProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "phone", "must_change_password", "password_changed_at")
    search_fields = ("user__username", "user__email")


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "business_name", "city", "state", "last_active_at")
    search_fields = ("user__username", "user__email", "business_name")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "price", "billing_cycle", "company_limit", "device_limit", "is_active")
    list_filter = ("is_active", "billing_cycle")
    search_fields = ("name", "code")


@admin.register(CustomerEntitlement)
class CustomerEntitlementAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "company_limit", "device_limit")
    search_fields = ("user__username",)


@admin.register(RegisteredCompany)
class RegisteredCompanyAdmin(admin.ModelAdmin):
    list_display = ("user", "gstin", "company_name", "status", "first_activated", "last_connected")
    list_filter = ("status",)
    search_fields = ("gstin", "company_name", "user__username")


@admin.register(CompanyLimitHistory)
class CompanyLimitHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "old_limit", "new_limit", "changed_by", "created_at")
    search_fields = ("user__username",)
    readonly_fields = [f.name for f in CompanyLimitHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "user", "plan", "final_amount", "payment_status", "payment_date")
    list_filter = ("payment_status",)
    search_fields = ("invoice_number", "user__username", "transaction_id")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "subject", "priority", "status", "assigned_to", "created_at")
    list_filter = ("status", "priority")
    search_fields = ("subject", "user__username")


@admin.register(CustomerNote)
class CustomerNoteAdmin(admin.ModelAdmin):
    list_display = ("user", "created_by", "created_at")
    search_fields = ("user__username",)


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "entity_type", "entity_id", "customer", "admin_user", "created_at")
    list_filter = ("action", "entity_type")
    search_fields = ("customer__username", "admin_user__username", "entity_id")
    readonly_fields = [f.name for f in AdminAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "expires_at", "used_at", "created_at")
    readonly_fields = [f.name for f in PasswordResetToken._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(SuperAdminSettings)
class SuperAdminSettingsAdmin(admin.ModelAdmin):
    list_display = ("application_name", "support_email", "maintenance_mode", "updated_at")

    def has_add_permission(self, request):
        return not SuperAdminSettings.objects.exists()
