from django.conf import settings
from django.db import models
from django.utils import timezone


class SuperAdminProfile(models.Model):
    """Marks a `User` row as a Super Admin account and carries the
    commercial-admin-only profile fields the customer `UserProfile` doesn't
    have. Presence of this row (not `is_staff`/`is_superuser` alone) is what
    `IsSuperAdmin` checks -- see permissions.py."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="superadmin_profile")
    display_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    # True on the bootstrap account until the first real password change --
    # the login flow forces /superadmin/change-password while this is set.
    must_change_password = models.BooleanField(default=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name or self.user.username


class PasswordResetToken(models.Model):
    """Backend-ready reset architecture (spec section 5): a hashed, single-use,
    expiring token. No email backend is configured in this project, so the
    raw token is never emailed -- it is logged server-side for a developer to
    relay manually, and never returned in any API response."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self, now=None):
        now = now or timezone.now()
        return self.used_at is None and self.expires_at > now


class CustomerProfile(models.Model):
    """Commercial/contact info for a customer account, additional to the
    existing `gst_tally.UserProfile` (phone) -- deliberately does not
    duplicate anything already on `User`/`UserProfile`/`Subscription`.
    Account status (active/expired/suspended) is intentionally NOT stored
    here: it is always read live from `subscriptions.Subscription`, the
    existing single source of truth for that."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile")
    business_name = models.CharField(max_length=255, blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default="India")
    pincode = models.CharField(max_length=10, blank=True)
    last_active_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name or self.user.username


class SubscriptionPlan(models.Model):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    HALF_YEARLY = "HALF_YEARLY"
    YEARLY = "YEARLY"
    BILLING_CYCLE_CHOICES = [
        (MONTHLY, "Monthly"), (QUARTERLY, "Quarterly"),
        (HALF_YEARLY, "Half-Yearly"), (YEARLY, "Yearly"),
    ]

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_cycle = models.CharField(max_length=15, choices=BILLING_CYCLE_CHOICES, default=YEARLY)
    validity_days = models.PositiveIntegerField(default=365)
    company_limit = models.PositiveIntegerField(default=1)
    device_limit = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    trial_available = models.BooleanField(default=False)
    trial_days = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class CustomerEntitlement(models.Model):
    """The customer's currently effective plan + limits (spec sections
    19-22, 28). Kept separate from `subscriptions.Subscription` (which owns
    only dates/status) so that app's schema stays untouched."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="entitlement")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    company_limit = models.PositiveIntegerField(default=1)
    device_limit = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def used_companies(self):
        return self.user.registered_companies.exclude(status=RegisteredCompany.DEACTIVATED).count()

    def remaining_companies(self):
        return max(0, self.company_limit - self.used_companies())

    def used_devices(self):
        from gst_tally.models import DeviceActivation
        return DeviceActivation.objects.filter(user=self.user, is_active=True).count()

    def remaining_devices(self):
        return max(0, self.device_limit - self.used_devices())


class CompanyLimitHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="company_limit_history")
    old_limit = models.PositiveIntegerField()
    new_limit = models.PositiveIntegerField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class RegisteredCompany(models.Model):
    """Links a customer account to a GSTIN. Deliberately does NOT duplicate
    `gst_tally.CompanyDetails`/`TallyCompanyMapping` -- Companies/Company
    Detail pages join those tables by GSTIN at read time (see
    services/companies.py)."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DEACTIVATED = "DEACTIVATED"
    BLOCKED = "BLOCKED"
    STATUS_CHOICES = [(ACTIVE, "Active"), (INACTIVE, "Inactive"), (DEACTIVATED, "Deactivated"), (BLOCKED, "Blocked")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="registered_companies")
    gstin = models.CharField(max_length=15)
    company_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=ACTIVE)
    admin_notes = models.TextField(blank=True)
    first_activated = models.DateTimeField(default=timezone.now)
    last_connected = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "gstin"], name="unique_customer_gstin")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company_name or self.gstin} -> {self.user}"


class Payment(models.Model):
    PAID = "PAID"
    PENDING = "PENDING"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    STATUS_CHOICES = [
        (PAID, "Paid"), (PENDING, "Pending"), (FAILED, "Failed"),
        (REFUNDED, "Refunded"), (PARTIALLY_REFUNDED, "Partially Refunded"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    invoice_number = models.CharField(max_length=50, unique=True)
    order_number = models.CharField(max_length=50, blank=True)
    transaction_id = models.CharField(max_length=100, blank=True)
    gateway_reference = models.CharField(max_length=100, blank=True)
    base_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=50, blank=True)
    payment_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    payment_date = models.DateField(null=True, blank=True)
    billing_period_start = models.DateField(null=True, blank=True)
    billing_period_end = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.invoice_number} ({self.payment_status})"


class SupportTicket(models.Model):
    LOW, MEDIUM, HIGH, CRITICAL = "LOW", "MEDIUM", "HIGH", "CRITICAL"
    PRIORITY_CHOICES = [(LOW, "Low"), (MEDIUM, "Medium"), (HIGH, "High"), (CRITICAL, "Critical")]

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    STATUS_CHOICES = [
        (OPEN, "Open"), (IN_PROGRESS, "In Progress"), (WAITING_FOR_CUSTOMER, "Waiting for Customer"),
        (RESOLVED, "Resolved"), (CLOSED, "Closed"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    company_gstin = models.CharField(max_length=15, blank=True)
    subject = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=MEDIUM)
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=OPEN)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.subject}"


class CustomerNote(models.Model):
    """Internal, Super-Admin-only notes -- never exposed via any customer-
    facing API (spec section 41)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="internal_notes")
    note = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AdminAuditLog(models.Model):
    """Generic Super Admin action log (spec sections 45/46) -- distinct from
    `subscriptions.SubscriptionAuditLog`, which only covers the subscription
    state machine itself. This one covers every mutating Super Admin action:
    plan changes, company-limit changes, device resets, suspensions,
    payment-status changes, company deactivation, etc. Read-only from the
    API/admin; never deleted or edited."""

    admin_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50, blank=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} on {self.entity_type}:{self.entity_id}"


class SuperAdminSettings(models.Model):
    """Singleton (always pk=1) -- see services/settings.py::get_settings()."""

    application_name = models.CharField(max_length=100, default="GSTR 2 Tally")
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=20, blank=True)
    default_plan = models.ForeignKey(SubscriptionPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    default_validity_days = models.PositiveIntegerField(default=365)
    default_company_limit = models.PositiveIntegerField(default=1)
    default_device_limit = models.PositiveIntegerField(default=1)
    expiry_warning_days = models.PositiveIntegerField(default=30)
    current_app_version = models.CharField(max_length=50, blank=True, default="")
    maintenance_mode = models.BooleanField(default=False)
    registration_enabled = models.BooleanField(default=True)
    trial_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class SandboxAPIConfiguration(models.Model):
    provider = models.CharField(max_length=30, default="sandbox")
    environment = models.CharField(max_length=30, default="test")
    api_key_encrypted = models.TextField()
    api_secret_encrypted = models.TextField()
    api_version = models.CharField(max_length=30, default="1.0.0")
    is_active = models.BooleanField(default=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sandbox_configurations_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sandbox_configurations_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "environment"], name="sandbox_provider_environment_unique")]
