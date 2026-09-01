from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0004_gstparty_remove_gstimportrecord_batch_and_more")]
    operations = [
        migrations.AddField(model_name="gstparty", name="verification_source", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name="gstparty", name="verification_status", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="gstparty", name="verified_at", field=models.DateTimeField(blank=True, null=True)),
    ]
