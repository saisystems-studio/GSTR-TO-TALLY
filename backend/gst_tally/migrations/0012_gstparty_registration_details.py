from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0011_gstparty_lookup_error")]
    operations = [
        migrations.AddField(
            model_name="gstparty",
            name="registration_date",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="gstparty",
            name="cancellation_date",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="gstparty",
            name="constitution_of_business",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
