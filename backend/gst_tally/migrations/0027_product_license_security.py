import django.core.serializers.json
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gst_tally", "0026_tallyimportjob_pause_requested"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductLicense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("activation_key_hash", models.CharField(max_length=64, unique=True)),
                ("display_activation_key_suffix", models.CharField(blank=True, max_length=12)),
                ("licensed_gstin", models.CharField(max_length=15)),
                ("licensed_tally_serial", models.CharField(max_length=64)),
                ("plan", models.CharField(default="Professional", max_length=50)),
                ("allowed_devices", models.PositiveSmallIntegerField(default=1)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("ACTIVE", "Active"), ("EXPIRING", "Expiring"), ("EXPIRED", "Expired"), ("SUSPENDED", "Suspended"), ("REVOKED", "Revoked")], default="PENDING", max_length=15)),
                ("purchase_date", models.DateField(blank=True, null=True)),
                ("expiry_date", models.DateField(blank=True, null=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="product_licenses", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "product_license_tbl"},
        ),
        migrations.CreateModel(
            name="LicensedDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("device_fingerprint", models.CharField(max_length=128)),
                ("device_name", models.CharField(blank=True, max_length=255)),
                ("windows_version", models.CharField(blank=True, max_length=100)),
                ("app_version", models.CharField(blank=True, max_length=50)),
                ("status", models.CharField(choices=[("ACTIVE", "Active"), ("PENDING_APPROVAL", "Pending Approval"), ("REVOKED", "Revoked"), ("REPLACED", "Replaced")], default="ACTIVE", max_length=20)),
                ("first_seen", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_seen", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("license", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="licensed_devices", to="gst_tally.productlicense")),
            ],
            options={"db_table": "licensed_device_tbl"},
        ),
        migrations.CreateModel(
            name="LicenseAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(max_length=50)),
                ("old_value", models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True)),
                ("new_value", models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True)),
                ("device_fingerprint", models.CharField(blank=True, max_length=128)),
                ("detected_tally_serial", models.CharField(blank=True, max_length=64)),
                ("current_company_gstin", models.CharField(blank=True, max_length=15)),
                ("ip_address", models.CharField(blank=True, max_length=45)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("license", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to="gst_tally.productlicense")),
            ],
            options={"db_table": "license_audit_log_tbl", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="DeviceActivationRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_device_fingerprint", models.CharField(max_length=128)),
                ("requested_device_name", models.CharField(blank=True, max_length=255)),
                ("windows_version", models.CharField(blank=True, max_length=100)),
                ("app_version", models.CharField(blank=True, max_length=50)),
                ("detected_tally_serial", models.CharField(blank=True, max_length=64)),
                ("current_company_gstin", models.CharField(blank=True, max_length=15)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")], default="PENDING", max_length=10)),
                ("requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("admin_reason", models.CharField(blank=True, max_length=255)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("license", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="device_requests", to="gst_tally.productlicense")),
                ("old_device", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="gst_tally.licenseddevice")),
            ],
            options={"db_table": "device_activation_request_tbl"},
        ),
        migrations.AddIndex(model_name="productlicense", index=models.Index(fields=["customer", "status"], name="prod_lic_customer_status_idx")),
        migrations.AddIndex(model_name="productlicense", index=models.Index(fields=["licensed_tally_serial"], name="prod_lic_tally_serial_idx")),
        migrations.AddIndex(model_name="productlicense", index=models.Index(fields=["licensed_gstin"], name="prod_lic_gstin_idx")),
        migrations.AddIndex(model_name="licenseddevice", index=models.Index(fields=["license", "status"], name="lic_device_license_status_idx")),
        migrations.AddConstraint(model_name="licenseddevice", constraint=models.UniqueConstraint(fields=("license", "device_fingerprint"), name="unique_license_device_fingerprint")),
        migrations.AddIndex(model_name="licenseauditlog", index=models.Index(fields=["license", "event_type"], name="lic_audit_license_event_idx")),
        migrations.AddIndex(model_name="licenseauditlog", index=models.Index(fields=["event_type", "created_at"], name="lic_audit_event_created_idx")),
        migrations.AddIndex(model_name="deviceactivationrequest", index=models.Index(fields=["license", "status"], name="dev_req_license_status_idx")),
    ]
