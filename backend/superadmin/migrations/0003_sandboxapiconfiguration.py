from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("superadmin", "0002_superadminsettings_current_app_version")]

    operations = [migrations.CreateModel(
        name="SandboxAPIConfiguration",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("provider", models.CharField(default="sandbox", max_length=30)),
            ("environment", models.CharField(default="test", max_length=30)),
            ("api_key_encrypted", models.TextField()),
            ("api_secret_encrypted", models.TextField()),
            ("api_version", models.CharField(default="1.0.0", max_length=30)),
            ("is_active", models.BooleanField(default=True)),
            ("last_verified_at", models.DateTimeField(blank=True, null=True)),
            ("last_error", models.CharField(blank=True, max_length=255)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sandbox_configurations_created", to=settings.AUTH_USER_MODEL)),
            ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sandbox_configurations_updated", to=settings.AUTH_USER_MODEL)),
        ],
        options={"constraints": [models.UniqueConstraint(fields=("provider", "environment"), name="sandbox_provider_environment_unique")]},
    )]
