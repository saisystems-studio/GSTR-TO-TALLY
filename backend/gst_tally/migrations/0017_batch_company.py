from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0016_tally_integration")]
    operations = [
        migrations.AddField(model_name="gstimportbatch", name="company_gstin", field=models.CharField(blank=True, max_length=15)),
        migrations.AddField(model_name="gstimportbatch", name="company_gstin_candidates", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="gstimportbatch", name="company_details", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="gstimportbatch", name="company_resolution_status", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="gstimportbatch", name="company_resolution_error", field=models.CharField(blank=True, max_length=100)),
    ]
