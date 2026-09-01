from django.conf import settings
from django.db import models
from django.utils import timezone

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="gst_profile", on_delete=models.CASCADE)
    phone_number = models.CharField(max_length=10, unique=True)
    activated_at = models.DateTimeField(null=True, blank=True)

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
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=10, choices=FileType.choices)
    total_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    source_parties = models.JSONField(default=dict, blank=True)
    company_gstin = models.CharField(max_length=15, blank=True)
    company_gstin_candidates = models.JSONField(default=list, blank=True)
    company_details = models.JSONField(default=dict, blank=True)
    company_resolution_status = models.CharField(max_length=40, blank=True)
    company_resolution_error = models.CharField(max_length=100, blank=True)
    tax_period = models.CharField(max_length=20, blank=True)
    source_fingerprint = models.CharField(max_length=64, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "gst_import_batch_tbl"
        indexes = [models.Index(fields=["company_gstin", "gst_return_type", "tax_period", "source_fingerprint"],
                                name="gst_batch_dup_scope_idx")]

class GSTInvoice(models.Model):
    import_batch = models.ForeignKey(GSTImportBatch, related_name="invoices", on_delete=models.CASCADE)
    invoice_date = models.DateField(null=True, blank=True)
    customer_gstin = models.CharField(max_length=15, blank=True)
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "gst_invoice_tbl"
        indexes = [
            models.Index(fields=["customer_gstin"], name="gst_inv_gstin_idx"),
            models.Index(fields=["invoice_no"], name="gst_inv_no_idx"),
            models.Index(fields=["invoice_date"], name="gst_inv_date_idx"),
            models.Index(fields=["source_type"], name="gst_inv_source_idx"),
            models.Index(fields=["filing_period"], name="gst_inv_period_idx"),
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
