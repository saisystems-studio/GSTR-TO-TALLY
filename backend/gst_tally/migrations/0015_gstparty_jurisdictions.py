from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0014_gstparty_retry_not_before")]
    operations = [
        migrations.AddField(
            model_name="gstparty", name="central_jurisdiction",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="gstparty", name="state_jurisdiction",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
