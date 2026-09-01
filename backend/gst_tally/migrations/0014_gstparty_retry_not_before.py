from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0013_gstparty_party_data_status")]
    operations = [migrations.AddField(
        model_name="gstparty", name="retry_not_before",
        field=models.DateTimeField(blank=True, null=True),
    )]
