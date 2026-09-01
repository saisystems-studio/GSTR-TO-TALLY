from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gst_tally", "0007_gstparty_lookup_tracking"),
    ]

    operations = [
        migrations.AlterField(
            model_name="gstparty",
            name="lookup_status",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
