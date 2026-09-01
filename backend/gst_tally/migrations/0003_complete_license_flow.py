import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def populate_legacy_licenses(apps, schema_editor):
    License = apps.get_model("gst_tally", "License")
    for license_obj in License.objects.filter(serial_number__isnull=True).iterator():
        license_obj.serial_number = f"LEGACY-{license_obj.pk:06d}"
        license_obj.registered_email = f"legacy-{license_obj.pk}@invalid.local"
        license_obj.save(update_fields=["serial_number", "registered_email"])


class Migration(migrations.Migration):
    dependencies = [
        ("gst_tally", "0002_license_userprofile_deviceactivation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="license",
            name="serial_number",
            field=models.CharField(max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="license",
            name="registered_email",
            field=models.EmailField(max_length=254, null=True),
        ),
        migrations.AddField(
            model_name="license",
            name="is_activated",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="license",
            name="activated_user",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="activated_license", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="license",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="license",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="deviceactivation",
            name="device_name",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.RunPython(populate_legacy_licenses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="license",
            name="serial_number",
            field=models.CharField(max_length=32, unique=True),
        ),
        migrations.AlterField(
            model_name="license",
            name="registered_email",
            field=models.EmailField(max_length=254),
        ),
        migrations.AlterField(
            model_name="license",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
