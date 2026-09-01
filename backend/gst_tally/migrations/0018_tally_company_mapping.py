from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0017_batch_company")]
    operations = [migrations.CreateModel(name="TallyCompanyMapping", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("gstin", models.CharField(max_length=15, unique=True)), ("tally_company_name", models.CharField(max_length=255)),
        ("company_details", models.JSONField(blank=True, default=dict)), ("status", models.CharField(max_length=30)),
        ("raw_response", models.TextField(blank=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
        ("updated_at", models.DateTimeField(auto_now=True)),
    ], options={"db_table": "tally_company_mapping_tbl"})]
