from django.conf import settings
from django.db import models
from django.utils import timezone

from .dates import add_one_year

# How many days before expiry the status flips from ACTIVE to EXPIRING_SOON
# (spec section 5's default warning ladder: 30/15/7/1 days remaining --
# EXPIRING_SOON itself just needs the widest of those, 30, as its threshold;
# the frontend/reminder layer decides which specific banner text to show
# within that window).
EXPIRING_SOON_WINDOW_DAYS = 30


class Subscription(models.Model):
    """One annual GSTR 2 Tally license per customer account (not per company
    or per device -- see spec sections 24-26). `subscription_status` is a
    cached/derived field, recomputed by `refresh_status()` on every read
    that matters (admin list, `/me/`, the enforcement middleware); the real
    source of truth for whether access is blocked is always today's date
    compared against `expiry_date`, plus the explicit `is_suspended` flag.
    """

    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    EXPIRING_SOON = "EXPIRING_SOON"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    STATUS_CHOICES = [
        (TRIAL, "Trial"), (ACTIVE, "Active"), (EXPIRING_SOON, "Expiring Soon"),
        (EXPIRED, "Expired"), (SUSPENDED, "Suspended"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription")
    plan = models.CharField(max_length=50, default="Annual")

    # Purchase vs activation are deliberately separate dates (spec section 9)
    # -- expiry is always derived from activation_date, never purchase_date.
    purchase_date = models.DateField(null=True, blank=True)
    is_activated = models.BooleanField(default=False)
    activation_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    # Admin-only override, checked before any date math (spec section 4:
    # suspended wins regardless of where the account sits in its period).
    is_suspended = models.BooleanField(default=False)

    subscription_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=TRIAL)

    activated_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    last_status_checked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} ({self.subscription_status})"

    def days_remaining(self, today=None):
        """Always computed fresh, never trusted from a stored value (spec
        section 17) -- 0 once expired, never negative."""
        if not self.expiry_date:
            return 0
        today = today or timezone.localdate()
        return max(0, (self.expiry_date - today).days)

    def is_expired(self, today=None):
        if not self.is_activated or not self.expiry_date:
            return False
        today = today or timezone.localdate()
        return today >= self.expiry_date

    def compute_status(self, today=None):
        today = today or timezone.localdate()
        if self.is_suspended:
            return self.SUSPENDED
        if not self.is_activated or not self.expiry_date:
            return self.TRIAL
        if today >= self.expiry_date:
            return self.EXPIRED
        if (self.expiry_date - today).days <= EXPIRING_SOON_WINDOW_DAYS:
            return self.EXPIRING_SOON
        return self.ACTIVE

    def refresh_status(self, today=None, save=True):
        """Recomputes `subscription_status` from real dates/flags -- this is
        what the enforcement middleware and `/me/` call on every request, so
        the frontend/backend can never disagree about "is this account
        blocked right now" (spec section 4: backend is the source of truth,
        never a frontend timer)."""
        today = today or timezone.localdate()
        previous = self.subscription_status
        next_status = self.compute_status(today)
        update_fields = ["last_status_checked_at"]
        self.last_status_checked_at = timezone.now()
        if next_status != previous:
            self.subscription_status = next_status
            update_fields.append("subscription_status")
            if next_status == self.EXPIRED and not self.expired_at:
                self.expired_at = timezone.now()
                update_fields.append("expired_at")
                SubscriptionAuditLog.objects.create(
                    subscription=self, action=SubscriptionAuditLog.EXPIRED,
                    old_expiry=self.expiry_date, new_expiry=self.expiry_date,
                )
        if save:
            self.save(update_fields=update_fields)
        return self.subscription_status

    def activate(self, performed_by=None, purchase_date=None, today=None):
        """First-and-only activation (spec sections 1, 10, 11, 27): a second
        call is a no-op that returns the already-set dates unchanged --
        never resets activation_date, regardless of who calls it or how
        many times."""
        if self.is_activated:
            return self
        today = today or timezone.localdate()
        self.purchase_date = purchase_date or self.purchase_date or today
        self.activation_date = today
        self.expiry_date = add_one_year(today)
        self.is_activated = True
        self.is_suspended = False
        self.activated_at = timezone.now()
        self.expired_at = None
        self.subscription_status = self.ACTIVE
        self.last_status_checked_at = timezone.now()
        self.save()
        SubscriptionAuditLog.objects.create(
            subscription=self, action=SubscriptionAuditLog.ACTIVATED,
            old_expiry=None, new_expiry=self.expiry_date, performed_by=performed_by,
        )
        return self

    def renew(self, performed_by=None, amount=None, payment_status=None, period_years=1,
              period_months=None, custom_expiry_date=None, notes="", today=None):
        """Spec sections 14/15/30/31: renewing an ACTIVE/EXPIRING_SOON
        subscription extends from its CURRENT expiry (never loses remaining
        validity); renewing an EXPIRED one starts the new period from today.
        Always recorded in RenewalHistory, never overwriting that history.

        `period_years` stays the default (existing callers/tests are
        unaffected). The Super Admin "Renew / Extend" modal additionally
        supports `period_months` (1/3/6) or an explicit `custom_expiry_date`
        -- exactly one period option is honored, checked in that order.
        """
        today = today or timezone.localdate()
        self.refresh_status(today=today, save=False)
        previous_activation, previous_expiry = self.activation_date, self.expiry_date
        expired = self.subscription_status == self.EXPIRED or not self.is_activated

        base = today if expired else (self.expiry_date or today)
        if custom_expiry_date is not None:
            new_expiry = custom_expiry_date
            renewal_period = "Custom"
        elif period_months is not None:
            month_index = base.month - 1 + period_months
            year = base.year + month_index // 12
            month = month_index % 12 + 1
            import calendar
            day = min(base.day, calendar.monthrange(year, month)[1])
            new_expiry = base.replace(year=year, month=month, day=day)
            renewal_period = f"{period_months} month" if period_months == 1 else f"{period_months} months"
        else:
            new_expiry = base
            for _ in range(max(1, period_years)):
                new_expiry = add_one_year(new_expiry)
            renewal_period = f"{period_years} year" if period_years == 1 else f"{period_years} years"

        if expired:
            self.activation_date = today
            self.purchase_date = self.purchase_date or today
        self.expiry_date = new_expiry
        self.is_activated = True
        self.is_suspended = False
        self.expired_at = None
        self.subscription_status = self.ACTIVE
        self.last_status_checked_at = timezone.now()
        self.save()

        RenewalHistory.objects.create(
            subscription=self,
            previous_activation_date=previous_activation, previous_expiry_date=previous_expiry,
            renewal_date=today, new_expiry_date=new_expiry,
            renewal_period=renewal_period,
            amount=amount, payment_status=payment_status or "", changed_by=performed_by, notes=notes or "",
        )
        SubscriptionAuditLog.objects.create(
            subscription=self, action=SubscriptionAuditLog.RENEWED,
            old_expiry=previous_expiry, new_expiry=new_expiry, performed_by=performed_by,
        )
        return self

    def suspend(self, performed_by=None):
        if self.is_suspended:
            return self
        self.is_suspended = True
        self.subscription_status = self.SUSPENDED
        self.save(update_fields=["is_suspended", "subscription_status", "updated_at"])
        SubscriptionAuditLog.objects.create(
            subscription=self, action=SubscriptionAuditLog.SUSPENDED,
            old_expiry=self.expiry_date, new_expiry=self.expiry_date, performed_by=performed_by,
        )
        return self

    def reactivate(self, performed_by=None, today=None):
        if not self.is_suspended:
            return self
        self.is_suspended = False
        self.refresh_status(today=today, save=False)
        self.save(update_fields=["is_suspended", "subscription_status", "last_status_checked_at", "updated_at"])
        SubscriptionAuditLog.objects.create(
            subscription=self, action=SubscriptionAuditLog.REACTIVATED,
            old_expiry=self.expiry_date, new_expiry=self.expiry_date, performed_by=performed_by,
        )
        return self


class RenewalHistory(models.Model):
    """One immutable row per renewal -- never edited/overwritten, so the full
    period-by-period history stays intact even though `Subscription` itself
    only ever holds the CURRENT period (spec section 16)."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="renewal_history")
    previous_activation_date = models.DateField(null=True, blank=True)
    previous_expiry_date = models.DateField(null=True, blank=True)
    renewal_date = models.DateField()
    new_expiry_date = models.DateField()
    renewal_period = models.CharField(max_length=20, default="1 year")
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    payment_status = models.CharField(max_length=30, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Renewal for {self.subscription_id} on {self.renewal_date} -> {self.new_expiry_date}"


class SubscriptionAuditLog(models.Model):
    ACTIVATED = "LICENSE_ACTIVATED"
    RENEWED = "SUBSCRIPTION_RENEWED"
    EXPIRED = "SUBSCRIPTION_EXPIRED"
    SUSPENDED = "SUBSCRIPTION_SUSPENDED"
    REACTIVATED = "SUBSCRIPTION_REACTIVATED"
    ACTION_CHOICES = [
        (ACTIVATED, ACTIVATED), (RENEWED, RENEWED), (EXPIRED, EXPIRED),
        (SUSPENDED, SUSPENDED), (REACTIVATED, REACTIVATED),
    ]

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="audit_log")
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    old_expiry = models.DateField(null=True, blank=True)
    new_expiry = models.DateField(null=True, blank=True)
    # null = performed automatically by the system (e.g. the expiry sweep),
    # not by a Super Admin -- kept distinguishable rather than defaulting to
    # any particular user.
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} #{self.subscription_id} @ {self.created_at:%Y-%m-%d %H:%M}"
