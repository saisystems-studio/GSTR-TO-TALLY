from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gst_tally", "0010_repair_source_parties_schema")]
    operations = [
        migrations.AddField(
            model_name="gstparty",
            name="lookup_error",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
