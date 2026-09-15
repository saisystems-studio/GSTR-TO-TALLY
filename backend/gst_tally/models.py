import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="gst_profile", on_delete=models.CASCADE)
    phone_number = models.CharField(max_length=10, unique=True)
    activated_at = models.DateTimeField(null=True, blank=True)

class PasswordResetCode(models.Model):
    """A single-use 6-digit email verification code for the forgot-password
    flow (see auth_service.py). Only the HMAC hash of the code is ever
    stored, mirroring how activation keys are handled."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="password_reset_codes", on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "password_reset_code_tbl"
        indexes = [models.Index(fields=["user", "used_at"], name="pwd_reset_user_used_idx")]

class License(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        EXPIRED = "EXPIRED", "Expired"
        REVOKED = "REVOKED", "Revoked"
    serial_number = models.CharField(max_length=32, unique=True)
    registered_email = models.EmailField()
    activation_key_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    is_activated = models.BooleanField(default=False)
    activated_user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="activated_license", null=True, blank=True,
        on_delete=models.PROTECT,
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    max_devices = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.serial_number} ({self.registered_email})"

class DeviceActivation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="device_activations", on_delete=models.CASCADE)
    license = models.ForeignKey(License, related_name="device_activations", on_delete=models.PROTECT)
    device_id = models.CharField(max_length=64)
    trusted_token_hash = models.CharField(max_length=64)
    device_name = models.CharField(max_length=255, blank=True)
    activated_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "device_id"], name="unique_user_device")]
        indexes = [models.Index(fields=["device_id", "is_active"], name="gst_device_active_idx")]

class ProductLicense(models.Model):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    STATUS_CHOICES = [
        (PENDING, "Pending"), (ACTIVE, "Active"), (EXPIRING, "Expiring"),
        (EXPIRED, "Expired"), (SUSPENDED, "Suspended"), (REVOKED, "Revoked"),
    ]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="product_licenses", on_delete=models.PROTECT)
    activation_key_hash = models.CharField(max_length=64, unique=True)
    display_activation_key = models.CharField(max_length=100, blank=True)
    display_activation_key_suffix = models.CharField(max_length=12, blank=True)
    licensed_gstin = models.CharField(max_length=15)
    licensed_tally_serial = models.CharField(max_length=64)
    plan = models.CharField(max_length=50, default="Professional")
    allowed_devices = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=PENDING)
    purchase_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_license_tbl"
        indexes = [
            models.Index(fields=["customer", "status"], name="prod_lic_customer_status_idx"),
            models.Index(fields=["licensed_tally_serial"], name="prod_lic_tally_serial_idx"),
            models.Index(fields=["licensed_gstin"], name="prod_lic_gstin_idx"),
        ]

    def __str__(self):
        return f"{self.licensed_gstin} / {self.licensed_tally_serial}"


class LicensedDevice(models.Model):
    ACTIVE = "ACTIVE"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REVOKED = "REVOKED"
    REPLACED = "REPLACED"
    STATUS_CHOICES = [
        (ACTIVE, "Active"), (PENDING_APPROVAL, "Pending Approval"),
        (REVOKED, "Revoked"), (REPLACED, "Replaced"),
    ]

    license = models.ForeignKey(ProductLicense, related_name="licensed_devices", on_delete=models.CASCADE)
    device_fingerprint = models.CharField(max_length=128)
    device_name = models.CharField(max_length=255, blank=True)
    windows_version = models.CharField(max_length=100, blank=True)
    app_version = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "licensed_device_tbl"
        constraints = [models.UniqueConstraint(fields=["license", "device_fingerprint"], name="unique_license_device_fingerprint")]
        indexes = [models.Index(fields=["license", "status"], name="lic_device_license_status_idx")]


class DeviceActivationRequest(models.Model):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STATUS_CHOICES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected")]

    license = models.ForeignKey(ProductLicense, related_name="device_requests", on_delete=models.CASCADE)
    old_device = models.ForeignKey(LicensedDevice, related_name="+", null=True, blank=True, on_delete=models.SET_NULL)
    requested_device_fingerprint = models.CharField(max_length=128)
    requested_device_name = models.CharField(max_length=255, blank=True)
    windows_version = models.CharField(max_length=100, blank=True)
    app_version = models.CharField(max_length=50, blank=True)
    detected_tally_serial = models.CharField(max_length=64, blank=True)
    current_company_gstin = models.CharField(max_length=15, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    requested_at = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.SET_NULL)
    admin_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "device_activation_request_tbl"
        indexes = [models.Index(fields=["license", "status"], name="dev_req_license_status_idx")]


class LicenseAuditLog(models.Model):
    license = models.ForeignKey(ProductLicense, related_name="audit_logs", null=True, blank=True, on_delete=models.SET_NULL)
    event_type = models.CharField(max_length=50)
    old_value = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    new_value = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    device_fingerprint = models.CharField(max_length=128, blank=True)
    detected_tally_serial = models.CharField(max_length=64, blank=True)
    current_company_gstin = models.CharField(max_length=15, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = "license_audit_log_tbl"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["license", "event_type"], name="lic_audit_license_event_idx"),
            models.Index(fields=["event_type", "created_at"], name="lic_audit_event_created_idx"),
        ]

class GSTImportBatch(models.Model):
    class ReturnType(models.TextChoices):
        GSTR1 = "GSTR1", "GSTR-1 (B2B)"
        GSTR2A = "GSTR2A", "GSTR-2A"
        GSTR2B = "GSTR2B", "GSTR-2B"
    class FileType(models.TextChoices):
        JSON = "JSON", "JSON"
        CSV = "CSV", "CSV"
        EXCEL = "EXCEL", "Excel"
    gst_return_type = models.CharField(max_length=10, choices=ReturnType.choices, default=ReturnType.GSTR1)
    # Nullable: historical batches predate this column and simply have no
    # attributed uploader. Used only by the superadmin app's usage rollups --
    # never read by the accounting/import pipeline itself.
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="gst_import_batches",
                                     null=True, blank=True, on_delete=models.SET_NULL)
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=10, choices=FileType.choices)
    total_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    # Rows skipped because an invoice with the same business identity
    # (see services/import_identity.py::invoice_fingerprint) already exists
    # for this company GSTIN + return type -- never counted in imported_rows.
    duplicate_rows = models.PositiveIntegerField(default=0)
    company_import_summary = models.ForeignKey("GSTCompanyImportSummary", related_name="batches",
                                               null=True, blank=True, on_delete=models.SET_NULL)
    product_license = models.ForeignKey(ProductLicense, related_name="import_batches", null=True, blank=True, on_delete=models.SET_NULL)
    source_parties = models.JSONField(default=dict, blank=True)
    company_gstin = models.CharField(max_length=15, blank=True)
    company_scope_id = models.CharField(max_length=64, blank=True, db_index=True)
    company_gstin_candidates = models.JSONField(default=list, blank=True)
    company_details = models.JSONField(default=dict, blank=True)
    company_resolution_status = models.CharField(max_length=40, blank=True)
    company_resolution_error = models.CharField(max_length=100, blank=True)
    tax_period = models.CharField(max_length=20, blank=True)
    file_hash = models.CharField(max_length=64, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    source_fingerprint = models.CharField(max_length=64, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)
    # Source/preview rows are operational data, not the audit trail.  The
    # registry below remains permanent and is the sole duplicate authority.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "gst_import_batch_tbl"
        indexes = [
            models.Index(fields=["company_gstin", "gst_return_type", "tax_period", "source_fingerprint"],
                         name="gst_batch_dup_scope_idx"),
            models.Index(fields=["product_license", "company_gstin", "file_hash"],
                         name="gst_batch_file_hash_idx"),
        ]

class GSTCompanyImportSummary(models.Model):
    company_name = models.CharField(max_length=255, blank=True)
    company_gstin = models.CharField(max_length=15)
    company_scope_id = models.CharField(max_length=64, blank=True, db_index=True)
    return_type = models.CharField(max_length=10)
    transaction_type = models.CharField(max_length=20)
    source_file_name = models.CharField(max_length=255, blank=True)
    source_format = models.CharField(max_length=10, blank=True)
    import_scope_hash = models.CharField(max_length=64)
    total_source_count = models.PositiveIntegerField(default=0)
    successful_voucher_count = models.PositiveIntegerField(default=0)
    failed_record_count = models.PositiveIntegerField(default=0)
    needs_attention_count = models.PositiveIntegerField(default=0)
    skipped_duplicate_count = models.PositiveIntegerField(default=0)
    pending_record_count = models.PositiveIntegerField(default=0)
    first_uploaded_at = models.DateTimeField(default=timezone.now)
    last_uploaded_at = models.DateTimeField(default=timezone.now)
    first_tally_import_at = models.DateTimeField(null=True, blank=True)
    last_tally_import_at = models.DateTimeField(null=True, blank=True)
    last_retry_at = models.DateTimeField(null=True, blank=True)
    import_status = models.CharField(max_length=30, default="PENDING")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "gst_company_import_summary_tbl"
        constraints = [
            models.UniqueConstraint(fields=["company_scope_id", "company_gstin", "return_type", "import_scope_hash"],
                                    name="uniq_gst_company_summary"),
        ]
        indexes = [
            models.Index(fields=["company_gstin", "return_type"], name="gst_summary_company_return_idx"),
            models.Index(fields=["import_status"], name="gst_summary_status_idx"),
        ]


class GSTInvoice(models.Model):
    import_batch = models.ForeignKey(GSTImportBatch, related_name="invoices", on_delete=models.CASCADE)
    invoice_date = models.DateField(null=True, blank=True)
    voucher_date = models.DateField(null=True, blank=True)
    is_carry_forward = models.BooleanField(default=False)
    original_period = models.CharField(max_length=20, blank=True)
    posting_period = models.CharField(max_length=20, blank=True)
    # customer_gstin keeps its long-standing meaning: the counterparty GSTIN
    # the Tally voucher-building pipeline (tally/mappings.py) reads for every
    # return type -- the recipient for GSTR-1, the supplier for GSTR-2A/2B
    # (mirrored from supplier_gstin at parse time). supplier_gstin/
    # supplier_name/customer_name are the explicit, correctly-direction-aware
    # canonical fields used for display and party extraction; see
    # services/canonical_invoice.py.
    customer_gstin = models.CharField(max_length=15, blank=True)
    customer_name = models.CharField(max_length=255, blank=True)
    supplier_gstin = models.CharField(max_length=15, blank=True)
    supplier_name = models.CharField(max_length=255, blank=True)
    invoice_no = models.CharField(max_length=100)
    taxable_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_percent = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    cgst = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    sgst = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    igst = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    invoice_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    state_code = models.CharField(max_length=2, blank=True)
    reverse_charge = models.CharField(max_length=10, blank=True)
    invoice_type = models.CharField(max_length=30, blank=True)
    filing_period = models.CharField(max_length=20, blank=True)
    filing_type = models.CharField(max_length=20, blank=True)
    filing_date = models.DateField(null=True, blank=True)
    source_type = models.CharField(max_length=20, blank=True)
    item_name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    hsn_sac = models.CharField(max_length=20, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    rate = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    discount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    cess = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True)
    supply_type = models.CharField(max_length=20, blank=True)
    other_charges = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    round_off = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    source_line = models.JSONField(default=dict, blank=True)
    # Deterministic identity of this invoice's business data -- company GSTIN
    # + return type + counterparty GSTIN + invoice number/date/type + amounts
    # -- used to reject re-importing the same invoice from a different file
    # without blocking genuinely new invoices for the same company/GSTIN. See
    # services/import_identity.py::invoice_fingerprint. Cascades away with the
    # batch (import_batch is on_delete=CASCADE), so deleting a batch always
    # frees its invoices' fingerprints for re-import.
    dedup_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "gst_invoice_tbl"
        indexes = [
            models.Index(fields=["customer_gstin"], name="gst_inv_gstin_idx"),
            models.Index(fields=["supplier_gstin"], name="gst_inv_supp_gstin_idx"),
            models.Index(fields=["invoice_no"], name="gst_inv_no_idx"),
            models.Index(fields=["invoice_date"], name="gst_inv_date_idx"),
            models.Index(fields=["source_type"], name="gst_inv_source_idx"),
            models.Index(fields=["filing_period"], name="gst_inv_period_idx"),
            models.Index(fields=["is_carry_forward", "posting_period"], name="gst_inv_carry_period_idx"),
        ]

class GSTParty(models.Model):
    gstin = models.CharField(max_length=15, unique=True)
    legal_name = models.CharField(max_length=255, blank=True)
    trade_name = models.CharField(max_length=255, blank=True)
    address = models.TextField(blank=True)
    principal_place_of_business = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    district = models.CharField(max_length=100, blank=True)
    state_name = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    registration_status = models.CharField(max_length=50, blank=True)
    registration_date = models.CharField(max_length=20, blank=True)
    cancellation_date = models.CharField(max_length=20, blank=True)
    constitution_of_business = models.CharField(max_length=100, blank=True)
    taxpayer_type = models.CharField(max_length=50, blank=True)
    central_jurisdiction = models.CharField(max_length=255, blank=True)
    state_jurisdiction = models.CharField(max_length=255, blank=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    verification_source = models.CharField(max_length=30, blank=True)
    verification_status = models.CharField(max_length=20, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    lookup_source = models.CharField(max_length=20, blank=True)
    lookup_status = models.CharField(max_length=100, blank=True)
    lookup_error = models.CharField(max_length=100, blank=True)
    lookup_diagnostics = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    retry_not_before = models.DateTimeField(null=True, blank=True)
    party_data_status = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "gst_party_tbl"

class GSTLedgerMapping(models.Model):
    gstin = models.CharField(max_length=15, db_index=True)
    gst_party_name = models.CharField(max_length=255, blank=True)
    tally_ledger_name = models.CharField(max_length=255)
    mapping_type = models.CharField(max_length=20, default="BOTH")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["gstin", "mapping_type"], name="unique_gstin_mapping_type")]

class GSTSyncLog(models.Model):
    batch = models.ForeignKey(GSTImportBatch, related_name="sync_logs", on_delete=models.CASCADE)
    import_record = models.ForeignKey(GSTInvoice, related_name="sync_logs", on_delete=models.CASCADE)
    sync_status = models.CharField(max_length=20)
    request_payload = models.JSONField(default=dict)
    response_payload = models.JSONField(default=dict)
    error_message = models.TextField(blank=True)
    synced_at = models.DateTimeField(auto_now_add=True)

class TallyVoucherMapping(models.Model):
    batch = models.ForeignKey(GSTImportBatch, related_name="tally_vouchers", on_delete=models.CASCADE)
    invoice = models.ForeignKey(GSTInvoice, related_name="tally_vouchers", on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=64, unique=True)
    source_invoice_number = models.CharField(max_length=100)
    party_gstin = models.CharField(max_length=15, blank=True)
    tally_company = models.CharField(max_length=255)
    tally_voucher_identifier = models.CharField(max_length=255, blank=True)
    import_status = models.CharField(max_length=30)
    raw_response = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "tally_voucher_mapping_tbl"
        indexes = [models.Index(fields=["tally_company", "source_invoice_number"], name="tally_company_inv_idx")]


class GSTTallyVoucherRegistry(models.Model):
    company_gstin = models.CharField(max_length=15)
    company_scope_id = models.CharField(max_length=64, blank=True, db_index=True)
    company_name = models.CharField(max_length=255, blank=True)
    return_type = models.CharField(max_length=10)
    transaction_type = models.CharField(max_length=20)
    party_gstin = models.CharField(max_length=15)
    invoice_number = models.CharField(max_length=100)
    invoice_date = models.DateField(null=True, blank=True)
    voucher_identity_hash = models.CharField(max_length=64)
    import_summary = models.ForeignKey(GSTCompanyImportSummary, related_name="voucher_registry",
                                       null=True, blank=True, on_delete=models.SET_NULL)
    latest_invoice = models.ForeignKey(GSTInvoice, related_name="voucher_registry_entries",
                                       null=True, blank=True, on_delete=models.SET_NULL)
    import_status = models.CharField(max_length=30, default="PENDING")
    tally_created = models.BooleanField(default=False)
    tally_altered = models.BooleanField(default=False)
    tally_voucher_identifier = models.CharField(max_length=255, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    imported_at = models.DateTimeField(null=True, blank=True)
    last_retry_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "gst_tally_voucher_registry_tbl"
        constraints = [
            models.UniqueConstraint(fields=["company_scope_id", "company_gstin", "return_type", "voucher_identity_hash"],
                                    name="uniq_gst_voucher_registry"),
        ]
        indexes = [
            models.Index(fields=["company_gstin", "return_type"], name="gst_reg_company_return_idx"),
            models.Index(fields=["import_status"], name="gst_registry_status_idx"),
            models.Index(fields=["voucher_identity_hash"], name="gst_registry_hash_idx"),
        ]

class CompanyDetails(models.Model):
    """Company + Tally license identity captured at Company Verification time
    (see services/company_verification.py). Independent of TallyCompanyMapping,
    which is written later, during voucher import."""
    company_name = models.CharField(max_length=255, unique=True)
    gstin = models.CharField(max_length=15, blank=True)
    state = models.CharField(max_length=100, blank=True)
    financial_year = models.CharField(max_length=40, blank=True)
    financial_year_from = models.CharField(max_length=20, blank=True)
    financial_year_to = models.CharField(max_length=20, blank=True)

    tally_serial_number = models.CharField(max_length=64, blank=True)
    tally_edition = models.CharField(max_length=50, blank=True)
    tss_status = models.CharField(max_length=50, blank=True)
    license_administrator = models.CharField(max_length=255, blank=True)

    tally_connected = models.BooleanField(default=False)
    company_verified = models.BooleanField(default=False)

    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: db_table = "companydetails_tbl"

class TallyCompanyMapping(models.Model):
    gstin = models.CharField(max_length=15, unique=True)
    tally_company_name = models.CharField(max_length=255)
    company_details = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=30)
    raw_response = models.TextField(blank=True)
    # The first Tally license identity (serial + administrator) this company
    # GSTIN was successfully verified against. Once set, later imports for the
    # same company must match this exact pair -- see services/tally_license.py.
    license_serial = models.CharField(max_length=64, blank=True)
    license_administrator = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: db_table = "tally_company_mapping_tbl"

class TallyImportJob(models.Model):
    """Step 6 import execution state -- replaces the old boolean/cache-only
    lock (see tally/import_job.py). One batch may have many job rows over
    time (retries/resumes); the most recent row is the one that matters for
    "is an import currently running" / "restore progress after page reload".

    Status values: PENDING, RUNNING, VERIFYING, COMPLETED, PARTIAL, FAILED,
    INTERRUPTED. A job is a *valid, active* lock only while status is one of
    PENDING/RUNNING/VERIFYING AND heartbeat_at is recent -- see
    tally/import_job.py's STALE_HEARTBEAT_SECONDS. An old RUNNING row whose
    heartbeat has gone stale (the process that owned it died/was killed) is
    marked INTERRUPTED rather than left to block the batch forever.
    """
    job_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(GSTImportBatch, related_name="tally_import_jobs", on_delete=models.CASCADE)
    # Set only for a VPS import.  Development installations may retain the
    # direct local client without changing their existing workflow.
    local_agent = models.ForeignKey("LocalTallyAgent", related_name="import_jobs", null=True, blank=True,
                                    on_delete=models.PROTECT)
    status = models.CharField(max_length=20, default="PENDING")
    # Set by the pause endpoint, read by the running worker thread before
    # each voucher (see tally/import_job.py's should_pause_callback wiring)
    # -- never acted on mid-voucher, so a write already in flight to Tally
    # always finishes before the loop actually stops.
    pause_requested = models.BooleanField(default=False)
    request_id = models.CharField(max_length=64, blank=True)
    total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    imported = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    verification_pending = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    current_invoice = models.CharField(max_length=100, blank=True)
    # Full import_batch() response dict, saved only once the job reaches a
    # terminal status -- the same shape Step 6 has always consumed, so the
    # frontend's result rendering needs no reshaping.
    result = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    error_message = models.TextField(blank=True)
    # Set only when the job crashed on a TallyConnectionError (see
    # tally/client.py) -- e.g. TALLY_CONNECTION_REFUSED/TALLY_HOST_UNREACHABLE
    # -- so the frontend can tell "Tally Prime disconnected mid-import"
    # (recoverable: reconnect Tally, then retry) apart from any other crash.
    # Blank for every other FAILED job.
    error_code = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    heartbeat_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = "tally_import_job_tbl"
        indexes = [models.Index(fields=["batch", "status"], name="tally_import_job_batch_idx")]


class LocalTallyAgent(models.Model):
    """An outbound-only Windows agent.  The token is stored as a SHA-256
    digest; the plaintext is shown only at provisioning time."""
    device = models.OneToOneField(LicensedDevice, related_name="local_tally_agent", on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    detected_serial = models.CharField(max_length=64, blank=True)
    detected_company_gstin = models.CharField(max_length=15, blank=True)
    detected_company_name = models.CharField(max_length=255, blank=True)
    tally_reachable = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "local_tally_agent_tbl"
        indexes = [models.Index(fields=["detected_company_gstin", "last_seen_at"], name="agent_gstin_seen_idx")]


class LocalTallyJob(models.Model):
    QUEUED, CLAIMED, SENDING, SUCCESS, FAILED, RETRYABLE, EXPIRED = (
        "QUEUED", "CLAIMED", "SENDING_TO_TALLY", "SUCCESS", "FAILED", "RETRYABLE", "EXPIRED")
    agent = models.ForeignKey(LocalTallyAgent, related_name="jobs", on_delete=models.PROTECT)
    batch = models.ForeignKey(GSTImportBatch, related_name="local_agent_jobs", on_delete=models.CASCADE)
    job_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=64, unique=True)
    payload = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=20, default=QUEUED)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    acknowledgement = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "local_tally_job_tbl"
        indexes = [models.Index(fields=["agent", "status", "created_at"], name="agent_job_claim_idx")]
