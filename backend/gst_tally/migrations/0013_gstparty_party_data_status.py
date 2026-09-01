from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0012_gstparty_registration_details")]
    operations = [
        migrations.AddField(
            model_name="gstparty",
            name="party_data_status",
            field=models.CharField(blank=True, max_length=20),
        ),
    ]
