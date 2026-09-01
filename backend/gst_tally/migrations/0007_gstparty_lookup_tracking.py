from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0006_gstparty_principal_place_of_business")]
    operations = [
        migrations.AddField(model_name="gstparty", name="lookup_source", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="gstparty", name="lookup_status", field=models.CharField(blank=True, max_length=30)),
    ]
