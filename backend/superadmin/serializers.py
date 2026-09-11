from rest_framework import serializers
from django.utils import timezone

from gst_tally.models import (
    CompanyDetails,
    DeviceActivation,
    DeviceActivationRequest,
    LicensedDevice,
    LicenseAuditLog,
    ProductLicense,
    TallyCompanyMapping,
)
from gst_tally.services.product_license import (
    product_license_activation_key,
    product_license_days_remaining,
    product_license_status,
)
from subscriptions.models import RenewalHistory, Subscription, SubscriptionAuditLog

from .models import (AdminAuditLog, CompanyLimitHistory, CustomerNote, CustomerProfile,
                      CustomerEntitlement, Payment, RegisteredCompany, SubscriptionPlan,
                      SuperAdminProfile, SuperAdminSettings, SupportTicket)


class SuperAdminProfileSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email")
    role = serializers.SerializerMethodField()
    account_status = serializers.SerializerMethodField()
    account_created = serializers.DateTimeField(source="user.date_joined", read_only=True)
    last_login = serializers.DateTimeField(source="user.last_login", read_only=True)

    class Meta:
        model = SuperAdminProfile
        fields = ["id", "name", "display_name", "email", "phone", "role", "account_status",
                  "account_created", "last_login", "password_changed_at", "must_change_password"]

    def get_name(self, obj):
        return obj.display_name or obj.user.get_full_name() or obj.user.username

    def get_role(self, obj):
        return "SUPER_ADMIN"

    def get_account_status(self, obj):
        return "ACTIVE" if obj.user.is_active else "INACTIVE"

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        if "email" in user_data:
            instance.user.email = user_data["email"]
            instance.user.save(update_fields=["email"])
        return super().update(instance, validated_data)


class CustomerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = ["business_name", "contact_person", "address", "city", "state", "country", "pincode", "last_active_at"]


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ["id", "name", "code", "description", "price", "billing_cycle", "validity_days",
                  "company_limit", "device_limit", "is_active", "trial_available", "trial_days",
                  "created_at", "updated_at"]


class CustomerEntitlementSerializer(serializers.ModelSerializer):
    used_companies = serializers.SerializerMethodField()
    remaining_companies = serializers.SerializerMethodField()
    used_devices = serializers.SerializerMethodField()
    remaining_devices = serializers.SerializerMethodField()
    plan_code = serializers.CharField(source="plan.code", read_only=True, default=None)

    class Meta:
        model = CustomerEntitlement
        fields = ["company_limit", "device_limit", "plan_code", "used_companies",
                  "remaining_companies", "used_devices", "remaining_devices"]

    def get_used_companies(self, obj):
        return obj.used_companies()

    def get_remaining_companies(self, obj):
        return obj.remaining_companies()

    def get_used_devices(self, obj):
        return obj.used_devices()

    def get_remaining_devices(self, obj):
        return obj.remaining_devices()


class CustomerListSerializer(serializers.ModelSerializer):
    """Row shape for the Customers table (spec section 11) -- built off
    `Subscription` (one row per customer) with the extra columns joined in."""

    customer_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)
    phone = serializers.SerializerMethodField()
    business_name = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()
    gstin = serializers.SerializerMethodField()
    tally_serial = serializers.SerializerMethodField()
    license_status = serializers.SerializerMethodField()
    city = serializers.SerializerMethodField()
    state = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()
    companies_allowed = serializers.SerializerMethodField()
    companies_used = serializers.SerializerMethodField()
    last_active = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = ["customer_id", "username", "email", "phone", "business_name", "company", "gstin",
                  "tally_serial", "license_status", "city", "state", "plan",
                  "companies_allowed", "companies_used", "purchase_date", "expiry_date", "days_remaining",
                  "subscription_status", "last_active"]

    def get_phone(self, obj):
        profile = getattr(obj.user, "gst_profile", None)
        return profile.phone_number if profile else ""

    def get_username(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def get_business_name(self, obj):
        profile = getattr(obj.user, "customer_profile", None)
        return profile.business_name if profile else ""

    def _license(self, obj):
        if not hasattr(obj, "_sa_product_license"):
            obj._sa_product_license = obj.user.product_licenses.order_by("-created_at").first()
        return obj._sa_product_license

    def get_company(self, obj):
        profile = getattr(obj.user, "customer_profile", None)
        return (profile.business_name if profile else "") or obj.user.username

    def get_gstin(self, obj):
        license_obj = self._license(obj)
        return license_obj.licensed_gstin if license_obj else ""

    def get_tally_serial(self, obj):
        license_obj = self._license(obj)
        return license_obj.licensed_tally_serial if license_obj else ""

    def get_license_status(self, obj):
        license_obj = self._license(obj)
        return product_license_status(license_obj) if license_obj else obj.subscription_status

    def get_city(self, obj):
        profile = getattr(obj.user, "customer_profile", None)
        return profile.city if profile else ""

    def get_state(self, obj):
        profile = getattr(obj.user, "customer_profile", None)
        return profile.state if profile else ""

    def get_days_remaining(self, obj):
        return obj.days_remaining()

    def get_companies_allowed(self, obj):
        entitlement = getattr(obj.user, "entitlement", None)
        return entitlement.company_limit if entitlement else 0

    def get_companies_used(self, obj):
        entitlement = getattr(obj.user, "entitlement", None)
        return entitlement.used_companies() if entitlement else 0

    def get_last_active(self, obj):
        profile = getattr(obj.user, "customer_profile", None)
        return profile.last_active_at if profile else None


class RegisteredCompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = RegisteredCompany
        fields = ["id", "gstin", "company_name", "status", "admin_notes", "first_activated", "last_connected"]


class CompanyDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyDetails
        fields = ["company_name", "gstin", "state", "financial_year", "tally_serial_number", "tally_edition",
                  "tss_status", "license_administrator", "tally_connected", "company_verified", "verified_at"]


class TallyCompanyMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TallyCompanyMapping
        fields = ["gstin", "tally_company_name", "status", "license_serial", "license_administrator"]


class DeviceActivationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceActivation
        fields = ["id", "device_name", "device_id", "activated_at", "last_used_at", "is_active"]


class LicensedDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LicensedDevice
        fields = ["id", "device_fingerprint", "device_name", "windows_version", "app_version",
                  "status", "first_seen", "last_seen", "created_at", "updated_at"]


class DeviceActivationRequestSerializer(serializers.ModelSerializer):
    customer = serializers.SerializerMethodField()
    activation_key = serializers.SerializerMethodField()
    license_gstin = serializers.CharField(source="license.licensed_gstin", read_only=True)
    registered_tally_serial = serializers.CharField(source="license.licensed_tally_serial", read_only=True)
    old_device_name = serializers.CharField(source="old_device.device_name", read_only=True, default="")
    old_device_fingerprint = serializers.CharField(source="old_device.device_fingerprint", read_only=True, default="")

    class Meta:
        model = DeviceActivationRequest
        fields = ["id", "customer", "license", "activation_key", "license_gstin", "registered_tally_serial",
                  "old_device", "old_device_name", "old_device_fingerprint",
                  "requested_device_fingerprint", "requested_device_name", "windows_version",
                  "app_version", "detected_tally_serial", "current_company_gstin", "status",
                  "requested_at", "approved_at", "approved_by", "admin_reason"]

    def get_activation_key(self, obj):
        return product_license_activation_key(obj.license)

    def get_customer(self, obj):
        profile = getattr(obj.license.customer, "customer_profile", None)
        return (profile.business_name if profile else "") or obj.license.customer.get_full_name() or obj.license.customer.username


class ProductLicenseSerializer(serializers.ModelSerializer):
    customer = serializers.SerializerMethodField()
    customer_id = serializers.IntegerField(source="customer.id", read_only=True)
    customer_email = serializers.EmailField(source="customer.email", read_only=True)
    company = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    activation_key = serializers.SerializerMethodField()
    stored_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    computed_status = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()
    device = serializers.SerializerMethodField()
    tally_edition = serializers.SerializerMethodField()
    tss_status = serializers.SerializerMethodField()
    license_administrator = serializers.SerializerMethodField()
    active_device_count = serializers.SerializerMethodField()
    last_seen = serializers.SerializerMethodField()
    devices = LicensedDeviceSerializer(source="licensed_devices", many=True, read_only=True)

    class Meta:
        model = ProductLicense
        fields = ["id", "customer", "customer_id", "customer_email", "company", "phone",
                  "activation_key", "display_activation_key", "display_activation_key_suffix",
                  "licensed_gstin", "licensed_tally_serial", "plan", "allowed_devices", "status",
                  "stored_status", "computed_status", "purchase_date", "expiry_date", "days_remaining",
                  "activated_at", "last_verified_at", "device", "tally_edition", "tss_status",
                  "license_administrator", "active_device_count", "last_seen",
                  "devices", "created_at", "updated_at"]

    def get_company(self, obj):
        profile = getattr(obj.customer, "customer_profile", None)
        return (profile.business_name if profile else "") or obj.customer.username

    def get_customer(self, obj):
        return obj.customer.get_full_name() or obj.customer.username

    def get_phone(self, obj):
        profile = getattr(obj.customer, "gst_profile", None)
        return profile.phone_number if profile else ""

    def get_activation_key(self, obj):
        return product_license_activation_key(obj)

    def get_status(self, obj):
        return product_license_status(obj)

    def get_computed_status(self, obj):
        return product_license_status(obj)

    def get_days_remaining(self, obj):
        return product_license_days_remaining(obj)

    def get_device(self, obj):
        device = obj.licensed_devices.filter(status=LicensedDevice.ACTIVE).order_by("-last_seen").first()
        return device.device_name if device else ""

    def _company_details(self, obj):
        if not hasattr(obj, "_sa_company_details"):
            obj._sa_company_details = CompanyDetails.objects.filter(
                gstin=obj.licensed_gstin,
                tally_serial_number=obj.licensed_tally_serial,
            ).order_by("-verified_at", "-updated_at").first()
        return obj._sa_company_details

    def get_tally_edition(self, obj):
        details = self._company_details(obj)
        return details.tally_edition if details else ""

    def get_tss_status(self, obj):
        details = self._company_details(obj)
        return details.tss_status if details else ""

    def get_license_administrator(self, obj):
        details = self._company_details(obj)
        if details and details.license_administrator:
            return details.license_administrator
        mapping = TallyCompanyMapping.objects.filter(gstin=obj.licensed_gstin).first()
        return mapping.license_administrator if mapping else ""

    def get_active_device_count(self, obj):
        return obj.licensed_devices.filter(status=LicensedDevice.ACTIVE).count()

    def get_last_seen(self, obj):
        latest = obj.licensed_devices.filter(status=LicensedDevice.ACTIVE).order_by("-last_seen").first()
        return latest.last_seen if latest else None


class LicenseAuditLogSerializer(serializers.ModelSerializer):
    created_by = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = LicenseAuditLog
        fields = ["id", "license", "event_type", "old_value", "new_value", "device_fingerprint",
                  "detected_tally_serial", "current_company_gstin", "ip_address", "created_at", "created_by"]


class CompanyLimitHistorySerializer(serializers.ModelSerializer):
    changed_by = serializers.CharField(source="changed_by.username", read_only=True, default=None)

    class Meta:
        model = CompanyLimitHistory
        fields = ["old_limit", "new_limit", "changed_by", "reason", "created_at"]


class RenewalHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = RenewalHistory
        fields = ["previous_activation_date", "previous_expiry_date", "renewal_date", "new_expiry_date",
                  "renewal_period", "amount", "payment_status", "notes", "created_at"]


class SubscriptionAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionAuditLog
        fields = ["action", "old_expiry", "new_expiry", "created_at"]


class PaymentSerializer(serializers.ModelSerializer):
    customer = serializers.CharField(source="user.username", read_only=True)
    company = serializers.SerializerMethodField()
    expiry_date = serializers.DateField(source="billing_period_end", read_only=True)
    period = serializers.SerializerMethodField()
    plan_name = serializers.CharField(source="plan.name", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = ["id", "customer", "plan_name", "invoice_number", "order_number", "transaction_id",
                  "gateway_reference", "base_amount", "discount", "tax", "final_amount", "payment_method",
                  "payment_status", "payment_date", "billing_period_start", "billing_period_end", "expiry_date",
                  "period", "company", "notes", "created_at"]

    def get_company(self, obj):
        profile = getattr(obj.user, "customer_profile", None)
        return (profile.business_name if profile else "") or obj.user.username

    def get_period(self, obj):
        if obj.billing_period_start and obj.billing_period_end:
            months = max(1, round((obj.billing_period_end - obj.billing_period_start).days / 30))
            return f"{months} Months"
        return ""


class SupportTicketSerializer(serializers.ModelSerializer):
    customer = serializers.CharField(source="user.username", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, default=None)

    class Meta:
        model = SupportTicket
        fields = ["id", "customer", "company_gstin", "subject", "description", "category", "priority",
                  "status", "assigned_to_name", "created_at", "updated_at"]


class CustomerNoteSerializer(serializers.ModelSerializer):
    created_by = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = CustomerNote
        fields = ["id", "note", "created_by", "created_at"]


class AdminAuditLogSerializer(serializers.ModelSerializer):
    admin_user = serializers.CharField(source="admin_user.username", read_only=True, default=None)
    customer = serializers.CharField(source="customer.username", read_only=True, default=None)

    class Meta:
        model = AdminAuditLog
        fields = ["id", "admin_user", "action", "entity_type", "entity_id", "customer",
                  "old_value", "new_value", "reason", "ip_address", "created_at"]


class SuperAdminSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SuperAdminSettings
        fields = ["application_name", "support_email", "support_phone", "default_plan",
                  "default_validity_days", "default_company_limit", "default_device_limit",
                  "expiry_warning_days", "current_app_version", "maintenance_mode",
                  "registration_enabled", "trial_enabled"]
